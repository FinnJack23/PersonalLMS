"""Persistence-neutral content repository protocol.

Structural contract for storing and retrieving ``CorpusDocument`` and
``ContentChunk`` objects, and for keyword search over chunk text. No
implementation lives here — see ``content/sqlite.py`` for the only
concrete implementation in this codebase.

Reuses ``SourceSearchMode`` from ``personal_lms.catalog.protocol`` rather
than defining a parallel enum: "all terms" vs "exact phrase" search-mode
semantics are identical at this layer, just applied to chunk text instead
of source metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from personal_lms.catalog.protocol import SourceSearchMode
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.content import ContentChunk, CorpusDocument
from personal_lms.domain.enums import SourceProcessingStatus
from personal_lms.domain.privacy import PrivacyClassification

__all__ = [
    "ChunkSearchFilters",
    "ChunkSearchHit",
    "ContentRepository",
    "SourceSearchMode",
]


@dataclass(frozen=True, slots=True)
class ChunkSearchFilters:
    """Criteria narrowing a ``list_chunks``/``search`` call.

    Every field is optional and independent — a chunk matches when it
    satisfies every filter that is set, mirroring
    ``catalog.protocol.SourceSearchFilters``'s composable-filter shape.
    Knowledge-scope filters match if *any* of a chunk's ``knowledge_scopes``
    entries has that field set to the given value.
    """

    document_id: str | None = None
    source_id: str | None = None
    status: SourceProcessingStatus | None = None
    privacy_classification: PrivacyClassification | None = None
    knowledge_domain: str | None = None
    certification: str | None = None
    course: str | None = None
    topic: str | None = None
    objective_framework: str | None = None


@dataclass(frozen=True, slots=True)
class ChunkSearchHit:
    """One keyword-search result over chunk text.

    ``score`` is higher-is-more-relevant (see
    ``catalog.protocol.SourceSearchHit`` for the same bm25 sign-flip
    convention). ``citation`` preserves whatever page/section/timestamp
    provenance the chunk actually carries — never a fabricated location.
    """

    chunk: ContentChunk
    score: float
    snippet: str | None
    citation: SourceCitation


@runtime_checkable
class ContentRepository(Protocol):
    """Structural contract for corpus-document and content-chunk persistence.

    Synchronous throughout, local disk or in-memory I/O only (SQLite
    today). Performs no filesystem scanning, extraction, OCR, embedding,
    vector search, model call, or Obsidian access — it only stores and
    retrieves already-constructed domain objects.
    """

    def initialize_schema(self) -> None:
        """Create the repository's schema if it does not already exist.

        Must be safe to call more than once against the same store.
        """
        ...

    def upsert_document(self, document: CorpusDocument) -> None:
        """Insert ``document``, or replace the existing row sharing its ``document_id``."""
        ...

    def get_document(self, document_id: str) -> CorpusDocument | None: ...

    def list_documents(self, *, source_id: str | None = None) -> tuple[CorpusDocument, ...]: ...

    def upsert_chunk(self, chunk: ContentChunk) -> None:
        """Insert ``chunk``, or replace the existing row sharing its ``chunk_id``.

        ``chunk.document_id`` must already have a persisted
        ``CorpusDocument``, and ``chunk.source_id`` must match that
        document's ``source_id`` exactly — see
        ``content.errors.ParentDocumentNotFoundError`` and
        ``content.errors.ParentSourceMismatchError``.
        """
        ...

    def get_chunk(self, chunk_id: str) -> ContentChunk | None: ...

    def list_chunks(
        self, *, filters: ChunkSearchFilters | None = None
    ) -> tuple[ContentChunk, ...]: ...

    def search(
        self,
        query: str,
        *,
        mode: SourceSearchMode = SourceSearchMode.ALL_TERMS,
        filters: ChunkSearchFilters | None = None,
        limit: int = 20,
    ) -> tuple[ChunkSearchHit, ...]:
        """Deterministic keyword search over chunk text and section titles, best match first."""
        ...

    def close(self) -> None: ...
