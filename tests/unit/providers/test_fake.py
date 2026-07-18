from __future__ import annotations

import asyncio
import socket
from decimal import Decimal
from pathlib import Path

import pytest

from personal_lms.domain.models import ModelRequest, ModelResult
from personal_lms.providers import (
    FakeHostedProvider,
    FakeLocalProvider,
    ProviderContractError,
    ProviderError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def _request(prompt: str = "Explain longest prefix match.") -> ModelRequest:
    return ModelRequest(capability_profile="fake_local_general", prompt=prompt)


def test_fake_local_provider_defaults() -> None:
    provider = FakeLocalProvider()
    assert provider.is_local is True
    assert provider.cost_per_call_usd == Decimal("0")
    assert provider.capability_profiles[0].is_local is True
    assert len(provider.capability_profiles) >= 1


def test_fake_hosted_provider_defaults() -> None:
    provider = FakeHostedProvider()
    assert provider.is_local is False
    assert provider.cost_per_call_usd >= Decimal("0")
    assert provider.capability_profiles[0].is_local is False


def test_fake_providers_do_not_claim_to_be_real_vendors() -> None:
    for provider in (FakeLocalProvider(), FakeHostedProvider()):
        blob = f"{provider.provider_id} {provider.output_text}".lower()
        for vendor in ("openai", "anthropic", "gemini", "qwen", "ollama", "claude", "gpt"):
            assert vendor not in blob


def test_generate_preserves_request_correlation() -> None:
    provider = FakeLocalProvider()
    request = _request()

    result = asyncio.run(provider.generate(request))

    assert isinstance(result, ModelResult)
    assert result.request_id == request.request_id


def test_fake_local_provider_configurable_output() -> None:
    provider = FakeLocalProvider(
        output_text="custom local output",
        input_tokens=42,
        output_tokens=7,
        latency_ms=123.4,
        finish_reason="length",
        cost_per_call_usd=Decimal("0"),
    )

    result = asyncio.run(provider.generate(_request()))

    assert result.output_text == "custom local output"
    assert result.input_tokens == 42
    assert result.output_tokens == 7
    assert result.latency_ms == 123.4
    assert result.finish_reason == "length"


def test_fake_hosted_provider_configurable_cost() -> None:
    provider = FakeHostedProvider(cost_per_call_usd=Decimal("0.37"))
    assert provider.cost_per_call_usd == Decimal("0.37")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_tokens": -1},
        {"output_tokens": -1},
        {"latency_ms": -0.1},
        {"cost_per_call_usd": Decimal("-0.01")},
        {"provider_id": ""},
    ],
)
def test_fake_local_provider_rejects_negative_or_empty_configuration(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        FakeLocalProvider(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_tokens": -1},
        {"output_tokens": -1},
        {"latency_ms": -0.1},
        {"cost_per_call_usd": Decimal("-0.01")},
        {"provider_id": ""},
    ],
)
def test_fake_hosted_provider_rejects_negative_or_empty_configuration(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        FakeHostedProvider(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailableError("fake-local", "offline for test"),
        ProviderTimeoutError("fake-local", 1.0),
        ProviderExecutionError("fake-local", "simulated failure"),
        ProviderContractError("fake-local", "bad output shape"),
    ],
)
def test_injected_failure_is_raised_from_generate(error: ProviderError) -> None:
    provider = FakeLocalProvider(fail_with=error)

    with pytest.raises(type(error)):
        asyncio.run(provider.generate(_request()))


def test_injected_failure_message_excludes_prompt_text() -> None:
    secret_prompt = "correct horse battery staple do-not-leak"
    provider = FakeLocalProvider(
        fail_with=ProviderExecutionError("fake-local", "simulated failure")
    )

    with pytest.raises(ProviderExecutionError) as exc_info:
        asyncio.run(provider.generate(_request(prompt=secret_prompt)))

    assert secret_prompt not in str(exc_info.value)


def test_generate_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    # The event loop itself opens a self-pipe socket on construction, so the
    # loop must exist before socket.socket is blocked — this test isolates
    # FakeLocalProvider.generate, not asyncio's own internals.
    loop = asyncio.new_event_loop()
    try:

        def _blocked(*args: object, **kwargs: object) -> None:
            raise AssertionError("no network access is permitted in fake providers")

        monkeypatch.setattr(socket, "socket", _blocked)

        result = loop.run_until_complete(FakeLocalProvider().generate(_request()))
        assert result.output_text
    finally:
        monkeypatch.undo()
        loop.close()


def test_generate_has_no_filesystem_effect(tmp_path: Path) -> None:
    asyncio.run(FakeHostedProvider().generate(_request()))
    assert list(tmp_path.iterdir()) == []


def test_output_is_independent_of_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example.invalid")
    monkeypatch.setenv("HOSTED_MODEL_API_KEY", "should-not-matter")

    provider = FakeLocalProvider()
    result = asyncio.run(provider.generate(_request()))

    assert result.output_text == provider.output_text
