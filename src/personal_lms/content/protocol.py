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
from typing import ClassVar, Protocol, runtime_checkable

from personal_lms.catalog.protocol import SourceSearchMode
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.content import ContentChunk, CorpusDocument
from personal_lms.domain.enums import SourceProcessingStatus
from personal_lms.domain.objective_packs import (
    PermittedUse,
    ReviewState,
    TrustStatus,
)
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import SourceRightsStatus

__all__ = [
    "ChunkEligibilityFilter",
    "ChunkSearchFilters",
    "ChunkSearchHit",
    "ContentRepository",
    "SourceSearchMode",
]


@dataclass(frozen=True, slots=True)
class ChunkEligibilityFilter:
    """Structural governance constraints applied in SQL before ``LIMIT``.

    The retrieval-side half of the policy defined once in
    ``objective_packs.eligibility``. Applying these after ``LIMIT`` would
    be a security hole shaped like a performance detail: ineligible rows
    would consume the result window, and the caller would be told "no
    results" rather than the truth.

    Callers should not assemble one of these by hand — use
    ``content.governed.build_governed_filters``, which turns on every
    dimension at once. A hand-built partial filter is almost certainly
    leaving one open by accident.

    Objective scope is deliberately **absent** here: it belongs to the
    existing ``KnowledgeScope.objective_framework`` relation (see
    ``ChunkSearchFilters.objective_framework``), not to a parallel
    governance column that could disagree with it.
    """

    #: The dimensions a governed query constrains, matching
    #: ``content.governed.GOVERNED_DIMENSIONS``. ``ClassVar`` keeps this a
    #: constant rather than a dataclass field — without it, ``slots=True``
    #: would turn it into a per-instance slot descriptor and a constructor
    #: parameter.
    DIMENSIONS: ClassVar[tuple[str, ...]] = (
        "quarantine",
        "rights",
        "permitted_use",
        "privacy",
        "objective_version",
        "review_state",
        "trust",
        "content_binding",
    )

    exclude_quarantined: bool = False
    allowed_rights_statuses: frozenset[SourceRightsStatus] | None = None
    required_permitted_use: PermittedUse | None = None
    allowed_trust_statuses: frozenset[TrustStatus] | None = None
    allowed_review_states: frozenset[ReviewState] | None = None
    require_current_binding: bool = False
    eligibility_policy_version: str | None = None
    #: Review decisions that actually exist in the review store. ``None``
    #: leaves the dimension unconstrained (tests of other dimensions); a
    #: set — including an empty one — constrains it. An empty set
    #: authorizes nothing, which is the correct reading of "no decisions
    #: are persisted".
    #: Classifications the *parent document* may carry. Governed reads
    #: take the strictest classification across chunk and document; the
    #: base ``allowed_privacy_classifications`` filter keeps chunk-only
    #: semantics for existing callers.
    allowed_document_privacy: frozenset[PrivacyClassification] | None = None
    known_review_decision_ids: frozenset[str] | None = None
    #: ``(source_id, sha256)`` pairs for the bytes each source currently
    #: holds. Sorted tuple rather than a mapping so the filter stays
    #: hashable and comparable like every other field here.
    current_source_sha256: tuple[tuple[str, str], ...] | None = None

    @property
    def is_active(self) -> bool:
        """Whether this filter constrains anything at all."""
        return any(
            (
                self.exclude_quarantined,
                self.allowed_rights_statuses is not None,
                self.required_permitted_use is not None,
                self.allowed_trust_statuses is not None,
                self.allowed_review_states is not None,
                self.require_current_binding,
                self.eligibility_policy_version is not None,
                self.allowed_document_privacy is not None,
                self.known_review_decision_ids is not None,
                self.current_source_sha256 is not None,
            )
        )


@dataclass(frozen=True, slots=True)
class ChunkSearchFilters:
    """Criteria narrowing a ``list_chunks``/``search`` call.

    Every field is optional and independent — a chunk matches when it
    satisfies every filter that is set, mirroring
    ``catalog.protocol.SourceSearchFilters``'s composable-filter shape.
    Knowledge-scope filters match if *any* of a chunk's ``knowledge_scopes``
    entries has that field set to the given value.

    ``privacy_classification`` is an exact-match filter (equality only).
    ``allowed_privacy_classifications`` is the multi-value alternative for
    ceiling-style filtering ("this classification or anything less
    restrictive") — set of exactly the classifications permitted, applied
    as a SQL ``IN`` clause in the WHERE (so it narrows candidates before
    ``LIMIT`` truncates, unlike Python-side post-filtering). An empty
    (non-``None``) set means nothing is permitted, not "no filter" —
    matches ``None``-means-unfiltered/empty-collection-means-nothing
    conventions used throughout this codebase.
    """

    document_id: str | None = None
    source_id: str | None = None
    status: SourceProcessingStatus | None = None
    privacy_classification: PrivacyClassification | None = None
    allowed_privacy_classifications: frozenset[PrivacyClassification] | None = None
    knowledge_domain: str | None = None
    certification: str | None = None
    course: str | None = None
    topic: str | None = None
    objective_framework: str | None = None
    eligibility: ChunkEligibilityFilter | None = None


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

    def upsert_eligibility(self, record: object) -> None:
        """Insert, or replace, one chunk's governance record.

        ``record.chunk_id`` must already have a persisted ``ContentChunk``
        — see ``content.errors.ChunkNotFoundError``. Recording
        eligibility for a chunk that does not exist would create a
        governance row nothing can ever match.
        """
        ...

    def get_eligibility(self, chunk_id: str) -> object | None:
        """The chunk's governance record, or ``None`` if never recorded.

        ``None`` means *not eligible* to every filter, never "unfiltered".
        """
        ...

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
