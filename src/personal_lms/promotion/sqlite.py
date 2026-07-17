"""SQLite implementation of the persistence-neutral Promotion Repository.

Python standard library only (``sqlite3``) — no ORM, no new dependency.
Every query is parameterized; this module never interpolates caller
input into SQL text. Mirrors
``personal_lms.source_inventory.sqlite``/``personal_lms.extraction.sqlite``'s
established conventions: UTC ISO-8601 timestamp text, a versioned
idempotent migration, foreign keys enabled, and every timestamp supplied
explicitly by the caller rather than read from the system clock (the sole
exception being ``schema_migrations.applied_at``).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self
from uuid import UUID

from personal_lms.domain.enums import SourceType
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.promotion import (
    PromotionBlocker,
    PromotionCandidate,
    PromotionDecision,
    PromotionDecisionOutcome,
    PromotionEligibility,
    PromotionEvent,
    PromotionExecution,
    PromotionExecutionState,
    PromotionMapping,
)
from personal_lms.promotion.errors import (
    PromotionCandidateNotFoundError,
    PromotionMappingConflictError,
    PromotionRepositoryContractError,
    PromotionRepositoryStorageError,
)

_SCHEMA_VERSION = 1

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS promotion_candidates (
        candidate_id TEXT PRIMARY KEY,
        inventory_source_id TEXT NOT NULL,
        source_version_id TEXT NOT NULL,
        extracted_artifact_id TEXT NOT NULL,
        proposed_catalog_source_id TEXT NOT NULL,
        proposed_title TEXT NOT NULL,
        proposed_source_type TEXT NOT NULL,
        proposed_privacy_classification TEXT NOT NULL,
        proposed_metadata_json TEXT NOT NULL,
        eligibility TEXT NOT NULL,
        blockers_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_candidates_inventory_source_id "
    "ON promotion_candidates(inventory_source_id)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_source_version_id "
    "ON promotion_candidates(source_version_id)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_extracted_artifact_id "
    "ON promotion_candidates(extracted_artifact_id)",
    """
    CREATE TABLE IF NOT EXISTS promotion_decisions (
        decision_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL REFERENCES promotion_candidates(candidate_id),
        outcome TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_decisions_candidate_id ON promotion_decisions(candidate_id)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_outcome ON promotion_decisions(outcome)",
    """
    CREATE TABLE IF NOT EXISTS promotion_mappings (
        inventory_source_id TEXT PRIMARY KEY,
        source_version_id TEXT NOT NULL,
        catalog_source_id TEXT NOT NULL,
        mapping_version INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_mappings_catalog_source_id "
    "ON promotion_mappings(catalog_source_id)",
    """
    CREATE TABLE IF NOT EXISTS promotion_executions (
        candidate_id TEXT PRIMARY KEY REFERENCES promotion_candidates(candidate_id),
        decision_id TEXT NOT NULL,
        catalog_source_id TEXT NOT NULL,
        execution_state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_executions_execution_state "
    "ON promotion_executions(execution_state)",
    "CREATE INDEX IF NOT EXISTS idx_executions_catalog_source_id "
    "ON promotion_executions(catalog_source_id)",
    """
    CREATE TABLE IF NOT EXISTS promotion_events (
        event_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL REFERENCES promotion_candidates(candidate_id),
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        detail_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_candidate_id ON promotion_events(candidate_id)",
)

_CANDIDATE_IDENTITY_FIELDS = (
    "inventory_source_id",
    "source_version_id",
    "extracted_artifact_id",
)


def _dt_to_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _text_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SQLitePromotionRepository:
    """SQLite-backed ``PromotionRepository``. Structurally conforms to the protocol."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._connection.autocommit = True
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.autocommit = False

    @classmethod
    def open(cls, database_path: str | Path) -> Self:
        """Open (creating if absent) the SQLite file at ``database_path``.

        Does not create any table — call ``initialize_schema()`` before
        use. ``database_path`` may be ``":memory:"``.
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
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        row = self._connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        current_version: int = row[0] if row is not None and row[0] is not None else 0

        if current_version > _SCHEMA_VERSION:
            raise PromotionRepositoryContractError(
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
            raise PromotionRepositoryStorageError("schema_migration_failed") from exc

    # --- candidates ------------------------------------------------------

    def create_candidate(self, candidate: PromotionCandidate) -> PromotionCandidate:
        row = self._connection.execute(
            "SELECT * FROM promotion_candidates WHERE candidate_id = ?",
            (str(candidate.candidate_id),),
        ).fetchone()
        if row is not None:
            existing = self._row_to_candidate(row)
            for field_name in _CANDIDATE_IDENTITY_FIELDS:
                if getattr(existing, field_name) != getattr(candidate, field_name):
                    raise PromotionRepositoryContractError(
                        "candidate_id_reused_with_different_identity"
                    )
            return existing

        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO promotion_candidates (
                        candidate_id, inventory_source_id, source_version_id,
                        extracted_artifact_id, proposed_catalog_source_id, proposed_title,
                        proposed_source_type, proposed_privacy_classification,
                        proposed_metadata_json, eligibility, blockers_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(candidate.candidate_id),
                        str(candidate.inventory_source_id),
                        str(candidate.source_version_id),
                        str(candidate.extracted_artifact_id),
                        candidate.proposed_catalog_source_id,
                        candidate.proposed_title,
                        candidate.proposed_source_type.value,
                        candidate.proposed_privacy_classification.value,
                        json.dumps(candidate.proposed_metadata),
                        candidate.eligibility.value,
                        json.dumps([b.value for b in candidate.blockers]),
                        _dt_to_text(candidate.created_at),
                    ),
                )
        except sqlite3.Error as exc:
            raise PromotionRepositoryStorageError("create_candidate_failed") from exc
        return candidate

    def get_candidate(self, candidate_id: UUID) -> PromotionCandidate:
        row = self._connection.execute(
            "SELECT * FROM promotion_candidates WHERE candidate_id = ?", (str(candidate_id),)
        ).fetchone()
        if row is None:
            raise PromotionCandidateNotFoundError(candidate_id)
        return self._row_to_candidate(row)

    def list_candidates_for_source(
        self, inventory_source_id: UUID
    ) -> tuple[PromotionCandidate, ...]:
        rows = self._connection.execute(
            "SELECT * FROM promotion_candidates WHERE inventory_source_id = ? "
            "ORDER BY created_at, candidate_id",
            (str(inventory_source_id),),
        ).fetchall()
        return tuple(self._row_to_candidate(row) for row in rows)

    # --- decisions ----------------------------------------------------------

    def record_decision(self, decision: PromotionDecision) -> PromotionDecision:
        self.get_candidate(decision.candidate_id)  # raises if unknown
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO promotion_decisions
                        (decision_id, candidate_id, outcome, reviewer, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(decision.decision_id),
                        str(decision.candidate_id),
                        decision.outcome.value,
                        decision.reviewer,
                        decision.reason,
                        _dt_to_text(decision.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise PromotionRepositoryContractError("decision_id_conflict") from exc
        except sqlite3.Error as exc:
            raise PromotionRepositoryStorageError("record_decision_failed") from exc
        return decision

    def get_latest_decision(self, candidate_id: UUID) -> PromotionDecision | None:
        row = self._connection.execute(
            "SELECT * FROM promotion_decisions WHERE candidate_id = ? "
            "ORDER BY created_at DESC, decision_id DESC LIMIT 1",
            (str(candidate_id),),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_decision(row)

    def list_decisions(self, candidate_id: UUID) -> tuple[PromotionDecision, ...]:
        rows = self._connection.execute(
            "SELECT * FROM promotion_decisions WHERE candidate_id = ? "
            "ORDER BY created_at, decision_id",
            (str(candidate_id),),
        ).fetchall()
        return tuple(self._row_to_decision(row) for row in rows)

    # --- mappings -------------------------------------------------------------

    def get_mapping(self, inventory_source_id: UUID) -> PromotionMapping | None:
        row = self._connection.execute(
            "SELECT * FROM promotion_mappings WHERE inventory_source_id = ?",
            (str(inventory_source_id),),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_mapping(row)

    def create_mapping(self, mapping: PromotionMapping) -> PromotionMapping:
        existing = self.get_mapping(mapping.inventory_source_id)
        if existing is not None and existing.catalog_source_id != mapping.catalog_source_id:
            raise PromotionMappingConflictError(
                mapping.inventory_source_id, "catalog_source_id_mismatch"
            )
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO promotion_mappings
                        (inventory_source_id, source_version_id, catalog_source_id,
                         mapping_version, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(inventory_source_id) DO UPDATE SET
                        source_version_id = excluded.source_version_id,
                        mapping_version = excluded.mapping_version
                    """,
                    (
                        str(mapping.inventory_source_id),
                        str(mapping.source_version_id),
                        mapping.catalog_source_id,
                        mapping.mapping_version,
                        _dt_to_text(mapping.created_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise PromotionMappingConflictError(
                mapping.inventory_source_id, "catalog_source_id_conflict"
            ) from exc
        except sqlite3.Error as exc:
            raise PromotionRepositoryStorageError("create_mapping_failed") from exc
        result = self.get_mapping(mapping.inventory_source_id)
        assert result is not None  # just written above
        return result

    # --- executions ----------------------------------------------------------

    def get_execution(self, candidate_id: UUID) -> PromotionExecution | None:
        row = self._connection.execute(
            "SELECT * FROM promotion_executions WHERE candidate_id = ?", (str(candidate_id),)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_execution(row)

    def save_execution(self, execution: PromotionExecution) -> PromotionExecution:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO promotion_executions
                        (candidate_id, decision_id, catalog_source_id, execution_state,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(candidate_id) DO UPDATE SET
                        decision_id = excluded.decision_id,
                        catalog_source_id = excluded.catalog_source_id,
                        execution_state = excluded.execution_state,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(execution.candidate_id),
                        str(execution.decision_id),
                        execution.catalog_source_id,
                        execution.execution_state.value,
                        _dt_to_text(execution.created_at),
                        _dt_to_text(execution.updated_at),
                    ),
                )
        except sqlite3.Error as exc:
            raise PromotionRepositoryStorageError("save_execution_failed") from exc
        result = self.get_execution(execution.candidate_id)
        assert result is not None  # just written above
        return result

    def has_completed_promotion_for_source_version(
        self,
        inventory_source_id: UUID,
        source_version_id: UUID,
        *,
        exclude_candidate_id: UUID | None = None,
    ) -> bool:
        params: list[object] = [
            str(inventory_source_id),
            str(source_version_id),
            PromotionExecutionState.COMPLETED.value,
        ]
        clause = ""
        if exclude_candidate_id is not None:
            clause = " AND c.candidate_id != ?"
            params.append(str(exclude_candidate_id))
        row = self._connection.execute(
            f"""
            SELECT 1 FROM promotion_candidates c
            JOIN promotion_executions e ON e.candidate_id = c.candidate_id
            WHERE c.inventory_source_id = ? AND c.source_version_id = ?
                AND e.execution_state = ?{clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
        return row is not None

    # --- events -----------------------------------------------------------------

    def record_event(self, event: PromotionEvent) -> PromotionEvent:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO promotion_events
                        (event_id, candidate_id, event_type, occurred_at, detail_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(event.event_id),
                        str(event.candidate_id),
                        event.event_type,
                        _dt_to_text(event.occurred_at),
                        json.dumps(event.detail),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise PromotionRepositoryContractError("event_for_unknown_candidate") from exc
        except sqlite3.Error as exc:
            raise PromotionRepositoryStorageError("record_event_failed") from exc
        return event

    def list_events(self, candidate_id: UUID) -> tuple[PromotionEvent, ...]:
        rows = self._connection.execute(
            "SELECT * FROM promotion_events WHERE candidate_id = ? ORDER BY occurred_at, event_id",
            (str(candidate_id),),
        ).fetchall()
        return tuple(self._row_to_event(row) for row in rows)

    # --- internal helpers ----------------------------------------------------

    def _row_to_candidate(self, row: sqlite3.Row) -> PromotionCandidate:
        return PromotionCandidate(
            candidate_id=UUID(row["candidate_id"]),
            inventory_source_id=UUID(row["inventory_source_id"]),
            source_version_id=UUID(row["source_version_id"]),
            extracted_artifact_id=UUID(row["extracted_artifact_id"]),
            proposed_catalog_source_id=row["proposed_catalog_source_id"],
            proposed_title=row["proposed_title"],
            proposed_source_type=SourceType(row["proposed_source_type"]),
            proposed_privacy_classification=PrivacyClassification(
                row["proposed_privacy_classification"]
            ),
            proposed_metadata=json.loads(row["proposed_metadata_json"]),
            eligibility=PromotionEligibility(row["eligibility"]),
            blockers=tuple(PromotionBlocker(b) for b in json.loads(row["blockers_json"])),
            created_at=_text_to_dt(row["created_at"]),
        )

    def _row_to_decision(self, row: sqlite3.Row) -> PromotionDecision:
        return PromotionDecision(
            decision_id=UUID(row["decision_id"]),
            candidate_id=UUID(row["candidate_id"]),
            outcome=PromotionDecisionOutcome(row["outcome"]),
            reviewer=row["reviewer"],
            reason=row["reason"],
            created_at=_text_to_dt(row["created_at"]),
        )

    def _row_to_mapping(self, row: sqlite3.Row) -> PromotionMapping:
        return PromotionMapping(
            inventory_source_id=UUID(row["inventory_source_id"]),
            source_version_id=UUID(row["source_version_id"]),
            catalog_source_id=row["catalog_source_id"],
            mapping_version=row["mapping_version"],
            created_at=_text_to_dt(row["created_at"]),
        )

    def _row_to_execution(self, row: sqlite3.Row) -> PromotionExecution:
        return PromotionExecution(
            candidate_id=UUID(row["candidate_id"]),
            decision_id=UUID(row["decision_id"]),
            catalog_source_id=row["catalog_source_id"],
            execution_state=PromotionExecutionState(row["execution_state"]),
            created_at=_text_to_dt(row["created_at"]),
            updated_at=_text_to_dt(row["updated_at"]),
        )

    def _row_to_event(self, row: sqlite3.Row) -> PromotionEvent:
        return PromotionEvent(
            event_id=UUID(row["event_id"]),
            candidate_id=UUID(row["candidate_id"]),
            event_type=row["event_type"],
            occurred_at=_text_to_dt(row["occurred_at"]),
            detail=json.loads(row["detail_json"]),
        )
