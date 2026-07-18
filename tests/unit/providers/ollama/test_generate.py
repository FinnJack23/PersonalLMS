from __future__ import annotations

import asyncio
import importlib.util
import json
from decimal import Decimal

import pytest

if importlib.util.find_spec("httpx") is None:
    pytest.skip(
        "httpx (ollama extra) not installed (uv sync --extra ollama)", allow_module_level=True
    )

import httpx

from personal_lms.domain.models import ModelRequest
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.providers import ProviderContractError, ProviderExecutionError

from ._helpers import build_provider, counting_handler
from .conftest import make_config

pytestmark = pytest.mark.requires_ollama


def _request(**overrides: object) -> ModelRequest:
    defaults: dict[str, object] = {
        "capability_profile": "ollama-local",
        "prompt": "Explain OSPF DR election.",
    }
    defaults.update(overrides)
    return ModelRequest.model_validate(defaults)


def _chat_response(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "message": {"role": "assistant", "content": "The DR is elected by priority."},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 12,
        "eval_count": 34,
        "total_duration": 2_500_000_000,
    }
    defaults.update(overrides)
    return defaults


def test_generate_posts_to_chat_endpoint_with_stream_false() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response())

    provider = build_provider(make_config(), handler)
    asyncio.run(provider.generate(_request()))

    assert seen["method"] == "POST"
    assert seen["path"] == "/api/chat"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["stream"] is False


def test_generate_disables_thinking() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response())

    provider = build_provider(make_config(), handler)
    asyncio.run(provider.generate(_request()))

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["think"] is False


def test_generate_sends_deterministic_options() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response())

    provider = build_provider(make_config(temperature=0.0, seed=42), handler)
    asyncio.run(provider.generate(_request()))

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["options"] == {"temperature": 0.0, "seed": 42}


def test_generate_sends_minimal_single_message() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response())

    provider = build_provider(make_config(), handler)
    asyncio.run(provider.generate(_request(prompt="What is longest prefix match?")))

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["messages"] == [{"role": "user", "content": "What is longest prefix match?"}]


def test_generate_preserves_request_id_correlation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    provider = build_provider(make_config(), handler)
    request = _request()
    result = asyncio.run(provider.generate(request))

    assert result.request_id == request.request_id


def test_generate_declares_local_capability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    provider = build_provider(make_config(), handler)
    result = asyncio.run(provider.generate(_request()))

    assert result.is_local is True
    assert provider.is_local is True
    assert all(profile.is_local for profile in provider.capability_profiles)


def test_generate_supports_restricted_local_only_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    provider = build_provider(make_config(), handler)
    request = _request(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY)
    result = asyncio.run(provider.generate(request))

    assert result is not None
    assert provider.capability_profiles[0].max_privacy_classification == (
        PrivacyClassification.RESTRICTED_LOCAL_ONLY
    )


def test_generate_maps_token_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(prompt_eval_count=17, eval_count=29))

    provider = build_provider(make_config(), handler)
    result = asyncio.run(provider.generate(_request()))

    assert result.input_tokens == 17
    assert result.output_tokens == 29


def test_generate_converts_nanoseconds_to_millisecond_latency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(total_duration=3_200_000_000))

    provider = build_provider(make_config(), handler)
    result = asyncio.run(provider.generate(_request()))

    assert result.latency_ms == pytest.approx(3200.0)


def test_generate_latency_defaults_to_zero_when_duration_absent() -> None:
    response = _chat_response()
    del response["total_duration"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    provider = build_provider(make_config(), handler)
    result = asyncio.run(provider.generate(_request()))

    assert result.latency_ms == 0.0


def test_provider_reports_zero_decimal_cost() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    provider = build_provider(make_config(), handler)
    assert provider.cost_per_call_usd == Decimal("0")


def test_generate_accepts_empty_assistant_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_chat_response(message={"role": "assistant", "content": ""})
        )

    provider = build_provider(make_config(), handler)
    result = asyncio.run(provider.generate(_request()))

    assert result.output_text == ""


def test_generate_rejects_response_missing_message() -> None:
    response = _chat_response()
    del response["message"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderContractError):
        asyncio.run(provider.generate(_request()))


def test_generate_rejects_incomplete_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(done=False))

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderContractError):
        asyncio.run(provider.generate(_request()))


def test_model_not_installed_maps_to_execution_error_without_pulling() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path != "/api/pull"
        return httpx.Response(404, json={"error": "model 'qwen2.5:7b' not found"})

    wrapped, calls = counting_handler(handler)
    provider = build_provider(make_config(), wrapped)

    with pytest.raises(ProviderExecutionError):
        asyncio.run(provider.generate(_request()))

    assert len(calls) == 1
    assert all(call.url.path != "/api/pull" for call in calls)


def test_generate_makes_exactly_one_http_request_and_never_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    wrapped, calls = counting_handler(handler)
    provider = build_provider(make_config(), wrapped)
    asyncio.run(provider.generate(_request()))

    assert len(calls) == 1


def test_generate_does_not_retry_after_a_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal error"})

    wrapped, calls = counting_handler(handler)
    provider = build_provider(make_config(), wrapped)

    with pytest.raises(ProviderExecutionError):
        asyncio.run(provider.generate(_request()))

    assert len(calls) == 1
