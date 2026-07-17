"""SQLite implementation of the persistence-neutral Source Inventory.

Python standard library only (``sqlite3``) — no ORM, no new dependency.
Every query is parameterized; this module never interpolates a locator,
filter value, or any other caller-supplied string into SQL text. Table
and column names are fixed literals from this module only. A normalized
relationship-table design is used throughout (``source_domains``,
``source_certifications``, ``source_courses``, ``source_topics``) rather
than comma-separated strings.

Datetimes are stored as one documented representation: UTC ISO-8601 text
(``datetime.astimezone(UTC).isoformat()``). No SQL trigger, default, or
generated column ever calls a nondeterministic time function — every
timestamp written here comes from an explicit ``AwareDatetime`` already
present on the domain object being persisted; the only exception is
``schema_migrations.applied_at``, a one-time infrastructure audit record
that is not a domain value.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self
from uuid import UUID

from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import (
    SourceApprovalStatus,
    SourceAuthorityLevel,
    SourceInventoryProcessingStatus,
    SourceInventoryRecord,
    SourceLocation,
    SourceLocatorKind,
    SourceMediaType,
    SourceRightsStatus,
    SourceVersion,
)
from personal_lms.source_inventory.errors import (
    SourceAlreadyExistsError,
    SourceInventoryContractError,
    SourceInventoryStorageError,
    SourceLocationConflictError,
    SourceNotFoundError,
    SourceVersionAlreadyExistsError,
)
from personal_lms.source_inventory.protocol import SourceInventoryFilter

_SCHEMA_VERSION = 1

_TAG_TABLES: dict[str, str] = {
    "knowledge_domains": "source_domains",
    "certifications": "source_certifications",
    "courses": "source_courses",
    "topics": "source_topics",
}

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS sources (
        source_id TEXT PRIMARY KEY,
        locator_kind TEXT NOT NULL,
        locator TEXT NOT NULL,
        canonical_locator TEXT NOT NULL,
        media_type TEXT NOT NULL,
        title TEXT,
        description TEXT,
        mime_type TEXT,
        language TEXT,
        content_hash_sha256 TEXT,
        size_bytes INTEGER,
        processing_status TEXT NOT NULL,
        approval_status TEXT NOT NULL,
        rights_status TEXT NOT NULL,
        authority_level TEXT NOT NULL,
        privacy_classification TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_locator "
    "ON sources(locator_kind, canonical_locator)",
    "CREATE INDEX IF NOT EXISTS idx_sources_processing_status ON sources(processing_status)",
    "CREATE INDEX IF NOT EXISTS idx_sources_approval_status ON sources(approval_status)",
    "CREATE INDEX IF NOT EXISTS idx_sources_privacy ON sources(privacy_classification)",
    "CREATE INDEX IF NOT EXISTS idx_sources_media_type ON sources(media_type)",
    """
    CREATE TABLE IF NOT EXISTS source_versions (
        version_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        content_hash_sha256 TEXT NOT NULL,
        size_bytes INTEGER,
        observed_at TEXT NOT NULL,
        supersedes_version_id TEXT REFERENCES source_versions(version_id),
        metadata_json TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_versions_source_hash "
    "ON source_versions(source_id, content_hash_sha256)",
    "CREATE INDEX IF NOT EXISTS idx_versions_source_id ON source_versions(source_id)",
    """
    CREATE TABLE IF NOT EXISTS source_locations (
        location_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        locator_kind TEXT NOT NULL,
        locator TEXT NOT NULL,
        canonical_locator TEXT NOT NULL,
        first_observed_at TEXT NOT NULL,
        last_observed_at TEXT NOT NULL,
        is_active INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_locations_source_id ON source_locations(source_id)",
    """
    CREATE TABLE IF NOT EXISTS source_domains (
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        value TEXT NOT NULL,
        PRIMARY KEY (source_id, value)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_certifications (
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        value TEXT NOT NULL,
        PRIMARY KEY (source_id, value)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_courses (
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        value TEXT NOT NULL,
        PRIMARY KEY (source_id, value)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_topics (
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        value TEXT NOT NULL,
        PRIMARY KEY (source_id, value)
    )
    """,
)


def _dt_to_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _text_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _filter_clause(filters: SourceInventoryFilter | None) -> tuple[str, list[object]]:
    if filters is None:
        return "", []
    clauses: list[str] = []
    params: list[object] = []
    if filters.locator_kind is not None:
        clauses.append("s.locator_kind = ?")
        params.append(filters.locator_kind.value)
    if filters.media_type is not None:
        clauses.append("s.media_type = ?")
        params.append(filters.media_type.value)
    if filters.processing_status is not None:
        clauses.append("s.processing_status = ?")
        params.append(filters.processing_status.value)
    if filters.approval_status is not None:
        clauses.append("s.approval_status = ?")
        params.append(filters.approval_status.value)
    if filters.privacy_classification is not None:
        clauses.append("s.privacy_classification = ?")
        params.append(filters.privacy_classification.value)
    if filters.knowledge_domain is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM source_domains d "
            "WHERE d.source_id = s.source_id AND d.value = ?)"
        )
        params.append(filters.knowledge_domain)
    if filters.certification is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM source_certifications c "
            "WHERE c.source_id = s.source_id AND c.value = ?)"
        )
        params.append(filters.certification)
    if filters.course is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM source_courses c "
            "WHERE c.source_id = s.source_id AND c.value = ?)"
        )
        params.append(filters.course)
    if filters.topic is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM source_topics t WHERE t.source_id = s.source_id AND t.value = ?)"
        )
        params.append(filters.topic)
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


class SQLiteSourceInventory:
    """SQLite-backed ``SourceInventoryCatalog``. Structurally conforms to the protocol."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        # foreign_keys is a no-op while a transaction is pending, and
        # autocommit=False (see open()) means the connection is always
        # inside one — even immediately after commit(). Toggle to
        # autocommit=True only long enough to set the pragma (which then
        # persists for the connection's lifetime), then restore manual
        # transaction control for everything else.
        self._connection.autocommit = True
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.autocommit = False

    @classmethod
    def open(cls, database_path: str | Path) -> Self:
        """Open (creating if absent) the SQLite file at ``database_path``.

        Does not create any table — call ``initialize_schema()`` before
        use. ``database_path`` may be ``":memory:"`` for a private,
        process-local database. Never reads a database path from an
        environment variable — the caller always supplies it explicitly.

        ``autocommit=False`` (Python 3.12+) gives every ``with
        self._connection:`` block true transactional DDL — without it,
        ``sqlite3``'s legacy transaction handling implicitly commits
        before each ``CREATE TABLE``/``CREATE INDEX`` statement, which
        would silently defeat ``initialize_schema()``'s rollback-on-
        failure guarantee.
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
        """Idempotent, versioned schema migration.

        Safe to call repeatedly. A stored schema version newer than this
        code's ``_SCHEMA_VERSION`` fails safely with
        ``SourceInventoryContractError`` rather than silently proceeding
        against a schema this code does not understand. The whole
        migration runs in one transaction — a failure partway through
        rolls back entirely, leaving no partially-applied schema change.
        """
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        row = self._connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        current_version: int = row[0] if row is not None and row[0] is not None else 0

        if current_version > _SCHEMA_VERSION:
            raise SourceInventoryContractError(
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
            raise SourceInventoryStorageError("schema_migration_failed") from exc

    # --- sources -------------------------------------------------------------

    def add_source(self, source: SourceInventoryRecord) -> SourceInventoryRecord:
        if self._source_exists(source.source_id):
            raise SourceAlreadyExistsError(source.source_id)
        if self.find_by_locator(source.locator_kind, source.canonical_locator) is not None:
            raise SourceLocationConflictError(
                source.locator_kind.value, "canonical_locator already cataloged"
            )

        try:
            with self._connection:
                self._insert_source_row(source)
                self._replace_tags(source)
                initial_location = SourceLocation(
                    source_id=source.source_id,
                    locator_kind=source.locator_kind,
                    locator=source.locator,
                    first_observed_at=source.created_at,
                    last_observed_at=source.updated_at,
                    is_active=True,
                )
                self._insert_location_row(initial_location)
        except sqlite3.Error as exc:
            raise SourceInventoryStorageError("add_source_failed") from exc
        return source

    def get_source(self, source_id: UUID) -> SourceInventoryRecord:
        row = self._connection.execute(
            "SELECT * FROM sources WHERE source_id = ?", (str(source_id),)
        ).fetchone()
        if row is None:
            raise SourceNotFoundError(source_id)
        return self._row_to_record(row)

    def find_by_locator(
        self, locator_kind: SourceLocatorKind, canonical_locator: str
    ) -> SourceInventoryRecord | None:
        row = self._connection.execute(
            "SELECT * FROM sources WHERE locator_kind = ? AND canonical_locator = ?",
            (locator_kind.value, canonical_locator),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_sources(
        self, *, filters: SourceInventoryFilter | None = None
    ) -> tuple[SourceInventoryRecord, ...]:
        clause, params = _filter_clause(filters)
        rows = self._connection.execute(
            f"SELECT * FROM sources s WHERE 1=1{clause} ORDER BY source_id", params
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def update_source(self, source: SourceInventoryRecord) -> SourceInventoryRecord:
        existing = self.get_source(source.source_id)
        if source.created_at != existing.created_at:
            raise SourceInventoryContractError("created_at is immutable and must not change")

        try:
            with self._connection:
                self._connection.execute(
                    """
                    UPDATE sources SET
                        locator_kind = ?, locator = ?, canonical_locator = ?, media_type = ?,
                        title = ?, description = ?, mime_type = ?, language = ?,
                        content_hash_sha256 = ?, size_bytes = ?,
                        processing_status = ?, approval_status = ?, rights_status = ?,
                        authority_level = ?, privacy_classification = ?, updated_at = ?
                    WHERE source_id = ?
                    """,
                    (
                        source.locator_kind.value,
                        source.locator,
                        source.canonical_locator,
                        source.media_type.value,
                        source.title,
                        source.description,
                        source.mime_type,
                        source.language,
                        source.content_hash_sha256,
                        source.size_bytes,
                        source.processing_status.value,
                        source.approval_status.value,
                        source.rights_status.value,
                        source.authority_level.value,
                        source.privacy_classification.value,
                        _dt_to_text(source.updated_at),
                        str(source.source_id),
                    ),
                )
                self._replace_tags(source)

                locator_changed = (
                    source.locator_kind != existing.locator_kind
                    or source.canonical_locator != existing.canonical_locator
                )
                if locator_changed:
                    self._retire_active_locations(source.source_id, source.updated_at)
                    self._activate_or_insert_location(source)
        except sqlite3.Error as exc:
            raise SourceInventoryStorageError("update_source_failed") from exc
        return source

    # --- versions --------------------------------------------------------

    def add_version(self, version: SourceVersion) -> SourceVersion:
        if not self._source_exists(version.source_id):
            raise SourceNotFoundError(version.source_id)
        if self._version_exists(version.source_id, version.content_hash_sha256):
            raise SourceVersionAlreadyExistsError(version.source_id, version.content_hash_sha256)
        if version.supersedes_version_id is not None and not self._version_id_exists(
            version.supersedes_version_id
        ):
            raise SourceInventoryContractError(
                "supersedes_version_id does not reference an existing version"
            )

        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO source_versions
                        (version_id, source_id, content_hash_sha256, size_bytes,
                         observed_at, supersedes_version_id, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(version.version_id),
                        str(version.source_id),
                        version.content_hash_sha256,
                        version.size_bytes,
                        _dt_to_text(version.observed_at),
                        str(version.supersedes_version_id)
                        if version.supersedes_version_id
                        else None,
                        json.dumps(version.metadata_json),
                    ),
                )
        except sqlite3.Error as exc:
            raise SourceInventoryStorageError("add_version_failed") from exc
        return version

    def list_versions(self, source_id: UUID) -> tuple[SourceVersion, ...]:
        rows = self._connection.execute(
            "SELECT * FROM source_versions WHERE source_id = ? ORDER BY observed_at, version_id",
            (str(source_id),),
        ).fetchall()
        return tuple(self._row_to_version(row) for row in rows)

    # --- locations ---------------------------------------------------------

    def add_location(self, location: SourceLocation) -> SourceLocation:
        if not self._source_exists(location.source_id):
            raise SourceNotFoundError(location.source_id)
        exists = self._connection.execute(
            "SELECT 1 FROM source_locations WHERE location_id = ?", (str(location.location_id),)
        ).fetchone()
        if exists is not None:
            raise SourceLocationConflictError(
                location.locator_kind.value, "location_id already exists"
            )

        try:
            with self._connection:
                self._insert_location_row(location)
        except sqlite3.Error as exc:
            raise SourceInventoryStorageError("add_location_failed") from exc
        return location

    def list_locations(self, source_id: UUID) -> tuple[SourceLocation, ...]:
        rows = self._connection.execute(
            "SELECT * FROM source_locations WHERE source_id = ? "
            "ORDER BY first_observed_at, location_id",
            (str(source_id),),
        ).fetchall()
        return tuple(self._row_to_location(row) for row in rows)

    # --- internal helpers ----------------------------------------------------

    def _source_exists(self, source_id: UUID) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM sources WHERE source_id = ?", (str(source_id),)
        ).fetchone()
        return row is not None

    def _version_exists(self, source_id: UUID, content_hash_sha256: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM source_versions WHERE source_id = ? AND content_hash_sha256 = ?",
            (str(source_id), content_hash_sha256),
        ).fetchone()
        return row is not None

    def _version_id_exists(self, version_id: UUID) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM source_versions WHERE version_id = ?", (str(version_id),)
        ).fetchone()
        return row is not None

    def _insert_source_row(self, source: SourceInventoryRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO sources
                (source_id, locator_kind, locator, canonical_locator, media_type,
                 title, description, mime_type, language, content_hash_sha256, size_bytes,
                 processing_status, approval_status, rights_status, authority_level,
                 privacy_classification, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(source.source_id),
                source.locator_kind.value,
                source.locator,
                source.canonical_locator,
                source.media_type.value,
                source.title,
                source.description,
                source.mime_type,
                source.language,
                source.content_hash_sha256,
                source.size_bytes,
                source.processing_status.value,
                source.approval_status.value,
                source.rights_status.value,
                source.authority_level.value,
                source.privacy_classification.value,
                _dt_to_text(source.created_at),
                _dt_to_text(source.updated_at),
            ),
        )

    def _replace_tags(self, source: SourceInventoryRecord) -> None:
        for field_name, table in _TAG_TABLES.items():
            self._connection.execute(
                f"DELETE FROM {table} WHERE source_id = ?", (str(source.source_id),)
            )
            values: tuple[str, ...] = getattr(source, field_name)
            if values:
                self._connection.executemany(
                    f"INSERT INTO {table} (source_id, value) VALUES (?, ?)",
                    [(str(source.source_id), value) for value in values],
                )

    def _load_tags(self, source_id: UUID) -> dict[str, tuple[str, ...]]:
        loaded: dict[str, tuple[str, ...]] = {}
        for field_name, table in _TAG_TABLES.items():
            rows = self._connection.execute(
                f"SELECT value FROM {table} WHERE source_id = ? ORDER BY value", (str(source_id),)
            ).fetchall()
            loaded[field_name] = tuple(row["value"] for row in rows)
        return loaded

    def _insert_location_row(self, location: SourceLocation) -> None:
        self._connection.execute(
            """
            INSERT INTO source_locations
                (location_id, source_id, locator_kind, locator, canonical_locator,
                 first_observed_at, last_observed_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(location.location_id),
                str(location.source_id),
                location.locator_kind.value,
                location.locator,
                location.canonical_locator,
                _dt_to_text(location.first_observed_at),
                _dt_to_text(location.last_observed_at),
                int(location.is_active),
            ),
        )

    def _retire_active_locations(self, source_id: UUID, observed_at: datetime) -> None:
        self._connection.execute(
            "UPDATE source_locations SET is_active = 0, last_observed_at = ? "
            "WHERE source_id = ? AND is_active = 1",
            (_dt_to_text(observed_at), str(source_id)),
        )

    def _activate_or_insert_location(self, source: SourceInventoryRecord) -> None:
        row = self._connection.execute(
            "SELECT location_id FROM source_locations "
            "WHERE source_id = ? AND locator_kind = ? AND canonical_locator = ?",
            (str(source.source_id), source.locator_kind.value, source.canonical_locator),
        ).fetchone()
        if row is not None:
            self._connection.execute(
                "UPDATE source_locations SET is_active = 1, last_observed_at = ? "
                "WHERE location_id = ?",
                (_dt_to_text(source.updated_at), row["location_id"]),
            )
            return
        new_location = SourceLocation(
            source_id=source.source_id,
            locator_kind=source.locator_kind,
            locator=source.locator,
            first_observed_at=source.updated_at,
            last_observed_at=source.updated_at,
            is_active=True,
        )
        self._insert_location_row(new_location)

    def _row_to_record(self, row: sqlite3.Row) -> SourceInventoryRecord:
        source_id = UUID(row["source_id"])
        tags = self._load_tags(source_id)
        return SourceInventoryRecord(
            source_id=source_id,
            locator_kind=SourceLocatorKind(row["locator_kind"]),
            locator=row["locator"],
            media_type=SourceMediaType(row["media_type"]),
            title=row["title"],
            description=row["description"],
            mime_type=row["mime_type"],
            language=row["language"],
            content_hash_sha256=row["content_hash_sha256"],
            size_bytes=row["size_bytes"],
            processing_status=SourceInventoryProcessingStatus(row["processing_status"]),
            approval_status=SourceApprovalStatus(row["approval_status"]),
            rights_status=SourceRightsStatus(row["rights_status"]),
            authority_level=SourceAuthorityLevel(row["authority_level"]),
            privacy_classification=PrivacyClassification(row["privacy_classification"]),
            knowledge_domains=tags["knowledge_domains"],
            certifications=tags["certifications"],
            courses=tags["courses"],
            topics=tags["topics"],
            created_at=_text_to_dt(row["created_at"]),
            updated_at=_text_to_dt(row["updated_at"]),
        )

    def _row_to_version(self, row: sqlite3.Row) -> SourceVersion:
        return SourceVersion(
            version_id=UUID(row["version_id"]),
            source_id=UUID(row["source_id"]),
            content_hash_sha256=row["content_hash_sha256"],
            size_bytes=row["size_bytes"],
            observed_at=_text_to_dt(row["observed_at"]),
            supersedes_version_id=UUID(row["supersedes_version_id"])
            if row["supersedes_version_id"]
            else None,
            metadata_json=json.loads(row["metadata_json"]),
        )

    def _row_to_location(self, row: sqlite3.Row) -> SourceLocation:
        return SourceLocation(
            location_id=UUID(row["location_id"]),
            source_id=UUID(row["source_id"]),
            locator_kind=SourceLocatorKind(row["locator_kind"]),
            locator=row["locator"],
            first_observed_at=_text_to_dt(row["first_observed_at"]),
            last_observed_at=_text_to_dt(row["last_observed_at"]),
            is_active=bool(row["is_active"]),
        )
