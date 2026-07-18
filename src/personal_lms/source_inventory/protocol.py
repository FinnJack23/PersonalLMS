"""Persistence-neutral Source Inventory protocol.

Structural contract for storing and retrieving ``SourceInventoryRecord``,
``SourceVersion``, and ``SourceLocation`` objects. No implementation lives
here — see ``source_inventory/sqlite.py`` for the only concrete
implementation in this codebase. Deterministic ordering only, no hidden
global state, no environment configuration, no LLM/provider import, and
no dependency on the Tutor, Source Verifier, or Ollama packages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import (
    SourceApprovalStatus,
    SourceInventoryProcessingStatus,
    SourceInventoryRecord,
    SourceLocation,
    SourceLocatorKind,
    SourceMediaType,
    SourceVersion,
)


@dataclass(frozen=True, slots=True)
class SourceInventoryFilter:
    """Criteria narrowing a ``list_sources`` call.

    Every field is optional and independent — a source matches when it
    satisfies every filter that is set. No semantic search or full-text
    query behavior — that is out of scope for this milestone.
    """

    locator_kind: SourceLocatorKind | None = None
    media_type: SourceMediaType | None = None
    processing_status: SourceInventoryProcessingStatus | None = None
    approval_status: SourceApprovalStatus | None = None
    privacy_classification: PrivacyClassification | None = None
    knowledge_domain: str | None = None
    certification: str | None = None
    course: str | None = None
    topic: str | None = None


@runtime_checkable
class SourceInventoryCatalog(Protocol):
    """Structural contract for Source Inventory persistence.

    Synchronous throughout: every implementation is expected to be local
    disk or in-memory I/O (SQLite today), never a network call. Performs
    no filesystem scanning, hashing, URL fetching, extraction, OCR,
    embedding, vector search, RAG generation, Obsidian access, or
    provider call — it only stores and retrieves already-constructed
    domain objects.
    """

    def initialize_schema(self) -> None:
        """Create the inventory's schema if it does not already exist.

        Must be safe to call more than once against the same store.
        """
        ...

    def add_source(self, source: SourceInventoryRecord) -> SourceInventoryRecord:
        """Insert a new source. Raises ``SourceAlreadyExistsError`` for a
        duplicate ``source_id``, or ``SourceLocationConflictError`` for a
        duplicate ``(locator_kind, canonical_locator)``."""
        ...

    def get_source(self, source_id: UUID) -> SourceInventoryRecord:
        """Raises ``SourceNotFoundError`` if ``source_id`` is not cataloged."""
        ...

    def find_by_locator(
        self, locator_kind: SourceLocatorKind, canonical_locator: str
    ) -> SourceInventoryRecord | None: ...

    def list_sources(
        self, *, filters: SourceInventoryFilter | None = None
    ) -> tuple[SourceInventoryRecord, ...]: ...

    def update_source(self, source: SourceInventoryRecord) -> SourceInventoryRecord:
        """Persist mutable metadata/status changes for an existing source.

        Raises ``SourceNotFoundError`` if the source does not exist, or
        ``SourceInventoryContractError`` if an immutable field
        (``source_id``, ``created_at``) has changed.
        """
        ...

    def add_version(self, version: SourceVersion) -> SourceVersion:
        """Raises ``SourceNotFoundError`` for an unknown ``source_id``, or
        ``SourceVersionAlreadyExistsError`` for a duplicate
        ``(source_id, content_hash_sha256)`` pair."""
        ...

    def list_versions(self, source_id: UUID) -> tuple[SourceVersion, ...]: ...

    def add_location(self, location: SourceLocation) -> SourceLocation: ...

    def list_locations(self, source_id: UUID) -> tuple[SourceLocation, ...]: ...

    def close(self) -> None: ...
