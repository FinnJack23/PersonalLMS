from __future__ import annotations

from uuid import uuid4

import pytest

from personal_lms.domain import (
    ClaimSupportStatus,
    ClaimVerification,
    GroundingBundle,
    PrivacyClassification,
    SourceVerificationRequest,
    SourceVerificationResult,
    SourceVerificationStatus,
)
from personal_lms.source_verification.errors import SourceVerificationContractError
from personal_lms.source_verification.fake import FakeSourceVerifier
from personal_lms.source_verification.protocol import (
    SourceVerifier,
    validate_result_matches_request,
)


def _bundle() -> GroundingBundle:
    return GroundingBundle(request_id=uuid4(), is_sufficient=True)


def _request(**overrides: object) -> SourceVerificationRequest:
    defaults: dict[str, object] = {
        "request_id": "req-1",
        "generated_text": "The DR is elected by priority. [E1]",
        "grounding_bundle": _bundle(),
        "used_citation_labels": ("E1",),
        "privacy_classification": PrivacyClassification.INTERNAL,
    }
    defaults.update(overrides)
    return SourceVerificationRequest.model_validate(defaults)


def _result(**overrides: object) -> SourceVerificationResult:
    defaults: dict[str, object] = {
        "request_id": "req-1",
        "status": SourceVerificationStatus.VERIFIED,
        "claims": (
            ClaimVerification(
                claim_id="claim-1",
                status=ClaimSupportStatus.SUPPORTED,
                evidence_labels=("E1",),
            ),
        ),
        "verified_citation_labels": ("E1",),
        "unsupported_claim_count": 0,
        "conflict_count": 0,
    }
    defaults.update(overrides)
    return SourceVerificationResult.model_validate(defaults)


# --- runtime structural compatibility -----------------------------------------


def test_fake_source_verifier_satisfies_protocol() -> None:
    assert isinstance(FakeSourceVerifier(result=_result()), SourceVerifier)


def test_object_missing_verify_does_not_satisfy_protocol() -> None:
    class _NotAVerifier:
        verifier_id = "x"

    assert not isinstance(_NotAVerifier(), SourceVerifier)


# --- validate_result_matches_request -------------------------------------------


def test_validate_result_matches_request_accepts_a_consistent_pair() -> None:
    request = _request()
    result = _result()
    validate_result_matches_request(request, result, verifier_id="v")


def test_validate_result_matches_request_rejects_a_request_id_mismatch() -> None:
    request = _request(request_id="req-1")
    result = _result(request_id="req-2")
    with pytest.raises(SourceVerificationContractError):
        validate_result_matches_request(request, result, verifier_id="v")


def test_validate_result_matches_request_rejects_an_unknown_verified_label() -> None:
    request = _request(used_citation_labels=("E1",))
    result = _result(verified_citation_labels=("E1", "E2"))
    with pytest.raises(SourceVerificationContractError):
        validate_result_matches_request(request, result, verifier_id="v")


def test_validate_result_matches_request_rejects_an_unknown_claim_evidence_label() -> None:
    request = _request(used_citation_labels=("E1",))
    result = _result(
        claims=(
            ClaimVerification(
                claim_id="claim-1",
                status=ClaimSupportStatus.SUPPORTED,
                evidence_labels=("E1", "E99"),
            ),
        ),
    )
    with pytest.raises(SourceVerificationContractError):
        validate_result_matches_request(request, result, verifier_id="v")
