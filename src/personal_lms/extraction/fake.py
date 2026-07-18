"""Deterministic ``FakeExtractor`` test/development-only double.

Not a production extractor and never claims to be one: no PDF library, no
OCR engine, no transcription model, no filesystem read, no network
request, and no subprocess execution happen anywhere in this module. A
real extractor adapter (PDF text extraction, OCR, transcription, ...) is
explicitly out of scope for this milestone — see the package and
``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md``'s
non-goals section.

``FakeExtractor`` does not touch an ``ExtractionQueue`` itself. A caller
(typically a test) claims a job from the queue, passes it to
``extract()`` to obtain a synthetic ``ExtractionResult``, and then calls
the appropriate ``record_success``/``record_retryable_failure``/
``record_terminal_failure`` queue method based on that result — mirroring
how a real worker loop would be structured, without this package
prescribing one (see the queue protocol's "no implicit worker threads, no
polling loop, no scheduler" requirement).
"""

from __future__ import annotations

from uuid import UUID

from personal_lms.domain.extraction import (
    ExtractedArtifact,
    ExtractionFailure,
    ExtractionJob,
    ExtractionResult,
)


class FakeExtractor:
    """Configurable, deterministic extractor test double.

    Configure a synthetic outcome per ``job_id`` via
    ``configure_success``/``configure_retryable_failure``/
    ``configure_terminal_failure`` before calling ``extract()``. Every
    call is recorded in ``calls`` for assertion; ``extract()`` raises
    ``LookupError`` for a job with no configured outcome rather than
    guessing one.
    """

    def __init__(self) -> None:
        self._results: dict[UUID, ExtractionResult] = {}
        self.calls: list[ExtractionJob] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def configure_success(self, job_id: UUID, artifact: ExtractedArtifact) -> None:
        self._results[job_id] = ExtractionResult(job_id=job_id, artifact=artifact)

    def configure_retryable_failure(
        self, job_id: UUID, *, error_code: str, error_message: str
    ) -> None:
        self._results[job_id] = ExtractionResult(
            job_id=job_id,
            failure=ExtractionFailure(
                error_code=error_code, error_message=error_message, retryable=True
            ),
        )

    def configure_terminal_failure(
        self, job_id: UUID, *, error_code: str, error_message: str
    ) -> None:
        self._results[job_id] = ExtractionResult(
            job_id=job_id,
            failure=ExtractionFailure(
                error_code=error_code, error_message=error_message, retryable=False
            ),
        )

    def extract(self, job: ExtractionJob) -> ExtractionResult:
        self.calls.append(job)
        configured = self._results.get(job.job_id)
        if configured is None:
            raise LookupError(f"FakeExtractor has no configured outcome for job {job.job_id}")
        return configured
