from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import PrivacyClassification, SourceMediaType
from personal_lms.domain.extraction import (
    ALLOWED_JOB_TRANSITIONS,
    ExtractedArtifact,
    ExtractionArtifactProvenance,
    ExtractionCapability,
    ExtractionFailure,
    ExtractionJob,
    ExtractionJobStatus,
    ExtractionRequest,
    ExtractionResult,
    derive_artifact_id,
    is_valid_job_transition,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _job(**overrides: object) -> ExtractionJob:
    defaults: dict[str, object] = {
        "inventory_source_id": uuid4(),
        "source_version_id": uuid4(),
        "requested_capability": ExtractionCapability.PLAIN_TEXT,
        "media_kind": SourceMediaType.HTML,
        "created_at": _NOW,
        "updated_at": _NOW,
        "idempotency_key": "key-1",
    }
    defaults.update(overrides)
    return ExtractionJob.model_validate(defaults)


def _provenance(**overrides: object) -> ExtractionArtifactProvenance:
    defaults: dict[str, object] = {
        "job_id": uuid4(),
        "inventory_source_id": uuid4(),
        "source_version_id": uuid4(),
        "extractor_name": "fake-extractor",
        "extractor_version": "0.0.0-test",
        "extracted_at": _NOW,
    }
    defaults.update(overrides)
    return ExtractionArtifactProvenance.model_validate(defaults)


def _artifact(**overrides: object) -> ExtractedArtifact:
    defaults: dict[str, object] = {
        "artifact_id": uuid4(),
        "artifact_kind": ExtractionCapability.PLAIN_TEXT,
        "content_locator": "candidate://job/text",
        "created_at": _NOW,
        "provenance": _provenance(),
    }
    defaults.update(overrides)
    return ExtractedArtifact.model_validate(defaults)


# --- ExtractionJob validation ------------------------------------------------


def test_valid_job_defaults_pending() -> None:
    job = _job()
    assert job.status is ExtractionJobStatus.PENDING
    assert job.attempt_count == 0


def test_job_updated_cannot_precede_created() -> None:
    with pytest.raises(ValidationError):
        _job(created_at=_NOW, updated_at=_NOW - timedelta(seconds=1))


def test_job_empty_idempotency_key_rejected() -> None:
    with pytest.raises(ValidationError):
        _job(idempotency_key="")


def test_job_negative_attempt_count_rejected() -> None:
    with pytest.raises(ValidationError):
        _job(attempt_count=-1)


def test_job_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        _job(unexpected_field="nope")


# --- state transitions --------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ExtractionJobStatus.PENDING, ExtractionJobStatus.CLAIMED),
        (ExtractionJobStatus.CLAIMED, ExtractionJobStatus.RUNNING),
        (ExtractionJobStatus.RUNNING, ExtractionJobStatus.SUCCEEDED),
        (ExtractionJobStatus.RUNNING, ExtractionJobStatus.FAILED_RETRYABLE),
        (ExtractionJobStatus.RUNNING, ExtractionJobStatus.FAILED_TERMINAL),
        (ExtractionJobStatus.PENDING, ExtractionJobStatus.CANCELLED),
        (ExtractionJobStatus.CLAIMED, ExtractionJobStatus.CANCELLED),
        (ExtractionJobStatus.FAILED_RETRYABLE, ExtractionJobStatus.PENDING),
    ],
)
def test_allowed_transitions(current: ExtractionJobStatus, target: ExtractionJobStatus) -> None:
    assert is_valid_job_transition(current, target) is True


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ExtractionJobStatus.PENDING, ExtractionJobStatus.RUNNING),
        (ExtractionJobStatus.PENDING, ExtractionJobStatus.SUCCEEDED),
        (ExtractionJobStatus.CLAIMED, ExtractionJobStatus.PENDING),
        (ExtractionJobStatus.CLAIMED, ExtractionJobStatus.SUCCEEDED),
        (ExtractionJobStatus.RUNNING, ExtractionJobStatus.CANCELLED),
        (ExtractionJobStatus.RUNNING, ExtractionJobStatus.PENDING),
        (ExtractionJobStatus.SUCCEEDED, ExtractionJobStatus.PENDING),
        (ExtractionJobStatus.FAILED_TERMINAL, ExtractionJobStatus.PENDING),
        (ExtractionJobStatus.CANCELLED, ExtractionJobStatus.PENDING),
        (ExtractionJobStatus.FAILED_RETRYABLE, ExtractionJobStatus.RUNNING),
    ],
)
def test_rejected_transitions(current: ExtractionJobStatus, target: ExtractionJobStatus) -> None:
    assert is_valid_job_transition(current, target) is False


def test_every_status_has_a_transition_table_entry() -> None:
    assert set(ALLOWED_JOB_TRANSITIONS) == set(ExtractionJobStatus)


