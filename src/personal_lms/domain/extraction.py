"""Extraction domain contracts: extraction job queue and artifact metadata.

Pure data shapes only — no filesystem reads, PDF/OCR/video/audio
processing, subprocess execution, or network access happens here. See
``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md`` for the
full design, and ``personal_lms.extraction`` for the persistence-neutral
queue protocol and its only concrete (SQLite) implementation.

This module sits *between* ``domain.source_inventory`` (raw-archive
identity) and ``domain.promotion`` (the human-approved bridge into the
existing curated ``domain.catalog``). An ``ExtractionJob`` always
references an already-cataloged ``SourceInventoryRecord``/``SourceVersion``
pair by ``UUID``; this module never re-derives or duplicates that
identity.

Domain-neutral throughout: no certification, vendor, or knowledge-domain
name is hard-coded anywhere in this module.

Reuses ``SourceMediaType`` (``domain.source_inventory``) as the extraction
job's ``media_kind`` rather than defining a parallel enum — the two
concepts (what kind of media a source is) are identical here, mirroring
``domain.source_inventory``'s own precedent of reusing
``PrivacyClassification`` instead of duplicating it. ``requested_capability``
and ``artifact_kind``, by contrast, are a genuinely new concept (what an
extractor was asked to produce / did produce) with no existing analogue,
so ``ExtractionCapability`` is defined here and reused for both.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4, uuid5

from pydantic import AwareDatetime, Field, field_validator, model_validator

from personal_lms.domain.base import StrictModel
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import SourceMediaType

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Fixed, hardcoded namespace for this module's deterministic artifact
# identity derivation — generated once (uuid4()) and never changed; see
# ``derive_artifact_id``. Mirrors the precedent established in
# ``domain.source_inventory._SOURCE_INVENTORY_NAMESPACE`` and
# ``source_verification.model_backed._SOURCE_VERIFICATION_NAMESPACE``:
# uuid5 only, never uuid4, never a process hash, never the system clock.
_EXTRACTION_ARTIFACT_NAMESPACE = UUID("7389a4db-b757-46f0-b58c-a7750a52a21f")


def _valid_sha256_hex(value: str) -> str:
    if not _SHA256_HEX_PATTERN.fullmatch(value):
        raise ValueError("must be exactly 64 lowercase hex characters")
    return value


class ExtractionCapability(StrEnum):
    """What an extraction job was asked to produce — and what an
    ``ExtractedArtifact`` actually contains.

    Deliberately generic: no capability implies a specific tool (no PDF
    library, no OCR engine, no transcription model). This milestone adds
    no handler for any of these — see the module and package docstrings.
    """

    PLAIN_TEXT = "plain_text"
    STRUCTURED_TEXT = "structured_text"
    METADATA = "metadata"
    CAPTIONS = "captions"
    TRANSCRIPT = "transcript"
    THUMBNAIL_METADATA = "thumbnail_metadata"
    EMBEDDED_TEXT = "embedded_text"


class ExtractionJobStatus(StrEnum):
    """Extraction job lifecycle. See ``ALLOWED_TRANSITIONS`` for the
    complete, enforced state machine."""

    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    CANCELLED = "cancelled"


#: The complete, enforced extraction-job state machine. A transition not
#: listed here is invalid — see ``validate_job_transition``. Cancellation
#: from ``RUNNING`` is deliberately unsupported in this milestone: there is
#: no cooperative-cancellation signal a running extractor could observe.
#: A future milestone may add one; until then, a ``RUNNING`` job can only
#: reach a terminal state via ``record_success``/``record_retryable_failure``/
#: ``record_terminal_failure``.
ALLOWED_JOB_TRANSITIONS: dict[ExtractionJobStatus, frozenset[ExtractionJobStatus]] = {
    ExtractionJobStatus.PENDING: frozenset(
        {ExtractionJobStatus.CLAIMED, ExtractionJobStatus.CANCELLED}
    ),
    ExtractionJobStatus.CLAIMED: frozenset(
        {ExtractionJobStatus.RUNNING, ExtractionJobStatus.CANCELLED}
    ),
    ExtractionJobStatus.RUNNING: frozenset(
        {
            ExtractionJobStatus.SUCCEEDED,
            ExtractionJobStatus.FAILED_RETRYABLE,
            ExtractionJobStatus.FAILED_TERMINAL,
        }
    ),
    ExtractionJobStatus.FAILED_RETRYABLE: frozenset({ExtractionJobStatus.PENDING}),
    ExtractionJobStatus.SUCCEEDED: frozenset(),
    ExtractionJobStatus.FAILED_TERMINAL: frozenset(),
    ExtractionJobStatus.CANCELLED: frozenset(),
}


def is_valid_job_transition(current: ExtractionJobStatus, target: ExtractionJobStatus) -> bool:
    """Pure, deterministic transition check — no I/O, no exception."""
    return target in ALLOWED_JOB_TRANSITIONS[current]


class ExtractionRequest(StrictModel):
    """A caller's request to enqueue one extraction job.

    ``idempotency_key`` is caller-supplied (never derived here) — a
    repeated ``enqueue()`` call using the same key must return the
    existing logical job rather than creating a duplicate (see
    ``personal_lms.extraction.protocol.ExtractionQueue.enqueue``).
    """

    inventory_source_id: UUID
    source_version_id: UUID
    requested_capability: ExtractionCapability
    media_kind: SourceMediaType
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    priority: int = 100
    idempotency_key: str = Field(min_length=1)


class ExtractionJob(StrictModel):
    """One extraction job's full queue state.

    Never stores source bytes, extracted text, or any raw payload — only
    identity, status, and lifecycle timestamps. ``priority`` orders
    ``claim_next()``: lower values claim first; ties break by
    ``created_at`` then ``job_id`` for fully deterministic ordering (see
    ``personal_lms.extraction.sqlite``).
    """

    job_id: UUID = Field(default_factory=uuid4)
    inventory_source_id: UUID
    source_version_id: UUID
    requested_capability: ExtractionCapability
    media_kind: SourceMediaType
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    status: ExtractionJobStatus = ExtractionJobStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    priority: int = 100

    created_at: AwareDatetime
    updated_at: AwareDatetime
    claimed_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    failed_at: AwareDatetime | None = None

    worker_id: str | None = Field(default=None, min_length=1)
    idempotency_key: str = Field(min_length=1)
    last_error_code: str | None = Field(default=None, min_length=1)
    last_error_message: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _updated_not_before_created(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class ExtractionFailure(StrictModel):
    """A typed extraction failure reason — never the raw exception text or
    a stack trace."""

    error_code: str = Field(min_length=1)
    error_message: str = Field(min_length=1)
    retryable: bool


class ExtractionArtifactProvenance(StrictModel):
    """Ties one ``ExtractedArtifact`` to the extraction job, inventory
    source, and source version that produced it, and records which
    extractor did the work.

    A distinct concept from the flat identity fields used elsewhere in
    this codebase (e.g. ``ContentChunk.source_id``) — mirrors
    ``domain.catalog.ProvenanceMetadata``'s role of capturing *how*
    something entered the system, not just *what* it is.
    """

    job_id: UUID
    inventory_source_id: UUID
    source_version_id: UUID
    extractor_name: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    extracted_at: AwareDatetime


class ExtractedArtifact(StrictModel):
    """Metadata about one extraction result — never the extracted payload
    itself.

    ``content_locator`` is an opaque, controlled handle (e.g. a candidate-
    library object key) — never a raw filesystem path or URL that a caller
    could use for unrestricted access, and this schema never interprets or
    resolves it. Extraction succeeding and being recorded here does not
    make the artifact trusted or approved — see
    ``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md``'s
    explicit-promotion-boundary section.
    """

    artifact_id: UUID
    artifact_kind: ExtractionCapability
    content_locator: str = Field(min_length=1)
    content_hash: str | None = None
    content_size_bytes: int | None = Field(default=None, ge=0)
    mime_type: str | None = Field(default=None, min_length=1)
    language: str | None = Field(default=None, min_length=1)
    page_count: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    created_at: AwareDatetime
    extraction_warnings: tuple[str, ...] = Field(default_factory=tuple)
    provenance: ExtractionArtifactProvenance

    @field_validator("content_hash")
    @classmethod
    def _content_hash_is_valid_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _valid_sha256_hex(value)


class ExtractionResult(StrictModel):
    """The outcome of one extractor execution for one job — exactly one of
    ``artifact``/``failure`` is set.

    Used by test/development extractors (see
    ``personal_lms.extraction.fake.FakeExtractor``) to report a synthetic
    outcome; a caller dispatches to
    ``ExtractionQueue.record_success``/``record_retryable_failure``/
    ``record_terminal_failure`` based on which field is set and, for a
    failure, ``ExtractionFailure.retryable``.
    """

    job_id: UUID
    artifact: ExtractedArtifact | None = None
    failure: ExtractionFailure | None = None

    @model_validator(mode="after")
    def _exactly_one_of_artifact_or_failure(self) -> Self:
        if (self.artifact is None) == (self.failure is None):
            raise ValueError("exactly one of artifact or failure must be set")
        return self


def derive_artifact_id(
    *,
    job_id: UUID,
    artifact_kind: ExtractionCapability,
    content_hash: str | None,
    content_locator: str,
) -> UUID:
    """Deterministic artifact identity — never ``uuid4()``, never random.

    Recording the same logical artifact again (e.g. an idempotent retry of
    ``record_success`` for the same job) must produce the same
    ``artifact_id``. Keyed by ``job_id`` (stable across retries of the
    *same* logical job — a fresh ``ExtractionRequest`` with a new
    ``idempotency_key`` is a different logical job) plus ``artifact_kind``
    (one job may produce more than one artifact kind) plus, with
    precedence, ``content_hash`` when known, else ``content_locator``.
    """
    identity = content_hash if content_hash is not None else content_locator
    return uuid5(
        _EXTRACTION_ARTIFACT_NAMESPACE,
        f"{job_id}:{artifact_kind.value}:{identity}",
    )


__all__ = [
    "ALLOWED_JOB_TRANSITIONS",
    "ExtractedArtifact",
    "ExtractionArtifactProvenance",
    "ExtractionCapability",
    "ExtractionFailure",
    "ExtractionJob",
    "ExtractionJobStatus",
    "ExtractionRequest",
    "ExtractionResult",
    "derive_artifact_id",
    "is_valid_job_transition",
]
