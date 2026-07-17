"""SQLite implementation of the persistence-neutral source catalog.

Python standard library only (``sqlite3``; JSON fidelity comes from
Pydantic's own ``model_dump_json``/``model_validate_json``, not the
``json`` module directly) — no new dependency. Every query is
parameterized; this module never interpolates a search term, filter
value, or any other caller-supplied string into SQL text. Table and
column names are fixed literals from this module only, never caller
input, so SQL-injection-shaped search or filter input is always treated
as inert data.

Search preserves exact terms (CLI commands, acronyms, interface names, IP
addresses, error messages) three ways:

1. The FTS5 virtual table is built with
   ``tokenize = 'unicode61 remove_diacritics 0 tokenchars ".:/-"'`` — no
   stemming (unlike the ``porter``/``snowball`` tokenizers, which would
   fold "routing" and "router" together and could mangle acronyms or CLI
   tokens), and ``.``, ``:``, ``/``, ``-`` count as part of a token rather
   than as separators. Without ``tokenchars``, "192.168.1.1" would
   tokenize into four separate numeric tokens indistinguishable from
   "192-168-1-1" or "192/168/1/1" — with it, each of an IPv4 address, an
   IPv6 address, CIDR notation, and an interface name like
   "GigabitEthernet0/0/1" is one atomic token, so different punctuation
   joining the same digits produces different, non-matching tokens. The
   tradeoff: a word with trailing punctuation glued to it with no space
   (e.g. "complete.") indexes as one token distinct from "complete" —
   acceptable here since exact technical-identifier matching is the
   priority this catalog is built for.
2. Every caller query is escaped and wrapped in FTS5 phrase syntax
   (embedded double quotes doubled) before being bound as the ``MATCH``
   parameter — never concatenated as raw FTS5 query-language syntax. This
   holds for both search modes; see ``SourceSearchMode``.
3. In ``SourceSearchMode.ALL_TERMS`` (the default), the query is split on
   whitespace and each resulting term is phrase-quoted individually, then
   the per-term phrases are joined with ``AND`` — every term must appear
   somewhere in a source's metadata, but terms need not be adjacent to
   each other. In ``SourceSearchMode.EXACT_PHRASE``, the entire query is
   wrapped as a single phrase, requiring every term in exact, adjacent
   order — unchanged from this module's original phrase-only behavior.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

from personal_lms.catalog.protocol import SourceSearchFilters, SourceSearchHit, SourceSearchMode
from personal_lms.domain.catalog import SourceAssetRelationship, SourceRecord

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS source_records (
        source_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL,
        status TEXT NOT NULL,
        privacy_classification TEXT NOT NULL,
        is_generated_artifact INTEGER NOT NULL,
        record_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_source_records_source_type ON source_records(source_type)",
    "CREATE INDEX IF NOT EXISTS idx_source_records_status ON source_records(status)",
    "CREATE INDEX IF NOT EXISTS idx_source_records_privacy "
    "ON source_records(privacy_classification)",
    """
    CREATE TABLE IF NOT EXISTS source_knowledge_scopes (
        source_id TEXT NOT NULL,
        knowledge_domain TEXT,
        certification TEXT,
        course TEXT,
        topic TEXT,
        objective_framework TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scopes_source_id ON source_knowledge_scopes(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_scopes_domain ON source_knowledge_scopes(knowledge_domain)",
    "CREATE INDEX IF NOT EXISTS idx_scopes_certification ON source_knowledge_scopes(certification)",
    "CREATE INDEX IF NOT EXISTS idx_scopes_course ON source_knowledge_scopes(course)",
    "CREATE INDEX IF NOT EXISTS idx_scopes_topic ON source_knowledge_scopes(topic)",
    "CREATE INDEX IF NOT EXISTS idx_scopes_objective_framework "
    "ON source_knowledge_scopes(objective_framework)",
    """
    CREATE TABLE IF NOT EXISTS source_relationships (
        relationship_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        related_source_id TEXT NOT NULL,
        relationship_type TEXT NOT NULL,
        record_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_relationships_source_id ON source_relationships(source_id)",
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
        source_id UNINDEXED,
        filename,
        original_location,
        mime_type,
        acquisition_note,
        knowledge_scope_text,
        tokenize = "unicode61 remove_diacritics 0 tokenchars '.:/-'"
    )
    """,
)

_KNOWLEDGE_SCOPE_FILTER_COLUMNS = (
    "knowledge_domain",
    "certification",
    "course",
    "topic",
    "objective_framework",
)


def _knowledge_scope_text(record: SourceRecord) -> str:
    values: list[str] = []
    for scope in record.knowledge_scopes:
        values.extend(
            value
            for value in (
                scope.knowledge_domain,
                scope.certification,
                scope.course,
                scope.topic,
                scope.objective_framework,
            )
            if value is not None
        )
    return " ".join(values)


def _escape_fts_phrase(query: str) -> str:
    """Wrap ``query`` as one FTS5 phrase, escaping embedded double quotes."""
    return '"' + query.replace('"', '""') + '"'


def _build_match_query(query: str, mode: SourceSearchMode) -> str | None:
    """The FTS5 ``MATCH`` expression for ``query`` under ``mode``.

    Returns ``None`` for a query with no searchable terms (empty or
    whitespace-only) — never SQL/FTS5 text built from raw caller input.
    Every term (the whole query in ``EXACT_PHRASE`` mode, or each
    whitespace-split term in ``ALL_TERMS`` mode) is escaped and
    phrase-quoted individually via ``_escape_fts_phrase`` before being
    combined, so caller input is never interpreted as FTS5 query-language
    syntax (AND/OR/NOT, column filters, ``*`` prefix matching) in either
    mode.
    """
    if mode is SourceSearchMode.EXACT_PHRASE:
        if not query.strip():
            return None
        return _escape_fts_phrase(query)

    terms = query.split()
    if not terms:
        return None
    return " AND ".join(_escape_fts_phrase(term) for term in terms)


def _filter_clause(
    filters: SourceSearchFilters | None, *, table_alias: str
) -> tuple[str, list[object]]:
    """A ``" AND ..."`` SQL fragment (or ``""``) plus its bound parameters."""
    if filters is None:
        return "", []

    clauses: list[str] = []
    params: list[object] = []

    if filters.source_type is not None:
        clauses.append(f"{table_alias}.source_type = ?")
        params.append(filters.source_type.value)
    if filters.status is not None:
        clauses.append(f"{table_alias}.status = ?")
        params.append(filters.status.value)
    if filters.privacy_classification is not None:
        clauses.append(f"{table_alias}.privacy_classification = ?")
        params.append(filters.privacy_classification.value)

    scope_values = (
        filters.knowledge_domain,
        filters.certification,
        filters.course,
        filters.topic,
        filters.objective_framework,
    )
    scope_conditions = [
        f"{column} = ?"
        for column, value in zip(_KNOWLEDGE_SCOPE_FILTER_COLUMNS, scope_values, strict=True)
        if value is not None
    ]
    scope_params = [value for value in scope_values if value is not None]

    if scope_conditions:
        scope_where = " AND ".join(scope_conditions)
        clauses.append(
            "EXISTS (SELECT 1 FROM source_knowledge_scopes sks "
            f"WHERE sks.source_id = {table_alias}.source_id AND {scope_where})"
        )
        params.extend(scope_params)

    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


class SQLiteSourceCatalog:
    """SQLite-backed ``SourceCatalog``. Structurally conforms to the protocol."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    @classmethod
    def open(cls, database_path: str | Path) -> Self:
        """Open (creating if absent) the SQLite file at ``database_path``.

        Does not create any table — call ``initialize_schema()`` before
        use. ``database_path`` may be ``":memory:"`` for a private,
        process-local database.
        """
        connection = sqlite3.connect(str(database_path))
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

    def initialize_schema(self) -> None:
        with self._connection:
            for statement in _SCHEMA_STATEMENTS:
                self._connection.execute(statement)

    def upsert_source(self, record: SourceRecord) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO source_records
                    (source_id, source_type, status, privacy_classification,
                     is_generated_artifact, record_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    source_type = excluded.source_type,
                    status = excluded.status,
                    privacy_classification = excluded.privacy_classification,
                    is_generated_artifact = excluded.is_generated_artifact,
                    record_json = excluded.record_json
                """,
                (
                    record.source_id,
                    record.source_type.value,
                    record.status.value,
                    record.privacy_classification.value,
                    int(record.is_generated_artifact),
                    record.model_dump_json(),
                ),
            )

            self._connection.execute(
                "DELETE FROM source_knowledge_scopes WHERE source_id = ?", (record.source_id,)
            )
            self._connection.executemany(
                """
                INSERT INTO source_knowledge_scopes
                    (source_id, knowledge_domain, certification, course, topic,
                     objective_framework)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.source_id,
                        scope.knowledge_domain,
                        scope.certification,
                        scope.course,
                        scope.topic,
                        scope.objective_framework,
                    )
                    for scope in record.knowledge_scopes
                ],
            )

            self._connection.execute(
                "DELETE FROM source_fts WHERE source_id = ?", (record.source_id,)
            )
            self._connection.execute(
                """
                INSERT INTO source_fts
                    (source_id, filename, original_location, mime_type,
                     acquisition_note, knowledge_scope_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.source_id,
                    record.filename,
                    record.original_location,
                    record.mime_type,
                    record.provenance.acquisition_note or "",
                    _knowledge_scope_text(record),
                ),
            )

    def get_source(self, source_id: str) -> SourceRecord | None:
        row = self._connection.execute(
            "SELECT record_json FROM source_records WHERE source_id = ?", (source_id,)
        ).fetchone()
        if row is None:
            return None
        return SourceRecord.model_validate_json(row["record_json"])

    def list_sources(
        self, *, filters: SourceSearchFilters | None = None
    ) -> tuple[SourceRecord, ...]:
        clause, params = _filter_clause(filters, table_alias="source_records")
        rows = self._connection.execute(
            f"SELECT record_json FROM source_records WHERE 1=1{clause} ORDER BY source_id",
            params,
        ).fetchall()
        return tuple(SourceRecord.model_validate_json(row["record_json"]) for row in rows)

    def add_relationship(self, relationship: SourceAssetRelationship) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO source_relationships
                    (relationship_id, source_id, related_source_id, relationship_type,
                     record_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(relationship_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    related_source_id = excluded.related_source_id,
                    relationship_type = excluded.relationship_type,
                    record_json = excluded.record_json
                """,
                (
                    str(relationship.relationship_id),
                    relationship.source_id,
                    relationship.related_source_id,
                    relationship.relationship_type.value,
                    relationship.model_dump_json(),
                ),
            )

    def list_relationships(self, source_id: str) -> tuple[SourceAssetRelationship, ...]:
        rows = self._connection.execute(
            "SELECT record_json FROM source_relationships "
            "WHERE source_id = ? OR related_source_id = ? ORDER BY relationship_id",
            (source_id, source_id),
        ).fetchall()
        return tuple(
            SourceAssetRelationship.model_validate_json(row["record_json"]) for row in rows
        )

    def search(
        self,
        query: str,
        *,
        mode: SourceSearchMode = SourceSearchMode.ALL_TERMS,
        filters: SourceSearchFilters | None = None,
        limit: int = 20,
    ) -> tuple[SourceSearchHit, ...]:
        fts_query = _build_match_query(query, mode)
        if fts_query is None:
            return ()

        clause, params = _filter_clause(filters, table_alias="sr")
        rows = self._connection.execute(
            f"""
            SELECT sr.record_json AS record_json,
                   fts.rank AS rank,
                   snippet(source_fts, -1, '[', ']', '...', 10) AS snippet
            FROM source_fts fts
            JOIN source_records sr ON sr.source_id = fts.source_id
            WHERE source_fts MATCH ?{clause}
            ORDER BY fts.rank
            LIMIT ?
            """,
            (fts_query, *params, limit),
        ).fetchall()

        hits: list[SourceSearchHit] = []
        for row in rows:
            record = SourceRecord.model_validate_json(row["record_json"])
            hits.append(
                SourceSearchHit(
                    source_id=record.source_id,
                    record=record,
                    score=-row["rank"],
                    snippet=row["snippet"],
                )
            )
        return tuple(hits)
