"""Persistence-neutral source catalog protocol.

Structural contract for storing and retrieving ``SourceRecord`` and
``SourceAssetRelationship`` objects, and for keyword search over cataloged
metadata. No implementation lives here — see ``catalog/sqlite.py`` for the
only concrete implementation in this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from personal_lms.domain.catalog import SourceAssetRelationship, SourceRecord
from personal_lms.domain.enums import SourceProcessingStatus, SourceType
from personal_lms.domain.privacy import PrivacyClassification


class SourceSearchMode(StrEnum):
    """How ``SourceCatalog.search()`` interprets its ``query`` string.

    ``ALL_TERMS`` (the default) requires every whitespace-separated term
    to appear somewhere in a source's searchable metadata, in any order or
    position — ordinary keyword search. ``EXACT_PHRASE`` requires the
    entire query to appear as one exact, ordered, adjacent sequence — use
    it for a known CLI command or error message where word order and
    adjacency are part of what makes the match meaningful.
    """

    ALL_TERMS = "all_terms"
    EXACT_PHRASE = "exact_phrase"


@dataclass(frozen=True, slots=True)
class SourceSearchFilters:
    """Criteria narrowing a ``list_sources``/``search`` call.

    Every field is optional and independent — a source matches when it
    satisfies every filter that is set, mirroring
    ``providers.registry.CapabilityFilter``'s composable-filter shape.
    Knowledge-scope filters match if *any* of a source's
    ``knowledge_scopes`` entries has that field set to the given value —
    a source may carry more than one scope tag.
    """

    source_type: SourceType | None = None
    status: SourceProcessingStatus | None = None
    privacy_classification: PrivacyClassification | None = None
    knowledge_domain: str | None = None
    certification: str | None = None
    course: str | None = None
    topic: str | None = None
    objective_framework: str | None = None


@dataclass(frozen=True, slots=True)
class SourceSearchHit:
    """One keyword-search result.

    ``score`` is higher-is-more-relevant. FTS5's own ``bm25()`` is
    natively lower/more-negative-is-better; see ``catalog/sqlite.py`` for
    the sign flip applied before this type is constructed.
    """

    source_id: str
    record: SourceRecord
    score: float
    snippet: str | None = None


@runtime_checkable
class SourceCatalog(Protocol):
    """Structural contract for source-catalog persistence.

    Synchronous throughout: every implementation is expected to be local
    disk or in-memory I/O (SQLite today), never a network call. This
    protocol performs no filesystem scanning, OCR, embedding, vector
    search, RAG generation, Obsidian access, or provider call — it only
    stores and retrieves already-constructed domain objects.
    """

    def initialize_schema(self) -> None:
        """Create the catalog's schema if it does not already exist.

        Must be safe to call more than once against the same store.
        """
        ...

    def upsert_source(self, record: SourceRecord) -> None:
        """Insert ``record``, or replace the existing row sharing its ``source_id``."""
        ...

    def get_source(self, source_id: str) -> SourceRecord | None:
        """The cataloged record for ``source_id``, or ``None`` if absent."""
        ...

    def list_sources(
        self, *, filters: SourceSearchFilters | None = None
    ) -> tuple[SourceRecord, ...]: ...

    def add_relationship(self, relationship: SourceAssetRelationship) -> None:
        """Insert ``relationship``, or replace the existing row sharing its id."""
        ...

    def list_relationships(self, source_id: str) -> tuple[SourceAssetRelationship, ...]:
        """Every relationship where ``source_id`` appears on either side."""
        ...

    def search(
        self,
        query: str,
        *,
        mode: SourceSearchMode = SourceSearchMode.ALL_TERMS,
        filters: SourceSearchFilters | None = None,
        limit: int = 20,
    ) -> tuple[SourceSearchHit, ...]:
        """Deterministic keyword search over cataloged metadata, best match first."""
        ...

    def close(self) -> None: ...
