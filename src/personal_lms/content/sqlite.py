"""SQLite implementation of the persistence-neutral content repository.

Python standard library only (``sqlite3``; JSON fidelity comes from
Pydantic's own ``model_dump_json``/``model_validate_json``) — no new
dependency. Every query is parameterized; this module never interpolates
a search term, filter value, or any other caller-supplied string into SQL
text. Table and column names are fixed literals from this module only,
never caller input, so SQL-injection-shaped search or filter input is
always treated as inert data. This includes the multi-value
``ChunkSearchFilters.allowed_privacy_classifications`` filter: it builds a
``... IN (?, ?, ...)`` clause with exactly one ``?`` per allowed value —
the placeholder count varies, but every value is still bound, never
interpolated — applied in the WHERE clause and therefore evaluated before
``LIMIT`` truncates the result set.

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

import hashlib
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

from personal_lms.content.errors import (
    ChunkNotFoundError,
    ParentDocumentNotApprovedError,
    ParentDocumentNotFoundError,
    ParentSourceMismatchError,
)
from personal_lms.content.governed import GovernedChunkEligibility
from personal_lms.content.protocol import (
    ChunkEligibilityFilter,
    ChunkSearchFilters,
    ChunkSearchHit,
    SourceSearchMode,
)
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.content import ContentChunk, CorpusDocument
from personal_lms.domain.enums import SourceProcessingStatus
from personal_lms.domain.objective_packs import (
    PermittedUse,
    QuarantineStatus,
    TrustStatus,
)
from personal_lms.domain.source_inventory import SourceRightsStatus

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
    # Governance state lives in its own table rather than as new columns
    # on content_chunks: this module creates its schema with CREATE TABLE
    # IF NOT EXISTS and tracks no version, so adding a column would not
    # reach an existing database and would fail at query time. A new
    # table is safe on old and new stores alike.
    """
    CREATE TABLE IF NOT EXISTS content_chunk_eligibility (
        chunk_id TEXT PRIMARY KEY,
        rights_status TEXT NOT NULL,
        permitted_uses_csv TEXT NOT NULL,
        trust_status TEXT NOT NULL,
        quarantine_status TEXT NOT NULL,
        -- Binding columns. These are join keys, not metadata: a governed
        -- query matches only when the chunk's current text hash, its
        -- document's current content hash, and its source's current
        -- identity and bytes all still equal these values. Governance
        -- therefore cannot outlive the content it governs.
        chunk_text_hash TEXT NOT NULL,
        document_content_hash TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        review_decision_id TEXT NOT NULL,
        eligibility_policy_version TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunk_eligibility_trust "
    "ON content_chunk_eligibility(trust_status)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_eligibility_quarantine "
    "ON content_chunk_eligibility(quarantine_status)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_eligibility_binding "
    "ON content_chunk_eligibility(chunk_text_hash, document_content_hash)",
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


def _sha256_text(value: str | None) -> str | None:
    """SHA-256 of ``value`` as lowercase hex, for use inside SQL.

    ``None`` propagates as ``None`` so a missing field never matches a
    real digest.
    """
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    if filters.allowed_privacy_classifications is not None:
        allowed = filters.allowed_privacy_classifications
        if not allowed:
            # An empty allowed-set permits nothing — not "unfiltered".
            clauses.append("1 = 0")
        else:
            placeholders = ", ".join("?" for _ in allowed)
            clauses.append(f"{table_alias}.privacy_classification IN ({placeholders})")
            params.extend(sorted(classification.value for classification in allowed))

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

    if filters.eligibility is not None:
        eligibility_clause, eligibility_params = _eligibility_clause(
            filters.eligibility, table_alias=table_alias
        )
        if eligibility_clause:
            clauses.append(eligibility_clause)
            params.extend(eligibility_params)

    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


def _permitted_uses_csv(permitted_uses: frozenset[PermittedUse]) -> str:
    """Sorted, comma-delimited uses with leading and trailing commas.

    The surrounding commas are what let a containment test be exact: a
    ``LIKE '%,local_teach,%'`` match cannot be satisfied by a longer use
    name that merely ends in ``local_teach``. Values are sorted so the
    stored text is deterministic for a given set.
    """
    if not permitted_uses:
        return ","
    return "," + ",".join(sorted(use.value for use in permitted_uses)) + ","


def _parse_permitted_uses(csv_text: str) -> frozenset[PermittedUse]:
    return frozenset(PermittedUse(value) for value in csv_text.strip(",").split(",") if value)


def _eligibility_clause(
    eligibility: ChunkEligibilityFilter, *, table_alias: str
) -> tuple[str, list[object]]:
    """An ``EXISTS`` fragment constraining governance state, plus its parameters.

    Structured as ``EXISTS (SELECT 1 FROM content_chunk_eligibility ...)``
    so a chunk with *no* governance row fails every active constraint
    automatically — the fail-closed default the protocol documents. It
    lives in the WHERE clause, so SQLite applies it while selecting
    candidates, before ``ORDER BY`` and ``LIMIT`` reduce them.
    """
    conditions: list[str] = []
    params: list[object] = []

    if eligibility.exclude_quarantined:
        conditions.append("ce.quarantine_status = ?")
        params.append(QuarantineStatus.CLEAR.value)

    if eligibility.allowed_rights_statuses is not None:
        allowed = eligibility.allowed_rights_statuses
        if not allowed:
            conditions.append("1 = 0")
        else:
            placeholders = ", ".join("?" for _ in allowed)
            conditions.append(f"ce.rights_status IN ({placeholders})")
            params.extend(sorted(status.value for status in allowed))

    if eligibility.required_permitted_use is not None:
        conditions.append("ce.permitted_uses_csv LIKE ?")
        params.append(f"%,{eligibility.required_permitted_use.value},%")

    if eligibility.allowed_trust_statuses is not None:
        allowed_trust = eligibility.allowed_trust_statuses
        if not allowed_trust:
            conditions.append("1 = 0")
        else:
            placeholders = ", ".join("?" for _ in allowed_trust)
            conditions.append(f"ce.trust_status IN ({placeholders})")
            params.extend(sorted(status.value for status in allowed_trust))

    if eligibility.allowed_review_states is not None and not eligibility.allowed_review_states:
        # An empty allowed-set permits nothing. A non-empty one is
        # satisfied structurally: a governance row exists only because a
        # persisted review decision authorized it, so review state is
        # carried by review_decision_id rather than duplicated here.
        conditions.append("1 = 0")

    if eligibility.eligibility_policy_version is not None:
        conditions.append("ce.eligibility_policy_version = ?")
        params.append(eligibility.eligibility_policy_version)

    if eligibility.allowed_document_privacy is not None:
        # Governed retrieval takes the *strictest* classification across
        # the chunk and its parent document. The base filter deliberately
        # keeps chunk-only semantics — existing Librarian callers depend
        # on it — but a governed read must not let a public child silently
        # downgrade a restricted parent.
        allowed_docs = eligibility.allowed_document_privacy
        if not allowed_docs:
            conditions.append("1 = 0")
        else:
            placeholders = ", ".join("?" for _ in allowed_docs)
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM corpus_documents cd_priv "
                f"WHERE cd_priv.document_id = {table_alias}.document_id "
                f"AND cd_priv.privacy_classification NOT IN ({placeholders}))"
            )
            params.extend(sorted(classification.value for classification in allowed_docs))

    if eligibility.known_review_decision_ids is not None:
        # A governance row must name a decision the review store actually
        # holds. A non-empty string proves nothing; an empty allowed set
        # correctly authorizes nothing.
        allowed_decisions = eligibility.known_review_decision_ids
        if not allowed_decisions:
            conditions.append("1 = 0")
        else:
            placeholders = ", ".join("?" for _ in allowed_decisions)
            conditions.append(f"ce.review_decision_id IN ({placeholders})")
            params.extend(sorted(allowed_decisions))

    if eligibility.current_source_sha256 is not None:
        # The row's pinned source bytes must equal what that source
        # currently holds. Expressed as a VALUES join so the comparison
        # happens in SQL rather than after the fact.
        pairs = eligibility.current_source_sha256
        if not pairs:
            conditions.append("1 = 0")
        else:
            tuples = ", ".join("(?, ?)" for _ in pairs)
            conditions.append(f"(ce.source_id, ce.source_sha256) IN (VALUES {tuples})")
            for source_id, sha in pairs:
                params.extend((source_id, sha))

    if eligibility.require_current_binding:
        # The binding join. Each equality ties the governance row to the
        # *current* state of the thing it governs, so replacing a chunk's
        # text, revising its parent document, or superseding its source
        # makes this row stop matching — no invalidation sweep needed.
        # Recomputed from the chunk's actual text, not read from its
        # stored ``text_hash`` field. ``ContentChunk.text_hash`` is a
        # caller-supplied digest under the existing shared contract, so a
        # rewritten chunk could keep a stale one; binding governance to
        # the recomputed value closes that without narrowing a contract
        # every other consumer already depends on.
        conditions.append(
            f"ce.chunk_text_hash = personal_lms_sha256("
            f"json_extract({table_alias}.record_json, '$.text'))"
        )
        conditions.append(f"ce.source_id = {table_alias}.source_id")

        # The chunk's own current state, not the state it had when the
        # governance row was written. Withdrawing trusted_for_rag or
        # downgrading status must revoke access immediately.
        status_placeholders = ", ".join("?" for _ in _APPROVED_STATUSES)
        conditions.append(f"{table_alias}.status IN ({status_placeholders})")
        params.extend(sorted(status.value for status in _APPROVED_STATUSES))
        conditions.append(f"json_extract({table_alias}.record_json, '$.trusted_for_rag') = 1")

        # The parent document's current hash *and* current status.
        conditions.append(
            "EXISTS (SELECT 1 FROM corpus_documents cd_bind "
            f"WHERE cd_bind.document_id = {table_alias}.document_id "
            "AND json_extract(cd_bind.record_json, '$.content_hash') = ce.document_content_hash "
            f"AND cd_bind.status IN ({status_placeholders}))"
        )
        params.extend(sorted(status.value for status in _APPROVED_STATUSES))
        conditions.append("ce.review_decision_id <> ''")

    if not conditions:
        return "", []

    where = " AND ".join(conditions)
    clause = (
        "EXISTS (SELECT 1 FROM content_chunk_eligibility ce "
        f"WHERE ce.chunk_id = {table_alias}.chunk_id AND {where})"
    )
    return clause, params


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
        # A deterministic SHA-256 usable inside the WHERE clause, so the
        # governed binding can recompute a chunk's digest from its actual
        # text *before* ORDER BY and LIMIT. Standard library only; marked
        # deterministic so SQLite may use it in an index or partial index.
        self._connection.create_function("personal_lms_sha256", 1, _sha256_text, deterministic=True)

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

    def upsert_eligibility(self, record: GovernedChunkEligibility) -> None:
        """Insert or replace one chunk's governance record.

        Replacement rather than accumulation is deliberate: a chunk has
        exactly one current governance state, and the binding columns make
        a stale row unmatched rather than merely outdated.
        """
        existing = self._connection.execute(
            "SELECT 1 FROM content_chunks WHERE chunk_id = ?", (record.chunk_id,)
        ).fetchone()
        if existing is None:
            raise ChunkNotFoundError(record.chunk_id)

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO content_chunk_eligibility
                    (chunk_id, rights_status, permitted_uses_csv, trust_status,
                     quarantine_status, chunk_text_hash, document_content_hash,
                     source_id, source_sha256, review_decision_id,
                     eligibility_policy_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    rights_status = excluded.rights_status,
                    permitted_uses_csv = excluded.permitted_uses_csv,
                    trust_status = excluded.trust_status,
                    quarantine_status = excluded.quarantine_status,
                    chunk_text_hash = excluded.chunk_text_hash,
                    document_content_hash = excluded.document_content_hash,
                    source_id = excluded.source_id,
                    source_sha256 = excluded.source_sha256,
                    review_decision_id = excluded.review_decision_id,
                    eligibility_policy_version = excluded.eligibility_policy_version
                """,
                (
                    record.chunk_id,
                    record.rights_status.value,
                    _permitted_uses_csv(record.permitted_uses),
                    record.trust_status.value,
                    record.quarantine_status.value,
                    record.chunk_text_hash,
                    record.document_content_hash,
                    record.source_id,
                    record.source_sha256,
                    record.review_decision_id,
                    record.eligibility_policy_version,
                ),
            )

    def get_eligibility(self, chunk_id: str) -> GovernedChunkEligibility | None:
        row = self._connection.execute(
            """
            SELECT rights_status, permitted_uses_csv, trust_status, quarantine_status,
                   chunk_text_hash, document_content_hash, source_id, source_sha256,
                   review_decision_id, eligibility_policy_version
            FROM content_chunk_eligibility WHERE chunk_id = ?
            """,
            (chunk_id,),
        ).fetchone()
        if row is None:
            return None
        return GovernedChunkEligibility(
            chunk_id=chunk_id,
            chunk_text_hash=row["chunk_text_hash"],
            document_content_hash=row["document_content_hash"],
            source_id=row["source_id"],
            source_sha256=row["source_sha256"],
            review_decision_id=row["review_decision_id"],
            eligibility_policy_version=row["eligibility_policy_version"],
            rights_status=SourceRightsStatus(row["rights_status"]),
            permitted_uses=_parse_permitted_uses(row["permitted_uses_csv"]),
            trust_status=TrustStatus(row["trust_status"]),
            quarantine_status=QuarantineStatus(row["quarantine_status"]),
        )

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
