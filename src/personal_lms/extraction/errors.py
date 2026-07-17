"""Typed Extraction Queue persistence errors.

Safe context only: job/artifact identifiers, status values, and a
machine-readable reason code. Never raw SQLite error text, extracted
content, worker identifiers beyond what the caller itself supplied, or a
stack trace.
"""

from __future__ import annotations

from uuid import UUID

from personal_lms.domain.extraction import ExtractionJobStatus


class ExtractionQueueError(Exception):
    """Base class for all Extraction Queue persistence errors."""


class ExtractionJobNotFoundError(ExtractionQueueError):
    """Raised when a referenced ``job_id`` has no queued record."""

    def __init__(self, job_id: UUID) -> None:
        super().__init__(f"No extraction job found with id {job_id}")
        self.job_id = job_id


class ExtractionArtifactNotFoundError(ExtractionQueueError):
    """Raised when a referenced ``artifact_id`` has no recorded artifact."""

    def __init__(self, artifact_id: UUID) -> None:
        super().__init__(f"No extracted artifact found with id {artifact_id}")
        self.artifact_id = artifact_id


class InvalidExtractionJobTransitionError(ExtractionQueueError):
    """Raised when a requested status transition is not in
    ``domain.extraction.ALLOWED_JOB_TRANSITIONS``."""

    def __init__(
        self, job_id: UUID, current_status: ExtractionJobStatus, target_status: ExtractionJobStatus
    ) -> None:
        super().__init__(
            f"Extraction job {job_id} cannot transition from "
            f"{current_status.value!r} to {target_status.value!r}"
        )
        self.job_id = job_id
        self.current_status = current_status
        self.target_status = target_status


class ExtractionQueueContractError(ExtractionQueueError):
    """Raised for an internal contract violation (e.g. an unsupported
    schema version, or an idempotency-key collision with mismatched
    request fields)."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Extraction queue contract violated: {reason}")
        self.reason = reason


class ExtractionQueueStorageError(ExtractionQueueError):
    """Raised for a sanitized, underlying storage failure.

    Never carries raw SQLite error text (which can sometimes embed bound
    values) — only a fixed, machine-readable reason category.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"Extraction queue storage failure: {reason}")
        self.reason = reason
