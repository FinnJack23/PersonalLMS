from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path
from uuid import uuid4

import pytest

from personal_lms.domain import (
    GroundingBundle,
    PrivacyClassification,
    SourceVerificationRequest,
    SourceVerificationResult,
    SourceVerificationStatus,
)
from personal_lms.source_verification.errors import (
    SourceVerificationContractError,
    SourceVerificationExecutionError,
    SourceVerificationPrivacyError,
    SourceVerificationUnavailableError,
)
from personal_lms.source_verification.fake import FakeSourceVerifier


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
        "verified_citation_labels": ("E1",),
        "unsupported_claim_count": 0,
        "conflict_count": 0,
    }
    defaults.update(overrides)
    return SourceVerificationResult.model_validate(defaults)


def test_requires_exactly_one_of_result_or_fail_with() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        FakeSourceVerifier()


def test_requires_exactly_one_not_both() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        FakeSourceVerifier(
            result=_result(),
            fail_with=SourceVerificationExecutionError("v", "boom"),
        )


def test_returns_the_configured_result() -> None:
    configured = _result(status=SourceVerificationStatus.VERIFIED)
    fake = FakeSourceVerifier(result=configured)

    outcome = asyncio.run(fake.verify(_request(request_id=configured.request_id)))

    assert outcome == configured


def test_corrects_a_mismatched_configured_request_id() -> None:
    configured = _result(request_id="configured-placeholder")
    fake = FakeSourceVerifier(result=configured)
    request = _request(request_id="req-actual")

    outcome = asyncio.run(fake.verify(request))

    assert outcome.request_id == "req-actual"
    assert outcome.status == configured.status


@pytest.mark.parametrize(
    "fail_with",
    [
        SourceVerificationUnavailableError("v", "down"),
        SourceVerificationExecutionError("v", "boom"),
        SourceVerificationContractError("v", "bad shape"),
        SourceVerificationPrivacyError("v", "privacy"),
    ],
)
def test_raises_each_configured_typed_failure(fail_with: Exception) -> None:
    fake = FakeSourceVerifier(fail_with=fail_with)  # type: ignore[arg-type]

    with pytest.raises(type(fail_with)):
        asyncio.run(fake.verify(_request()))


def test_call_counting_and_request_capture() -> None:
    fake = FakeSourceVerifier(result=_result())
    first = _request(request_id="req-1")
    second = _request(request_id="req-2")

    asyncio.run(fake.verify(first))
    asyncio.run(fake.verify(second))

    assert fake.call_count == 2
    assert fake.calls == [first, second]


def test_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSourceVerifier(result=_result())

    loop = asyncio.new_event_loop()
    try:

        def _blocked(*args: object, **kwargs: object) -> None:
            raise AssertionError("no network access is permitted in FakeSourceVerifier")

        monkeypatch.setattr(socket, "socket", _blocked)

        outcome = loop.run_until_complete(fake.verify(_request()))
        assert outcome.status is SourceVerificationStatus.VERIFIED
    finally:
        monkeypatch.undo()
        loop.close()


def test_has_no_filesystem_effect(tmp_path: Path) -> None:
    fake = FakeSourceVerifier(result=_result())

    asyncio.run(fake.verify(_request()))

    assert list(tmp_path.iterdir()) == []


def test_ignores_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = _result(status=SourceVerificationStatus.VERIFIED)
    fake = FakeSourceVerifier(result=configured)
    monkeypatch.setenv("SOURCE_VERIFIER_API_KEY", "should-be-ignored")
    monkeypatch.setenv("OPENAI_API_KEY", "should-also-be-ignored")

    outcome = asyncio.run(fake.verify(_request(request_id=configured.request_id)))

    assert outcome == configured
    assert os.environ.get("SOURCE_VERIFIER_API_KEY") == "should-be-ignored"