def test_running_cannot_be_cancelled() -> None:
    """Documented limitation: cancellation from RUNNING is unsupported in
    this milestone — no cooperative-cancellation mechanism exists yet."""
    assert (
        is_valid_job_transition(ExtractionJobStatus.RUNNING, ExtractionJobStatus.CANCELLED) is False
    )


# --- ExtractedArtifact / provenance -------------------------------------------


def test_artifact_content_hash_must_be_valid_sha256_when_present() -> None:
    with pytest.raises(ValidationError):
        _artifact(content_hash="not-a-hash")


def test_artifact_content_hash_may_be_absent() -> None:
    artifact = _artifact(content_hash=None)
    assert artifact.content_hash is None


def test_artifact_negative_size_rejected() -> None:
    with pytest.raises(ValidationError):
        _artifact(content_size_bytes=-1)


def test_artifact_negative_duration_rejected() -> None:
    with pytest.raises(ValidationError):
        _artifact(duration_seconds=-1.0)


def test_artifact_warnings_do_not_imply_success() -> None:
    """Recording warnings on a successful artifact is allowed, but the
    presence of warnings never itself flips job status — that is the
    queue's job, not this schema's."""
    artifact = _artifact(extraction_warnings=("page 4 was blank",))
    assert artifact.extraction_warnings == ("page 4 was blank",)


# --- deterministic artifact identity -------------------------------------------


def test_same_inputs_produce_same_artifact_id() -> None:
    job_id = uuid4()
    first = derive_artifact_id(
        job_id=job_id,
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_hash="a" * 64,
        content_locator="candidate://x",
    )
    second = derive_artifact_id(
        job_id=job_id,
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_hash="a" * 64,
        content_locator="candidate://x",
    )
    assert first == second


def test_different_job_ids_produce_different_artifact_ids() -> None:
    first = derive_artifact_id(
        job_id=uuid4(),
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_hash="a" * 64,
        content_locator="candidate://x",
    )
    second = derive_artifact_id(
        job_id=uuid4(),
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_hash="a" * 64,
        content_locator="candidate://x",
    )
    assert first != second


def test_different_artifact_kinds_produce_different_artifact_ids() -> None:
    job_id = uuid4()
    plain = derive_artifact_id(
        job_id=job_id,
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_hash="a" * 64,
        content_locator="candidate://x",
    )
    metadata = derive_artifact_id(
        job_id=job_id,
        artifact_kind=ExtractionCapability.METADATA,
        content_hash="a" * 64,
        content_locator="candidate://x",
    )
    assert plain != metadata


def test_content_hash_takes_precedence_over_locator_for_identity() -> None:
    job_id = uuid4()
    by_hash = derive_artifact_id(
        job_id=job_id,
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_hash="a" * 64,
        content_locator="candidate://x",
    )
    by_hash_different_locator = derive_artifact_id(
        job_id=job_id,
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_hash="a" * 64,
        content_locator="candidate://y",
    )
    assert by_hash == by_hash_different_locator


def test_derive_artifact_id_uses_no_random_source() -> None:
    job_id = uuid4()
    results = {
        derive_artifact_id(
            job_id=job_id,
            artifact_kind=ExtractionCapability.PLAIN_TEXT,
            content_hash=None,
            content_locator="candidate://stable",
        )
        for _ in range(25)
    }
    assert len(results) == 1


# --- ExtractionResult -----------------------------------------------------------


def test_result_requires_exactly_one_of_artifact_or_failure() -> None:
    job_id = uuid4()
    with pytest.raises(ValidationError):
        ExtractionResult(job_id=job_id)
    with pytest.raises(ValidationError):
        ExtractionResult(
            job_id=job_id,
            artifact=_artifact(),
            failure=ExtractionFailure(error_code="x", error_message="y", retryable=True),
        )


def test_result_with_only_artifact_is_valid() -> None:
    result = ExtractionResult(job_id=uuid4(), artifact=_artifact())
    assert result.failure is None


# --- ExtractionRequest -----------------------------------------------------------


def test_request_requires_nonempty_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        ExtractionRequest(
            inventory_source_id=uuid4(),
            source_version_id=uuid4(),
            requested_capability=ExtractionCapability.PLAIN_TEXT,
            media_kind=SourceMediaType.PDF,
            idempotency_key="",
        )


def test_request_default_privacy_is_internal() -> None:
    request = ExtractionRequest(
        inventory_source_id=uuid4(),
        source_version_id=uuid4(),
        requested_capability=ExtractionCapability.TRANSCRIPT,
        media_kind=SourceMediaType.VIDEO,
        idempotency_key="k",
    )
    assert request.privacy_classification is PrivacyClassification.INTERNAL
