"""Typed Source Inventory persistence errors.

Safe context only: source ID, version ID, locator kind, media type, and a
machine-readable reason code. Never a full file path, URL query secret,
raw SQLite error text, raw metadata JSON, credential, or extracted text.
"""

from __future__ import annotations

from uuid import UUID


class SourceInventoryError(Exception):
    """Base class for all Source Inventory persistence errors."""


class SourceAlreadyExistsError(SourceInventoryError):
    """Raised by ``add_source`` when ``source_id`` is already cataloged."""

    def __init__(self, source_id: UUID) -> None:
        super().__init__(f"A source already exists with id {source_id}")
        self.source_id = source_id


class SourceNotFoundError(SourceInventoryError):
    """Raised when a referenced ``source_id`` has no cataloged record."""

    def __init__(self, source_id: UUID) -> None:
        super().__init__(f"No source found with id {source_id}")
        self.source_id = source_id


class SourceVersionAlreadyExistsError(SourceInventoryError):
    """Raised by ``add_version`` for a duplicate ``(source_id, content_hash_sha256)`` pair."""

    def __init__(self, source_id: UUID, content_hash_sha256: str) -> None:
        super().__init__(
            f"A version already exists for source {source_id} with hash {content_hash_sha256}"
        )
        self.source_id = source_id
        self.content_hash_sha256 = content_hash_sha256


class SourceLocationConflictError(SourceInventoryError):
    """Raised when a locator conflicts with an existing source or location.

    Never carries the raw locator value itself (a file path or URL may be
    sensitive) — only its ``locator_kind`` and a machine-readable reason.
    """

    def __init__(self, locator_kind: str, reason: str) -> None:
        super().__init__(f"Locator conflict for locator_kind {locator_kind!r}: {reason}")
        self.locator_kind = locator_kind
        self.reason = reason


class SourceInventoryContractError(SourceInventoryError):
    """Raised for an internal contract violation (e.g. an immutable-field
    change, or an unsupported schema version)."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Source inventory contract violated: {reason}")
        self.reason = reason


class SourceInventoryStorageError(SourceInventoryError):
    """Raised for a sanitized, underlying storage failure.

    Never carries raw SQLite error text (which can sometimes embed bound
    values) — only a fixed, machine-readable reason category.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"Source inventory storage failure: {reason}")
        self.reason = reason
