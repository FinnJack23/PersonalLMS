"""Persistence-neutral Extraction Queue protocol.

Structural contract for enqueuing, claiming, and completing
``ExtractionJob`` rows, and for recording their ``ExtractedArtifact``
results. No implementation lives here — see ``extraction/sqlite.py`` for
the only concrete implementation in this codebase.

Synchronous throughout: every implementation is expected to be local disk
or in-memory I/O (SQLite today), never a network call. No implicit worker
threads, no polling loop, no scheduler — every state transition is an
explicit, caller-initiated method call. Every state-mutating method
accepts an explicit ``now: AwareDatetime`` parameter rather than reading
the system clock itself, mirroring
``personal_lms.source_inventory``'s established explicit-clock
convention (no ``Clock``/time-provider abstraction exists yet in this
codebase, so none is introduced here either — the caller supplies
``now``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import AwareDatetime

from personal_lms.domain.extraction import (
    ExtractedArtifact,
    ExtractionJob,
    ExtractionJobStatus,
    ExtractionRequest,
)


@dataclass(frozen=True, slots=True)
class ExtractionJobFilter:
    """Criteria narrowing a ``list_by_status``-style call. Every field is
    optional and independent — a job matches when it satisfies every
    filter that is set."""

    status: ExtractionJobStatus | None = None
    inventory_source_id: UUID | None = None
    source_version_id: UUID | None = None


@runtime_checkable
class ExtractionQueue(Protocol):
    """Structural contract for extraction-job queue persistence."""

    def initialize_schema(self) -> None:
        """Create the queue's schema if it does not already exist.

        Must be safe to call more than once against the same store.
        """
        ...

    def enqueue(self, request: ExtractionRequest, *, now: AwareDatetime) -> ExtractionJob:
        """Create a new ``PENDING`` job, or return the existing job for a
        repeated ``request.idempotency_key`` — never a duplicate logical
        job. Raises ``ExtractionQueueContractError`` if the existing job
        for that key has different identity fields (``inventory_source_id``,
        ``source_version_id``, ``requested_capability``, ``media_kind``)."""
        ...

    def get(self, job_id: UUID) -> ExtractionJob:
        """Raises ``ExtractionJobNotFoundError`` if ``job_id`` is unknown."""
        ...

    def claim_next(self, *, worker_id: str, now: AwareDatetime) -> ExtractionJob | None:
        """Atomically claim the highest-priority ``PENDING`` job (lowest
        ``priority`` value first; ties broken by ``created_at`` then
        ``job_id`` for fully deterministic ordering), or ``None`` if no
        job is pending.

        At-most-one successful claim per job under the implementation's
        tested transaction boundary — see ``extraction/sqlite.py`` for
        exactly what guarantee that means for the SQLite implementation.
        """
        ...

    def mark_running(self, job_id: UUID, *, worker_id: str, now: AwareDatetime) -> ExtractionJob:
        """``CLAIMED`` -> ``RUNNING``. Raises
        ``InvalidExtractionJobTransitionError`` otherwise, or
        ``ExtractionQueueContractError`` if ``worker_id`` does not match
        the job's claiming worker."""
        ...

    def record_success(
        self, job_id: UUID, artifact: ExtractedArtifact, *, now: AwareDatetime
    ) -> ExtractionJob:
        """``RUNNING`` -> ``SUCCEEDED``, persisting ``artifact``.
        Idempotent for a repeated call with the same (deterministically
        identified) artifact — see ``domain.extraction.derive_artifact_id``."""
        ...

    def record_retryable_failure(
        self, job_id: UUID, *, error_code: str, error_message: str, now: AwareDatetime
    ) -> ExtractionJob:
        """``RUNNING`` -> ``FAILED_RETRYABLE``. Does not itself requeue —
        see ``requeue``."""
        ...

    def record_terminal_failure(
        self, job_id: UUID, *, error_code: str, error_message: str, now: AwareDatetime
    ) -> ExtractionJob:
        """``RUNNING`` -> ``FAILED_TERMINAL``."""
        ...

    def requeue(self, job_id: UUID, *, now: AwareDatetime) -> ExtractionJob:
        """``FAILED_RETRYABLE`` -> ``PENDING``. Never automatic — always
        an explicit caller-initiated call (see the package docstring)."""
        ...

    def cancel(self, job_id: UUID, *, now: AwareDatetime) -> ExtractionJob:
        """``PENDING``/``CLAIMED`` -> ``CANCELLED``. Cancellation from
        ``RUNNING`` is unsupported in this milestone — see
        ``domain.extraction.ALLOWED_JOB_TRANSITIONS``."""
        ...

    def list_by_status(
        self, *, filters: ExtractionJobFilter | None = None
    ) -> tuple[ExtractionJob, ...]:
        """Deterministic order: ``priority`` then ``created_at`` then
        ``job_id``."""
        ...

    def get_artifact(self, artifact_id: UUID) -> ExtractedArtifact:
        """Raises ``ExtractionArtifactNotFoundError`` if unknown."""
        ...

    def list_artifacts_for_job(self, job_id: UUID) -> tuple[ExtractedArtifact, ...]: ...

    def close(self) -> None: ...
