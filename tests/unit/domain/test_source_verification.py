from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    ClaimSupportStatus,
    ClaimVerification,
    GroundingBundle,
    PrivacyClassification,
    SourceVerificationRequest,
    SourceVerificationResult,
    SourceVerificationStatus,
)


def _bundle() -> GroundingBundle:
    return GroundingBundle(request_id=uuid4(), is_sufficient=True)


def _request(**overrides: object) -> SourceVerificationRequest:
    defaults: dict[str, object] = {
        "request_id": "req-1",
        "generated_text": "The DR is elected by priority, then router ID. [E1]",
        "grounding_bundle": _bundle(),
        "used_citation_labels": ("E1",),
        "privacy_classification": PrivacyClassification.INTERNAL,
    }
    defaults.update(overrides)
    return SourceVerificationRequest.model_validate(defaults)


def _claim(**overrides: object) -> ClaimVerification:
    defaults: dict[str, object] = {
        "claim_id": "claim-1",
        "status": ClaimSupportStatus.SUPPORTED,
        "evidence_labels": ("E1",),
    }
    defaults.update(overrides)
    return ClaimVerification.model_validate(defaults)


def _result(**overrides: object) -> SourceVerificationResult:
    defaults: dict[str, object] = {
        "request_id": "req-1",
        "status": SourceVerificationStatus.VERIFIED,
        "claims": (_claim(),),
        "verified_citation_labels": ("E1",),
        "unsupported_claim_count": 0,
        "conflict_count": 0,
    }
    defaults.update(overrides)
    return SourceVerificationResult.model_validate(defaults)


# --- SourceVerificationRequest -----------------------------------------------


def test_request_rejects_empty_generated_text() -> None:
    with pytest.raises(ValidationError):
        _request(generated_text="")


def test_request_requires_at_least_one_used_citation_label() -> None:
    with pytest.raises(ValidationError):
        _request(used_citation_labels=())


def test_request_rejects_structurally_malformed_labels() -> None:
    with pytest.raises(ValidationError):
        _request(used_citation_labels=("not-a-label",))


def test_request_accepts_multiple_structurally_valid_labels() -> None:
    request = _request(used_citation_labels=("E1", "E2"))
    assert request.used_citation_labels == ("E1", "E2")


def test_request_requires_privacy_classification() -> None:
    with pytest.raises(ValidationError):
        SourceVerificationRequest.model_validate(
            {
                "request_id": "req-1",
                "generated_text": "x",
                "grounding_bundle": _bundle(),
                "used_citation_labels": ("E1",),
            }
        )


def test_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _request(model_vendor="anthropic")  # type: ignore[call-arg]


# --- SourceVerificationResult: counts and confidence --------------------------


def test_result_rejects_negative_unsupported_claim_count() -> None:
    with pytest.raises(ValidationError):
        _result(unsupported_claim_count=-1)


def test_result_rejects_negative_conflict_count() -> None:
    with pytest.raises(ValidationError):
        _result(conflict_count=-1)


def test_result_accepts_confidence_within_range() -> None:
    result = _result(semantic_confidence=0.42)
    assert result.semantic_confidence == 0.42


def test_result_rejects_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        _result(semantic_confidence=1.5)


def test_result_rejects_confidence_below_zero() -> None:
    with pytest.raises(ValidationError):
        _result(semantic_confidence=-0.1)


# --- SourceVerificationResult: status-shape validators -------------------------


def test_verified_cannot_contain_unsupported_claims() -> None:
    with pytest.raises(ValidationError):
        _result(
            status=SourceVerificationStatus.VERIFIED,
            claims=(_claim(status=ClaimSupportStatus.UNSUPPORTED),),
        )


def test_verified_cannot_contain_conflicting_claims() -> None:
    with pytest.raises(ValidationError):
        _result(
            status=SourceVerificationStatus.VERIFIED,
            claims=(_claim(status=ClaimSupportStatus.CONFLICTING),),
        )


def test_verified_rejects_nonzero_unsupported_claim_count() -> None:
    with pytest.raises(ValidationError):
        _result(status=SourceVerificationStatus.VERIFIED, unsupported_claim_count=1)


def test_verified_rejects_nonzero_conflict_count() -> None:
    with pytest.raises(ValidationError):
        _result(status=SourceVerificationStatus.VERIFIED, conflict_count=1)


def test_verified_accepts_all_claims_supported() -> None:
    result = _result(status=SourceVerificationStatus.VERIFIED, claims=(_claim(),))
    assert result.status is SourceVerificationStatus.VERIFIED


def test_rejected_requires_a_reason_code() -> None:
    with pytest.raises(ValidationError):
        _result(
            status=SourceVerificationStatus.REJECTED,
            claims=(),
            verified_citation_labels=(),
            unsupported_claim_count=1,
            reason_codes=(),
        )


def test_rejected_with_a_reason_code_is_valid() -> None:
    result = _result(
        status=SourceVerificationStatus.REJECTED,
        claims=(),
        verified_citation_labels=(),
        unsupported_claim_count=1,
        reason_codes=("unsupported_claim",),
    )
    assert result.status is SourceVerificationStatus.REJECTED


def test_not_assessed_rejects_a_fabricated_confidence() -> None:
    with pytest.raises(ValidationError):
        _result(
            status=SourceVerificationStatus.NOT_ASSESSED,
            claims=(),
            verified_citation_labels=(),
            unsupported_claim_count=0,
            conflict_count=0,
            semantic_confidence=0.9,
        )


def test_not_applicable_rejects_a_fabricated_confidence() -> None:
    with pytest.raises(ValidationError):
        _result(
            status=SourceVerificationStatus.NOT_APPLICABLE,
            claims=(),
            verified_citation_labels=(),
            unsupported_claim_count=0,
            conflict_count=0,
            semantic_confidence=0.9,
        )


def test_not_assessed_without_confidence_is_valid() -> None:
    result = _result(
        status=SourceVerificationStatus.NOT_ASSESSED,
        claims=(),
        verified_citation_labels=(),
        unsupported_claim_count=0,
        conflict_count=0,
    )
    assert result.semantic_confidence is None
