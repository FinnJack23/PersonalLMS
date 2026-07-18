from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    CostClass,
    LatencyClass,
    ModelCapabilityProfile,
    ModelRequest,
    ModelResult,
    PrivacyClassification,
)


def _profile(**overrides: object) -> ModelCapabilityProfile:
    defaults: dict[str, object] = {
        "profile_id": "local_general",
        "is_local": True,
        "max_context_tokens": 8192,
        "max_privacy_classification": PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        "latency_class": LatencyClass.STANDARD,
        "cost_class": CostClass.FREE,
    }
    defaults.update(overrides)
    return ModelCapabilityProfile.model_validate(defaults)


def test_capability_profile_is_provider_neutral() -> None:
    profile = _profile()
    dumped = profile.model_dump_json().lower()
    assert "openai" not in dumped
    assert "anthropic" not in dumped
    assert "qwen" not in dumped
    assert profile.profile_id == "local_general"


def test_capability_profile_rejects_non_positive_context() -> None:
    with pytest.raises(ValidationError):
        _profile(max_context_tokens=0)


def test_capability_profile_json_round_trip() -> None:
    profile = _profile()
    restored = ModelCapabilityProfile.model_validate_json(profile.model_dump_json())
    assert restored == profile


def test_model_request_targets_capability_profile_not_a_vendor() -> None:
    request = ModelRequest(capability_profile="learning_reasoning", prompt="Explain LPM.")
    assert isinstance(request.request_id, UUID)
    assert request.capability_profile == "learning_reasoning"


def test_model_request_rejects_negative_context_estimate() -> None:
    with pytest.raises(ValidationError):
        ModelRequest(
            capability_profile="local_general",
            prompt="Explain LPM.",
            context_token_estimate=-1,
        )


def test_model_request_json_round_trip() -> None:
    request = ModelRequest(capability_profile="local_general", prompt="Explain LPM.")
    restored = ModelRequest.model_validate_json(request.model_dump_json())
    assert restored == request


def test_model_result_rejects_negative_tokens_and_latency() -> None:
    with pytest.raises(ValidationError):
        ModelResult(
            request_id=uuid4(),
            capability_profile="local_general",
            is_local=True,
            output_text="answer",
            input_tokens=-1,
            output_tokens=0,
            latency_ms=1.0,
            finish_reason="stop",
        )
    with pytest.raises(ValidationError):
        ModelResult(
            request_id=uuid4(),
            capability_profile="local_general",
            is_local=True,
            output_text="answer",
            input_tokens=0,
            output_tokens=0,
            latency_ms=-1.0,
            finish_reason="stop",
        )


def test_model_result_json_round_trip() -> None:
    result = ModelResult(
        request_id=uuid4(),
        capability_profile="local_general",
        is_local=True,
        output_text="answer",
        input_tokens=10,
        output_tokens=5,
        latency_ms=120.5,
        finish_reason="stop",
    )
    restored = ModelResult.model_validate_json(result.model_dump_json())
    assert restored == result
