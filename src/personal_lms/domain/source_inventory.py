"""Source Inventory domain contracts: raw-archive inventory and governance metadata.

Pure data shapes only — no filesystem scanning, hashing, URL fetching,
transcript retrieval, extraction, or Obsidian access happens here.

This is a deliberately distinct layer from ``personal_lms.domain.catalog``
(``SourceRecord``/``SourceAssetRelationship``/``SourceCatalog``), which is
an approved, actively-consumed contract (``librarian/grounding.py``, the
content-chunk pipeline's ``CorpusDocument.source_id``/``ContentChunk.source_id``
foreign-key-by-convention chain) built around a caller-supplied ``str``
identity and one combined lifecycle+approval status enum. That contract is
preserved unchanged here — nothing in this module renames, extends, or
touches it.

This module instead models the layer that logically sits *before* that
one: a raw-archive inventory entry may exist before extraction, before a
computed content hash, before any approval decision, and before it is
promoted into a ``SourceRecord``/``CorpusDocument`` for RAG. Where the two
layers overlap conceptually (privacy classification), this module reuses
the existing ``PrivacyClassification`` enum rather than duplicating it.

Domain-neutral throughout: no certification, vendor, or knowledge-domain
name is hard-coded anywhere in this module — see ``knowledge_domains``/
``certifications``/``courses``/``topics`` on ``SourceInventoryRecord``,
all free-form and fully optional.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from posixpath import normpath as _posix_normpath
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4, uuid5

from pydantic import AwareDatetime, Field, computed_field, field_validator, model_validator

from personal_lms.domain.base import StrictModel
from personal_lms.domain.privacy import PrivacyClassification

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Fixed, hardcoded namespace for this module's deterministic source
# identity derivation — generated once (uuid4()) and never changed; see
# ``derive_source_id``. Mirrors the precedent established in
# ``personal_lms.source_verification.model_backed``
# (``_SOURCE_VERIFICATION_NAMESPACE``): uuid5 only, never uuid4, never a
# process hash, never the system clock.
_SOURCE_INVENTORY_NAMESPACE = UUID("2f3a6e7c-9d1b-4b6a-8f0e-6a2c1d9b5e47")


class SourceLocatorKind(StrEnum):
    """How a source is located — independent of what kind of media it is."""

    FILE_PATH = "file_path"
    WEB_URL = "web_url"
    YOUTUBE_URL = "youtube_url"
    OBSIDIAN_NOTE = "obsidian_note"
    OTHER = "other"


class SourceMediaType(StrEnum):
    """What kind of media a source is — independent of how it is located.

    Deliberately separate from ``SourceLocatorKind``: a YouTube URL and a
    local ``.mp4`` file are both ``VIDEO``; a web URL and a local ``.md``
    file are ``HTML``/``MARKDOWN`` respectively, never conflated with
    their locator kind.
    """

    PDF = "pdf"
    MARKDOWN = "markdown"
    HTML = "html"
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    TEXT = "text"
    ARCHIVE = "archive"
    OTHER = "other"


class SourceInventoryProcessingStatus(StrEnum):
    """Extraction/classification lifecycle only — never an approval judgment.

    Distinct from ``personal_lms.domain.enums.SourceProcessingStatus``
    (that enum's ``APPROVED``/``REJECTED``/``REVIEWED``/``TRUSTED_FOR_RAG``
    values conflate processing and approval into one state machine for
    the existing candidate/curated-corpus contract). This narrower enum
    tracks only whether raw-archive extraction/classification work has
    happened — see ``SourceApprovalStatus`` for the separate governance
    decision.
    """

    CATALOGED = "cataloged"
    EXTRACTION_PENDING = "extraction_pending"
    EXTRACTED = "extracted"
    EXTRACTION_FAILED = "extraction_failed"
    CLASSIFICATION_PENDING = "classification_pending"
    CLASSIFIED = "classified"


class SourceApprovalStatus(StrEnum):
    """A human governance decision — never set automatically by this module.

    ``UNREVIEWED`` is the only default any constructor uses; no method in
    this package's SQLite implementation ever changes this value on a
    caller's behalf.
    """

    UNREVIEWED = "unreviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class SourceRightsStatus(StrEnum):
    """Records handling policy only — never a legal determination."""

    UNKNOWN = "unknown"
    OWNED = "owned"
    LICENSED = "licensed"
    PUBLIC_REFERENCE = "public_reference"
    RESTRICTED = "restricted"


class SourceAuthorityLevel(StrEnum):
    """A generic, vendor-neutral source-quality tier.

    Never a specific vendor, certification body, or institution — see the
    module docstring's domain-neutrality note.
    """

    UNKNOWN = "unknown"
    COMMUNITY = "community"
    REVIEWED_INTERNAL = "reviewed_internal"
    APPROVED_COURSE = "approved_course"
    OFFICIAL = "official"


def _valid_sha256_hex(value: str) -> str:
    if not _SHA256_HEX_PATTERN.fullmatch(value):
        raise ValueError("must be exactly 64 lowercase hex characters")
    return value


def _normalize_url_locator(locator: str) -> str:
    """Canonicalize an http(s) locator: preserve scheme/host/path/query, drop fragment.

    Never makes a network call and never resolves redirects. Raises
    ``ValueError`` (with no locator value embedded in the message — see
    ``SourceInventoryRecord``'s docstring) for a non-http(s) scheme or
    embedded credentials.
    """
    parsed = urlsplit(locator)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("locator must use the http or https scheme")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("locator must not embed credentials")
    if not parsed.hostname:
        raise ValueError("locator must include a host")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _normalize_file_path_locator(locator: str) -> str:
    """Deterministic, filesystem-independent path normalization.

    Pure lexical normalization only (collapses ``.``/``..``/duplicate
    separators) via ``posixpath.normpath`` — never touches the
    filesystem, never resolves symlinks, never requires the path to
    exist. All paths are normalized as POSIX-style regardless of host OS,
    so the same logical path always normalizes identically everywhere.
    """
    normalized = _posix_normpath(locator.replace("\\", "/"))
    return normalized


def normalize_locator(locator_kind: SourceLocatorKind, locator: str) -> str:
    """The deterministic canonical form of ``locator`` for ``locator_kind``.

    Shared by ``SourceInventoryRecord``/``SourceLocation`` (as a
    ``computed_field``) and by any caller needing to compute a canonical
    form before it has a full record (e.g. ``find_by_locator`` lookups).
    """
    stripped = locator.strip()
    if not stripped:
        raise ValueError("locator must not be empty")
    if locator_kind in (SourceLocatorKind.WEB_URL, SourceLocatorKind.YOUTUBE_URL):
        return _normalize_url_locator(stripped)
    if locator_kind is SourceLocatorKind.FILE_PATH:
        return _normalize_file_path_locator(stripped)
    return stripped


def _normalize_tags(values: tuple[str, ...]) -> tuple[str, ...]:
    """Strip, drop empties, deduplicate, and sort — fully deterministic
    regardless of input order or duplication."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        cleaned.append(stripped)
    return tuple(sorted(cleaned))


def derive_source_id(
    *,
    existing_id: UUID | None = None,
    content_hash_sha256: str | None = None,
    canonical_locator: str | None = None,
) -> UUID:
    """Deterministic source identity — never ``uuid4()``, never random,
    never clock- or environment-derived.

    Precedence: an already-known ``existing_id`` is preserved exactly;
    otherwise a ``content_hash_sha256`` (when explicitly given) takes
    precedence over ``canonical_locator`` — the same content observed at
    two different locations should identify as the same source, while a
    location alone (no hash yet available) is the fallback identity.
    Equivalent input always produces the equivalent ``UUID``, via
    ``uuid5`` over one fixed, hardcoded namespace. This never merges two
    already-registered sources whose hashes happen to match after the
    fact — that reconciliation is a later milestone's job.
    """
    if existing_id is not None:
        return existing_id
    if content_hash_sha256 is not None:
        return uuid5(_SOURCE_INVENTORY_NAMESPACE, f"sha256:{content_hash_sha256}")
    if canonical_locator is not None:
        return uuid5(_SOURCE_INVENTORY_NAMESPACE, f"locator:{canonical_locator}")
    raise ValueError(
        "derive_source_id requires one of existing_id, content_hash_sha256, or canonical_locator"
    )


class SourceInventoryRecord(StrictModel):
    """One raw-archive inventory entry: identity, location, provenance,
    lifecycle, privacy, approval, and version metadata only.

    Never stores raw source content, credentials, full transcripts,
    extracted text, or LLM-generated summaries — this is inventory and
    governance metadata, not a content store. ``canonical_locator`` is
    always derived deterministically from ``locator``/``locator_kind``
    (see ``normalize_locator``), never independently caller-supplied, so
    two records constructed from the same raw ``locator`` always agree on
    their canonical form. Validation error messages for a locator never
    embed the locator's own value (a file path or URL may itself be
    sensitive) — see ``normalize_locator``.

    ``created_at``/``updated_at`` are required, explicit inputs — this
    schema never calls the system clock itself; a caller or the
    repository layer supplies both.
    """

    source_id: UUID
    locator_kind: SourceLocatorKind
    locator: str = Field(min_length=1)
    media_type: SourceMediaType

    title: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    mime_type: str | None = Field(default=None, min_length=1)
    language: str | None = Field(default=None, min_length=1)

    content_hash_sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)

    processing_status: SourceInventoryProcessingStatus = SourceInventoryProcessingStatus.CATALOGED
    approval_status: SourceApprovalStatus = SourceApprovalStatus.UNREVIEWED
    rights_status: SourceRightsStatus = SourceRightsStatus.UNKNOWN
    authority_level: SourceAuthorityLevel = SourceAuthorityLevel.UNKNOWN
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL

    knowledge_domains: tuple[str, ...] = Field(default_factory=tuple)
    certifications: tuple[str, ...] = Field(default_factory=tuple)
    courses: tuple[str, ...] = Field(default_factory=tuple)
    topics: tuple[str, ...] = Field(default_factory=tuple)

    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("content_hash_sha256")
    @classmethod
    def _content_hash_is_valid_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _valid_sha256_hex(value)

    @field_validator("knowledge_domains", "certifications", "courses", "topics")
    @classmethod
    def _normalize_tag_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_tags(value)

    @model_validator(mode="after")
    def _updated_not_before_created(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self

    @model_validator(mode="after")
    def _locator_is_valid_for_its_kind(self) -> Self:
        # canonical_locator is a lazily-evaluated computed_field below, so
        # construction alone would otherwise never trigger
        # normalize_locator()'s validation (credentials, scheme, empty
        # locator) — this eagerly forces that check at construction time.
        normalize_locator(self.locator_kind, self.locator)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def canonical_locator(self) -> str:
        return normalize_locator(self.locator_kind, self.locator)


class SourceVersion(StrictModel):
    """One append-only, immutable observation of a source's content.

    Version records are never overwritten or deleted by this schema —
    persistence-layer append-only enforcement lives in the repository
    (``SourceVersionAlreadyExistsError``). ``metadata_json`` must be
    JSON-safe (no arbitrary binary or extracted content) — see the
    validator below.
    """

    version_id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    content_hash_sha256: str
    size_bytes: int | None = Field(default=None, ge=0)
    observed_at: AwareDatetime
    supersedes_version_id: UUID | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content_hash_sha256")
    @classmethod
    def _content_hash_is_valid_sha256(cls, value: str) -> str:
        return _valid_sha256_hex(value)

    @field_validator("metadata_json")
    @classmethod
    def _metadata_is_json_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata_json must contain only JSON-safe values") from exc
        return value

    @model_validator(mode="after")
    def _cannot_supersede_itself(self) -> Self:
        if self.supersedes_version_id == self.version_id:
            raise ValueError("supersedes_version_id must not equal version_id")
        return self


class SourceLocation(StrictModel):
    """One historical or current location a source has been observed at.

    Append-only history — a changed locator creates a new
    ``SourceLocation`` row and deactivates the prior one; it never
    deletes or overwrites earlier location metadata (see
    ``SourceInventoryCatalog.update_source``).
    """

    location_id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    locator_kind: SourceLocatorKind
    locator: str = Field(min_length=1)
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime
    is_active: bool = True

    @model_validator(mode="after")
    def _last_not_before_first(self) -> Self:
        if self.last_observed_at < self.first_observed_at:
            raise ValueError("last_observed_at must not precede first_observed_at")
        return self

    @model_validator(mode="after")
    def _locator_is_valid_for_its_kind(self) -> Self:
        normalize_locator(self.locator_kind, self.locator)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def canonical_locator(self) -> str:
        return normalize_locator(self.locator_kind, self.locator)


__all__ = [
    "SourceApprovalStatus",
    "SourceAuthorityLevel",
    "SourceInventoryProcessingStatus",
    "SourceInventoryRecord",
    "SourceLocation",
    "SourceLocatorKind",
    "SourceMediaType",
    "SourceRightsStatus",
    "SourceVersion",
    "derive_source_id",
    "normalize_locator",
]
