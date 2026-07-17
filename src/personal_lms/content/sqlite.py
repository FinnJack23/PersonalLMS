"""SQLite implementation of the persistence-neutral content repository.

Python standard library only (``sqlite3``; JSON fidelity comes from
Pydantic's own ``model_dump_json``/``model_validate_json``) — no new
dependency. Every query is parameterized; this module never interpolates
a search term, filter value, or any other caller-supplied string into SQL
text. Table and column names are fixed literals from this module only,
never caller input, so SQL-injection-shaped search or filter input is
always treated as inert data.

Mirrors ``personal_lms.catalog.sqlite`` closely (deliberately duplicated
rather than shared, keeping this package independent):

- the FTS5 virtual table uses the same
  ``tokenize = 'unicode61 remove_diacritics 0 tokenchars ".:/-"'``
  configuration, for the same reason — IPv4/IPv6 addresses, CIDR
  notation, and interface names like "GigabitEthernet0/0/1" each index
  and match as one atomic token, distinct from punctuation variants of
  the same digits;
- ``SourceSearchMode.ALL_TERMS`` (default) phrase-quotes each
  whitespace-split query term individually and joins them with ``AND``;
  ``EXACT_PHRASE`` phrase-quotes the entire query as one unit. Both are
  escaped before being bound as the ``MATCH`` parameter, never
  interpreted as raw FTS5 query-language syntax.

Unlike the source catalog, FTS5 here indexes actual chunk *text* — the
retrievable content itself — plus ``section_title`` metadata, not just
source-level descriptive fields.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

from personal_lms.content.errors import (
    ParentDocumentNotApprovedError,
    ParentDocumentNotFoundError,
    ParentSourceMismatchError,
)
from personal_lms.content.protocol import ChunkSearchFilters, ChunkSearchHit, SourceSearchMode
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.content import ContentChunk, CorpusDocument
from personal_lms.domain.enums import SourceProcessingStatus

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS corpus_documents (
        document_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        status TEXT NOT NULL,
        privacy_classification TEXT NOT NULL,
        record_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_corpus_documents_source_id ON corpus_documents(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_corpus_documents_status ON corpus_documents(status)",
    "CREATE INDEX IF NOT EXISTS idx_corpus_documents_privacy "
    "ON corpus_documents(privacy_classification)",
    """
    CREATE TABLE IF NOT EXISTS corpus_document_knowledge_scopes (
        document_id TEXT NOT NULL,
        knowledge_domain TEXT,
        certification TEXT,
        course TEXT,
        topic TEXT,
        objective_framework TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_doc_scopes_document_id "
    "ON corpus_document_knowledge_scopes(document_id)",
    """
    CREATE TABLE IF NOT EXISTS content_chunks (
        chunk_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        status TEXT NOT NULL,
        privacy_classification TEXT NOT NULL,
        record_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_content_chunks_document_id ON content_chunks(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_content_chunks_source_id ON content_chunks(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_content_chunks_status ON content_chunks(status)",
    "CREATE INDEX IF NOT EXISTS idx_content_chunks_privacy "
    "ON content_chunks(privacy_classification)",
    "CREATE INDEX IF NOT EXISTS idx_content_chunks_document_ordinal "
    "ON content_chunks(document_id, ordinal)",
    """
    CREATE TABLE IF NOT EXISTS content_chunk_knowledge_scopes (
        chunk_id TEXT NOT NULL,
        knowledge_domain TEXT,
        certification TEXT,
        course TEXT,
        topic TEXT,
        objective_framework TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunk_scopes_chunk_id "
    "ON content_chunk_knowledge_scopes(chunk_id)",
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS content_chunk_fts USING fts5(
        chunk_id UNINDEXED,
        text,
        section_title,
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

# Statuses that represent a chunk having passed human curation — mirrors
# the same three-status set used elsewhere (e.g.
# personal_lms.librarian.grounding._APPROVED_STATUSES,
# personal_lms.domain.content._TRUSTED_ELIGIBLE_STATUSES).
_APPROVED_STATUSES = frozenset(
    {
        SourceProcessingStatus.APPROVED,
        SourceProcessingStatus.REVIEWED,
        SourceProcessingStatus.TRUSTED_FOR_RAG,
    }
)


def _escape_fts_phrase(query: str) -> str:
    """Wrap ``query`` as one FTS5 phrase, escaping embedded double quotes."""
    return '"' + query.replace('"', '""') + '"'


def _build_match_query(query: str, mode: SourceSearchMode) -> str | None:
    """The FTS5 ``MATCH`` expression for ``query`` under ``mode``.

    Returns ``None`` for a query with no searchable terms (empty or
    whitespace-only) — never SQL/FTS5 text built from raw caller input.
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
    filters: ChunkSearchFilters | None, *, table_alias: str
) -> tuple[str, list[object]]:
    """A ``" AND ..."`` SQL fragment (or ``""``) plus its bound parameters."""
    if filters is None:
        return "", []

    clauses: list[str] = []
    params: list[object] = []

    if filters.document_id is not None:
        clauses.append(f"{table_alias}.document_id = ?")
        params.append(filters.document_id)
    if filters.source_id is not None:
        clauses.append(f"{table_alias}.source_id = ?")
        params.append(filters.source_id)
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
            "EXISTS (SELECT 1 FROM content_chunk_knowledge_scopes cks "
            f"WHERE cks.chunk_id = {table_alias}.chunk_id AND {scope_where})"
        )
        params.extend(scope_params)

    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


def _citation_location(chunk: ContentChunk) -> str | None:
    """A location string built only from provenance the chunk actually carries.

    Never fabricates a page, section, or timestamp that was not set on
    the chunk.
    """
    parts: list[str] = []
    if chunk.page_number is not None:
        parts.append(f"p.{chunk.page_number}")
    if chunk.section_title is not None:
        parts.append(f"§ {chunk.section_title}")
    start = chunk.timestamp_start_seconds
    end = chunk.timestamp_end_seconds
    if start is not None and end is not None:
        parts.append(f"{start:g}s–{end:g}s")
    elif start is not None:
        parts.append(f"{start:g}s")
    elif end is not None:
        parts.append(f"–{end:g}s")
    return ", ".join(parts) if parts else None


def _citation_from_chunk(chunk: ContentChunk, document: CorpusDocument) -> SourceCitation:
    """A citation with no invented fields.

    ``title`` always names the parent document — never the chunk's own
    ``section_title`` or a synthetic ordinal-based label; page, section,
    and timestamp-range provenance live in ``location`` instead, built
    only from whatever the chunk actually carries.
    """
    return SourceCitation(
        source_id=chunk.source_id,
        title=document.title,
        location=_citation_location(chunk),
        approved=chunk.status in _APPROVED_STATUSES,
    )


class SQLiteContentRepository:
    """SQLite-backed ``ContentRepository``. Structurally conforms to the protocol."""

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

    # --- documents -----------------------------------------------------------

    def upsert_document(self, document: CorpusDocument) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO corpus_documents
                    (document_id, source_id, status, privacy_classification, record_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    status = excluded.status,
                    privacy_classification = excluded.privacy_classification,
                    record_json = excluded.record_json
                """,
                (
                    document.document_id,
                    document.source_id,
                    document.status.value,
                    document.privacy_classification.value,
                    document.model_dump_json(),
                ),
            )

            self._connection.execute(
                "DELETE FROM corpus_document_knowledge_scopes WHERE document_id = ?",
                (document.document_id,),
            )
            self._connection.executemany(
                """
                INSERT INTO corpus_document_knowledge_scopes
                    (document_id, knowledge_domain, certification, course, topic,
                     objective_framework)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        document.document_id,
                        scope.knowledge_domain,
                        scope.certification,
                        scope.course,
                        scope.topic,
                        scope.objective_framework,
                    )
                    for scope in document.knowledge_scopes
                ],
            )

    def get_document(self, document_id: str) -> CorpusDocument | None:
        row = self._connection.execute(
            "SELECT record_json FROM corpus_documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        if row is None:
            return None
        return CorpusDocument.model_validate_json(row["record_json"])

    def list_documents(self, *, source_id: str | None = None) -> tuple[CorpusDocument, ...]:
        if source_id is None:
            rows = self._connection.execute(
                "SELECT record_json FROM corpus_documents ORDER BY document_id"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT record_json FROM corpus_documents WHERE source_id = ? ORDER BY document_id",
                (source_id,),
            ).fetchall()
        return tuple(CorpusDocument.model_validate_json(row["record_json"]) for row in rows)

    # --- chunks ----------------------------------------------------------------

    def upsert_chunk(self, chunk: ContentChunk) -> None:
        parent = self.get_document(chunk.document_id)
        if parent is None:
            raise ParentDocumentNotFoundError(chunk.document_id)
        if parent.source_id != chunk.source_id:
            raise ParentSourceMismatchError(
                chunk.chunk_id, chunk.document_id, parent.source_id, chunk.source_id
            )
        if chunk.trusted_for_rag and parent.status not in _APPROVED_STATUSES:
            raise ParentDocumentNotApprovedError(
                chunk.chunk_id, chunk.document_id, parent.status.value
            )

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO content_chunks
                    (chunk_id, document_id, source_id, ordinal, status,
                     privacy_classification, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    document_id = excluded.document_id,
                    source_id = excluded.source_id,
                    ordinal = excluded.ordinal,
                    status = excluded.status,
                    privacy_classification = excluded.privacy_classification,
                    record_json = excluded.record_json
                """,
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.source_id,
                    chunk.ordinal,
                    chunk.status.value,
                    chunk.privacy_classification.value,
                    chunk.model_dump_json(),
                ),
            )

            self._connection.execute(
                "DELETE FROM content_chunk_knowledge_scopes WHERE chunk_id = ?", (chunk.chunk_id,)
            )
            self._connection.executemany(
                """
                INSERT INTO content_chunk_knowledge_scopes
                    (chunk_id, knowledge_domain, certification, course, topic,
                     objective_framework)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        scope.knowledge_domain,
                        scope.certification,
                        scope.course,
                        scope.topic,
                        scope.objective_framework,
                    )
                    for scope in chunk.knowledge_scopes
                ],
            )

            self._connection.execute(
                "DELETE FROM content_chunk_fts WHERE chunk_id = ?", (chunk.chunk_id,)
            )
            self._connection.execute(
                "INSERT INTO content_chunk_fts (chunk_id, text, section_title) VALUES (?, ?, ?)",
                (chunk.chunk_id, chunk.text, chunk.section_title or ""),
            )

    def get_chunk(self, chunk_id: str) -> ContentChunk | None:
        row = self._connection.execute(
            "SELECT record_json FROM content_chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        if row is None:
            return None
        return ContentChunk.model_validate_json(row["record_json"])

    def list_chunks(self, *, filters: ChunkSearchFilters | None = None) -> tuple[ContentChunk, ...]:
        clause, params = _filter_clause(filters, table_alias="content_chunks")
        rows = self._connection.execute(
            f"SELECT record_json FROM content_chunks WHERE 1=1{clause} "
            "ORDER BY document_id, ordinal, chunk_id",
            params,
        ).fetchall()
        return tuple(ContentChunk.model_validate_json(row["record_json"]) for row in rows)

    def search(
        self,
        query: str,
        *,
        mode: SourceSearchMode = SourceSearchMode.ALL_TERMS,
        filters: ChunkSearchFilters | None = None,
        limit: int = 20,
    ) -> tuple[ChunkSearchHit, ...]:
        fts_query = _build_match_query(query, mode)
        if fts_query is None:
            return ()

        clause, params = _filter_clause(filters, table_alias="cc")
        rows = self._connection.execute(
            f"""
            SELECT cc.record_json AS record_json,
                   cd.record_json AS document_json,
                   fts.rank AS rank,
                   snippet(content_chunk_fts, -1, '[', ']', '...', 10) AS snippet
            FROM content_chunk_fts fts
            JOIN content_chunks cc ON cc.chunk_id = fts.chunk_id
            LEFT JOIN corpus_documents cd ON cd.document_id = cc.document_id
            WHERE content_chunk_fts MATCH ?{clause}
            ORDER BY fts.rank, cc.chunk_id
            LIMIT ?
            """,
            (fts_query, *params, limit),
        ).fetchall()

        hits: list[ChunkSearchHit] = []
        for row in rows:
            chunk = ContentChunk.model_validate_json(row["record_json"])
            if row["document_json"] is None:
                # Unreachable through the public API today (upsert_chunk
                # requires the parent to exist first, and there is no
                # delete_document()) — a genuine data-integrity violation
                # if it is ever hit, never silently dropped from results.
                raise ParentDocumentNotFoundError(chunk.document_id)
            document = CorpusDocument.model_validate_json(row["document_json"])
            hits.append(
                ChunkSearchHit(
                    chunk=chunk,
                    score=-row["rank"],
                    snippet=row["snippet"],
                    citation=_citation_from_chunk(chunk, document),
                )
            )
        return tuple(hits)
