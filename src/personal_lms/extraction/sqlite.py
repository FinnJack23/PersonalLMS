"""SQLite implementation of the persistence-neutral Extraction Queue.

Python standard library only (``sqlite3``) — no ORM, no new dependency.
Every query is parameterized; this module never interpolates caller
input into SQL text. Table and column names are fixed literals from this
module only.

Datetimes are stored as UTC ISO-8601 text
(``datetime.astimezone(UTC).isoformat()``), matching
``personal_lms.source_inventory.sqlite``'s convention. Every timestamp
written here comes from an explicit ``now`` parameter the caller supplies
— the only exceptions are ``schema_migrations.applied_at`` and generated
row identifiers (``event_id``), neither of which is a domain value.

Atomic claim semantics (``claim_next``): a single ``UPDATE ... WHERE
job_id = (SELECT ... WHERE status = 'pending' ORDER BY ... LIMIT 1)
RETURNING job_id`` statement selects *and* claims the next candidate job
in one atomic write — never a separate "read, decide, then write" pair of
statements. Two statements would let a connection's read take a SHARED
lock that a second connection also holds, so when either later tries to
*upgrade* to a write lock, SQLite can raise ``SQLITE_BUSY`` ("database is
locked") immediately, bypassing ``PRAGMA busy_timeout``'s retry-and-wait
behavior entirely — confirmed empirically against this codebase's
established ``autocommit=False`` connection convention, under which a
raw, manually-issued ``BEGIN IMMEDIATE`` is also rejected outright
("cannot start a transaction within a transaction"), so that classic
workaround does not apply here either. A single ``UPDATE ... RETURNING``
statement sidesteps both problems: it is one implicit transaction from
the start, so the two connections' write attempts are serialized
cleanly, and ``PRAGMA busy_timeout`` (set on every connection) lets the
second one wait for the first's commit rather than erroring. This gives
**at-most-one successful claim per job under this SQLite transaction
boundary** — a guarantee about this database file and these connections,
not a distributed-lock or exactly-once guarantee across processes or
machines. See ``tests/unit/extraction/test_sqlite.py`` for the
two-connection concurrency test that demonstrates it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from personal_lms.domain.extraction import (
    ExtractedArtifact,
    ExtractionArtifactProvenance,
    ExtractionCapability,
    ExtractionJob,
    ExtractionJobStatus,
    ExtractionRequest,
    is_valid_job_transition,
)
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import SourceMediaType
from personal_lms.extraction.errors import (
    ExtractionArtifactNotFoundError,
    ExtractionJobNotFoundError,
    ExtractionQueueContractError,
    ExtractionQueueStorageError,
    InvalidExtractionJobTransitionError,
)
from personal_lms.extraction.protocol import ExtractionJobFilter

_SCHEMA_VERSION = 1

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS extraction_jobs (
        job_id TEXT PRIMARY KEY,
        inventory_source_id TEXT NOT NULL,
        source_version_id TEXT NOT NULL,
        requested_capability TEXT NOT NULL,
        media_kind TEXT NOT NULL,
        privacy_classification TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL,
        priority INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        claimed_at TEXT,
        started_at TEXT,
        completed_at TEXT,
        failed_at TEXT,
        worker_id TEXT,
        idempotency_key TEXT NOT NULL,
        last_error_code TEXT,
        last_error_message TEXT
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency_key "
    "ON extraction_jobs(idempotency_key)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON extraction_jobs(status)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_priority ON extraction_jobs(priority)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_inventory_source_id "
    "ON extraction_jobs(inventory_source_id)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_source_version_id ON extraction_jobs(source_version_id)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_claim_order "
    "ON extraction_jobs(status, priority, created_at, job_id)",
    """
    CREATE TABLE IF NOT EXISTS extracted_artifacts (
        artifact_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES extraction_jobs(job_id),
        inventory_source_id TEXT NOT NULL,
        source_version_id TEXT NOT NULL,
        artifact_kind TEXT NOT NULL,
        content_locator TEXT NOT NULL,
        content_hash TEXT,
        content_size_bytes INTEGER,
        mime_type TEXT,
        language TEXT,
        page_count INTEGER,
        duration_seconds REAL,
        created_at TEXT NOT NULL,
        extractor_name TEXT NOT NULL,
        extractor_version TEXT NOT NULL,
        extraction_warnings TEXT NOT NULL,
        extracted_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_artifacts_job_id ON extracted_artifacts(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_inventory_source_id "
    "ON extracted_artifacts(inventory_source_id)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_source_version_id "
    "ON extracted_artifacts(source_version_id)",
    """
    CREATE TABLE IF NOT EXISTS extraction_job_events (
        event_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES extraction_jobs(job_id),
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        detail_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON extraction_job_events(job_id)",
)

# Fields identifying "the same logical enqueue request" — must match for a
# repeated idempotency_key to be treated as the same job (see ``enqueue``).
_IDENTITY_FIELDS = (
    "inventory_source_id",
    "source_version_id",
    "requested_capability",
    "media_kind",
)


def _dt_to_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _text_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _filter_clause(filters: ExtractionJobFilter | None) -> tuple[str, list[object]]:
    if filters is None:
        return "", []
    clauses: list[str] = []
    params: list[object] = []
    if filters.status is not None:
        clauses.append("status = ?")
        params.append(filters.status.value)
    if filters.inventory_source_id is not None:
        clauses.append("inventory_source_id = ?")
        params.append(str(filters.inventory_source_id))
    if filters.source_version_id is not None:
        clauses.append("source_version_id = ?")
        params.append(str(filters.source_version_id))
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


class SQLiteExtractionQueue:
    """SQLite-backed ``ExtractionQueue``. Structurally conforms to the protocol."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        # See personal_lms.source_inventory.sqlite.SQLiteSourceInventory.__init__
        # for why the pragmas are set in a brief autocommit=True window.
        self._connection.autocommit = True
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.autocommit = False

    @classmethod
    def open(cls, database_path: str | Path) -> Self:
        """Open (creating if absent) the SQLite file at ``database_path``.

        Does not create any table — call ``initialize_schema()`` before
        use. ``database_path`` may be ``":memory:"`` for a private,
        process-local database, but the atomic-claim concurrency guarantee
        requires a real file shared by two connections — see the module
        docstring.
        """
        connection = sqlite3.connect(str(database_path), autocommit=False)
        return cls(connection)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    # --- schema / migration -----------------------------------------------

    def initialize_schema(self) -> None:
        """Idempotent, versioned schema migration — see
        ``personal_lms.source_inventory.sqlite.SQLiteSourceInventory.initialize_schema``
        for the identical pattern this mirrors."""
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        row = self._connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        current_version: int = row[0] if row is not None and row[0] is not None else 0

        if current_version > _SCHEMA_VERSION:
            raise ExtractionQueueContractError(
                f"unsupported_schema_version: found {current_version}, "
                f"this code supports up to {_SCHEMA_VERSION}"
            )
        if current_version == _SCHEMA_VERSION:
            return

        try:
            with self._connection:
                for statement in _SCHEMA_STATEMENTS:
                    self._connection.execute(statement)
                self._connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (_SCHEMA_VERSION, _dt_to_text(datetime.now(UTC))),
                )
        except sqlite3.Error as exc:
            raise ExtractionQueueStorageError("schema_migration_failed") from exc

    # --- jobs ---------------------------------------------------------------

    def enqueue(self, request: ExtractionRequest, *, now: datetime) -> ExtractionJob:
        existing_row = self._connection.execute(
            "SELECT * FROM extraction_jobs WHERE idempotency_key = ?",
            (request.idempotency_key,),
        ).fetchone()
        if existing_row is not None:
            existing = self._row_to_job(existing_row)
            for field_name in _IDENTITY_FIELDS:
                if getattr(existing, field_name) != getattr(request, field_name):
                    raise ExtractionQueueContractError(
                        "idempotency_key_reused_with_different_request_identity"
                    )
            return existing

        job = ExtractionJob(
            inventory_source_id=request.inventory_source_id,
            source_version_id=request.source_version_id,
            requested_capability=request.requested_capability,
            media_kind=request.media_kind,
            privacy_classification=request.privacy_classification,
            priority=request.priority,
            idempotency_key=request.idempotency_key,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._connection:
                self._upsert_job_row(job)
                self._insert_event(job.job_id, "enqueued", now, {})
        except sqlite3.IntegrityError as exc:
            raise ExtractionQueueContractError("idempotency_key_conflict") from exc
        except sqlite3.Error as exc:
            raise ExtractionQueueStorageError("enqueue_failed") from exc
        return job

    def get(self, job_id: UUID) -> ExtractionJob:
        row = self._connection.execute(
            "SELECT * FROM extraction_jobs WHERE job_id = ?", (str(job_id),)
        ).fetchone()
        if row is None:
            raise ExtractionJobNotFoundError(job_id)
        return self._row_to_job(row)

    def claim_next(self, *, worker_id: str, now: datetime) -> ExtractionJob | None:
        if not worker_id:
            raise ValueError("worker_id must not be empty")

        try:
            with self._connection:
                row = self._connection.execute(
                    """
                    UPDATE extraction_jobs
                    SET status = ?, attempt_count = attempt_count + 1, claimed_at = ?,
                        updated_at = ?, worker_id = ?
                    WHERE job_id = (
                        SELECT job_id FROM extraction_jobs WHERE status = ?
                        ORDER BY priority ASC, created_at ASC, job_id ASC LIMIT 1
                    )
                    RETURNING *
                    """,
                    (
                        ExtractionJobStatus.CLAIMED.value,
                        _dt_to_text(now),
                        _dt_to_text(now),
                        worker_id,
                        ExtractionJobStatus.PENDING.value,
                    ),
                ).fetchone()
                if row is None:
                    return None
                claimed = self._row_to_job(row)
                self._insert_event(claimed.job_id, "claimed", now, {"worker_id": worker_id})
        except sqlite3.Error as exc:
            raise ExtractionQueueStorageError("claim_failed") from exc
        return claimed

    def mark_running(self, job_id: UUID, *, worker_id: str, now: datetime) -> ExtractionJob:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        job = self.get(job_id)
        if job.worker_id != worker_id:
            raise ExtractionQueueContractError("worker_id_does_not_match_claiming_worker")
        return self._transition(
            job,
            ExtractionJobStatus.RUNNING,
            now=now,
            extra_fields={"started_at": now},
            event_type="running",
            event_detail={"worker_id": worker_id},
        )

    def record_success(
        self, job_id: UUID, artifact: ExtractedArtifact, *, now: datetime
    ) -> ExtractionJob:
        job = self.get(job_id)
        if job.status is ExtractionJobStatus.SUCCEEDED:
            existing = self.list_artifacts_for_job(job_id)
            if any(a.artifact_id == artifact.artifact_id for a in existing):
                return job
            raise ExtractionQueueContractError("job_already_succeeded_with_a_different_artifact")
        return self._transition(
            job,
            ExtractionJobStatus.SUCCEEDED,
            now=now,
            extra_fields={"completed_at": now},
            event_type="succeeded",
            event_detail={"artifact_id": str(artifact.artifact_id)},
            artifact=artifact,
        )

    def record_retryable_failure(
        self, job_id: UUID, *, error_code: str, error_message: str, now: datetime
    ) -> ExtractionJob:
        job = self.get(job_id)
        if job.status is ExtractionJobStatus.FAILED_RETRYABLE:
            if job.last_error_code == error_code and job.last_error_message == error_message:
                return job
            raise ExtractionQueueContractError(
                "job_already_failed_retryable_with_a_different_error"
            )
        return self._transition(
            job,
            ExtractionJobStatus.FAILED_RETRYABLE,
            now=now,
            extra_fields={
                "failed_at": now,
                "last_error_code": error_code,
                "last_error_message": error_message,
            },
            event_type="failed_retryable",
            event_detail={"error_code": error_code},
        )

    def record_terminal_failure(
        self, job_id: UUID, *, error_code: str, error_message: str, now: datetime
    ) -> ExtractionJob:
        job = self.get(job_id)
        if job.status is ExtractionJobStatus.FAILED_TERMINAL:
            if job.last_error_code == error_code and job.last_error_message == error_message:
                return job
            raise ExtractionQueueContractError("job_already_failed_terminal_with_a_different_error")
        return self._transition(
            job,
            ExtractionJobStatus.FAILED_TERMINAL,
            now=now,
            extra_fields={
                "failed_at": now,
                "last_error_code": error_code,
                "last_error_message": error_message,
            },
            event_type="failed_terminal",
            event_detail={"error_code": error_code},
        )

    def requeue(self, job_id: UUID, *, now: datetime) -> ExtractionJob:
        job = self.get(job_id)
        return self._transition(
            job,
            ExtractionJobStatus.PENDING,
            now=now,
            extra_fields={},
            event_type="requeued",
            event_detail={},
        )

    def cancel(self, job_id: UUID, *, now: datetime) -> ExtractionJob:
        job = self.get(job_id)
        return self._transition(
            job,
            ExtractionJobStatus.CANCELLED,
            now=now,
            extra_fields={},
            event_type="cancelled",
            event_detail={},
        )

    def list_by_status(
        self, *, filters: ExtractionJobFilter | None = None
    ) -> tuple[ExtractionJob, ...]:
        clause, params = _filter_clause(filters)
        rows = self._connection.execute(
            f"SELECT * FROM extraction_jobs WHERE 1=1{clause} "
            "ORDER BY priority ASC, created_at ASC, job_id ASC",
            params,
        ).fetchall()
        return tuple(self._row_to_job(row) for row in rows)

    # --- artifacts -----------------------------------------------------------

    def get_artifact(self, artifact_id: UUID) -> ExtractedArtifact:
        row = self._connection.execute(
            "SELECT * FROM extracted_artifacts WHERE artifact_id = ?", (str(artifact_id),)
        ).fetchone()
        if row is None:
            raise ExtractionArtifactNotFoundError(artifact_id)
        return self._row_to_artifact(row)

    def list_artifacts_for_job(self, job_id: UUID) -> tuple[ExtractedArtifact, ...]:
        rows = self._connection.execute(
            "SELECT * FROM extracted_artifacts WHERE job_id = ? ORDER BY artifact_id",
            (str(job_id),),
        ).fetchall()
        return tuple(self._row_to_artifact(row) for row in rows)

    # --- internal helpers ----------------------------------------------------

    def _transition(
        self,
        job: ExtractionJob,
        target_status: ExtractionJobStatus,
        *,
        now: datetime,
        extra_fields: dict[str, object],
        event_type: str,
        event_detail: dict[str, object],
        artifact: ExtractedArtifact | None = None,
    ) -> ExtractionJob:
        if not is_valid_job_transition(job.status, target_status):
            raise InvalidExtractionJobTransitionError(job.job_id, job.status, target_status)
        update: dict[str, object] = {"status": target_status, "updated_at": now, **extra_fields}
        updated = job.model_copy(update=update)
        try:
            with self._connection:
                self._upsert_job_row(updated)
                if artifact is not None:
                    self._insert_artifact_row(artifact)
                self._insert_event(updated.job_id, event_type, now, event_detail)
        except sqlite3.Error as exc:
            raise ExtractionQueueStorageError(f"{event_type}_failed") from exc
        return updated

    def _upsert_job_row(self, job: ExtractionJob) -> None:
        self._connection.execute(
            """
            INSERT INTO extraction_jobs (
                job_id, inventory_source_id, source_version_id, requested_capability,
                media_kind, privacy_classification, status, attempt_count, priority,
                created_at, updated_at, claimed_at, started_at, completed_at, failed_at,
                worker_id, idempotency_key, last_error_code, last_error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                attempt_count = excluded.attempt_count,
                priority = excluded.priority,
                updated_at = excluded.updated_at,
                claimed_at = excluded.claimed_at,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at,
                failed_at = excluded.failed_at,
                worker_id = excluded.worker_id,
                last_error_code = excluded.last_error_code,
                last_error_message = excluded.last_error_message
            """,
            (
                str(job.job_id),
                str(job.inventory_source_id),
                str(job.source_version_id),
                job.requested_capability.value,
                job.media_kind.value,
                job.privacy_classification.value,
                job.status.value,
                job.attempt_count,
                job.priority,
                _dt_to_text(job.created_at),
                _dt_to_text(job.updated_at),
                _dt_to_text(job.claimed_at) if job.claimed_at else None,
                _dt_to_text(job.started_at) if job.started_at else None,
                _dt_to_text(job.completed_at) if job.completed_at else None,
                _dt_to_text(job.failed_at) if job.failed_at else None,
                job.worker_id,
                job.idempotency_key,
                job.last_error_code,
                job.last_error_message,
            ),
        )

    def _insert_artifact_row(self, artifact: ExtractedArtifact) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO extracted_artifacts (
                artifact_id, job_id, inventory_source_id, source_version_id, artifact_kind,
                content_locator, content_hash, content_size_bytes, mime_type, language,
                page_count, duration_seconds, created_at, extractor_name, extractor_version,
                extraction_warnings, extracted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(artifact.artifact_id),
                str(artifact.provenance.job_id),
                str(artifact.provenance.inventory_source_id),
                str(artifact.provenance.source_version_id),
                artifact.artifact_kind.value,
                artifact.content_locator,
                artifact.content_hash,
                artifact.content_size_bytes,
                artifact.mime_type,
                artifact.language,
                artifact.page_count,
                artifact.duration_seconds,
                _dt_to_text(artifact.created_at),
                artifact.provenance.extractor_name,
                artifact.provenance.extractor_version,
                json.dumps(list(artifact.extraction_warnings)),
                _dt_to_text(artifact.provenance.extracted_at),
            ),
        )

    def _insert_event(
        self, job_id: UUID, event_type: str, occurred_at: datetime, detail: dict[str, object]
    ) -> None:
        self._connection.execute(
            "INSERT INTO extraction_job_events "
            "(event_id, job_id, event_type, occurred_at, detail_json) VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), str(job_id), event_type, _dt_to_text(occurred_at), json.dumps(detail)),
        )

    def _row_to_job(self, row: sqlite3.Row) -> ExtractionJob:
        return ExtractionJob(
            job_id=UUID(row["job_id"]),
            inventory_source_id=UUID(row["inventory_source_id"]),
            source_version_id=UUID(row["source_version_id"]),
            requested_capability=ExtractionCapability(row["requested_capability"]),
            media_kind=SourceMediaType(row["media_kind"]),
            privacy_classification=PrivacyClassification(row["privacy_classification"]),
            status=ExtractionJobStatus(row["status"]),
            attempt_count=row["attempt_count"],
            priority=row["priority"],
            created_at=_text_to_dt(row["created_at"]),
            updated_at=_text_to_dt(row["updated_at"]),
            claimed_at=_text_to_dt(row["claimed_at"]) if row["claimed_at"] else None,
            started_at=_text_to_dt(row["started_at"]) if row["started_at"] else None,
            completed_at=_text_to_dt(row["completed_at"]) if row["completed_at"] else None,
            failed_at=_text_to_dt(row["failed_at"]) if row["failed_at"] else None,
            worker_id=row["worker_id"],
            idempotency_key=row["idempotency_key"],
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
        )

    def _row_to_artifact(self, row: sqlite3.Row) -> ExtractedArtifact:
        return ExtractedArtifact(
            artifact_id=UUID(row["artifact_id"]),
            artifact_kind=ExtractionCapability(row["artifact_kind"]),
            content_locator=row["content_locator"],
            content_hash=row["content_hash"],
            content_size_bytes=row["content_size_bytes"],
            mime_type=row["mime_type"],
            language=row["language"],
            page_count=row["page_count"],
            duration_seconds=row["duration_seconds"],
            created_at=_text_to_dt(row["created_at"]),
            extraction_warnings=tuple(json.loads(row["extraction_warnings"])),
            provenance=ExtractionArtifactProvenance(
                job_id=UUID(row["job_id"]),
                inventory_source_id=UUID(row["inventory_source_id"]),
                source_version_id=UUID(row["source_version_id"]),
                extractor_name=row["extractor_name"],
                extractor_version=row["extractor_version"],
                extracted_at=_text_to_dt(row["extracted_at"]),
            ),
        )
