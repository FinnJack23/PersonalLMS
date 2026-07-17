"""Tests for the injectable ``OllamaChatClient`` boundary.

Proves ``OllamaProvider.generate()`` can be exercised deterministically
via a pure-Python fake implementing exactly the ``OllamaChatClient``
protocol — no ``httpx.AsyncClient``/``httpx.MockTransport`` pair needed —
while producing identical ``ModelResult`` mapping to the existing
httpx-backed path (already covered by ``test_generate.py``, not
duplicated here).

Still requires ``httpx`` to be installed: ``provider.py`` imports ``httpx``
unconditionally at module level (a prior, deliberate optional-dependency
decision — see that module's docstring), so this file uses the same
skip guard as every other test file in this package.
"""

from __future__ import annotations

import asyncio
import importlib.util
from decimal import Decimal
from typing import Any

import pytest

if importlib.util.find_spec("httpx") is None:
    pytest.skip(
        "httpx (ollama extra) not installed (uv sync --extra ollama)", allow_module_level=True
    )

from personal_lms.domain.models import ModelRequest
from personal_lms.providers.errors import ProviderContractError
from personal_lms.providers.ollama.provider import OllamaChatClient, OllamaProvider

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


class _FakeChatClient:
    """Pure-Python OllamaChatClient — no httpx, no network, no transport."""

    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        stream: bool,
        options: dict[str, Any],
        keep_alive: str | None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "stream": stream,
                "options": options,
                "keep_alive": keep_alive,
            }
        )
        return self.response


def _provider(chat_client: OllamaChatClient, **config_overrides: object) -> OllamaProvider:
    return OllamaProvider(make_config(**config_overrides), chat_client=chat_client)


def test_fake_chat_client_satisfies_the_protocol() -> None:
    assert isinstance(_FakeChatClient(_chat_response()), OllamaChatClient)


def test_generate_calls_the_injected_chat_client_exactly_once() -> None:
    fake = _FakeChatClient(_chat_response())
    provider = _provider(fake)

    asyncio.run(provider.generate(_request()))

    assert len(fake.calls) == 1


def test_generate_via_injected_client_sends_the_configured_model() -> None:
    fake = _FakeChatClient(_chat_response())
    provider = _provider(fake, model="qwen2.5:7b")

    asyncio.run(provider.generate(_request()))

    assert fake.calls[0]["model"] == "qwen2.5:7b"


def test_generate_via_injected_client_sends_the_exact_prompt_as_one_user_message() -> None:
    fake = _FakeChatClient(_chat_response())
    provider = _provider(fake)

    asyncio.run(provider.generate(_request(prompt="What is longest prefix match?")))

    assert fake.calls[0]["messages"] == [
        {"role": "user", "content": "What is longest prefix match?"}
    ]


def test_generate_via_injected_client_disables_streaming() -> None:
    fake = _FakeChatClient(_chat_response())
    provider = _provider(fake)

    asyncio.run(provider.generate(_request()))

    assert fake.calls[0]["stream"] is False


def test_generate_via_injected_client_propagates_request_id() -> None:
    fake = _FakeChatClient(_chat_response())
    provider = _provider(fake)
    request = _request()

    result = asyncio.run(provider.generate(request))

    assert result.request_id == request.request_id


def test_generate_via_injected_client_maps_output_text_and_tokens() -> None:
    fake = _FakeChatClient(_chat_response(prompt_eval_count=17, eval_count=29))
    provider = _provider(fake)

    result = asyncio.run(provider.generate(_request()))

    assert result.output_text == "The DR is elected by priority."
    assert result.input_tokens == 17
    assert result.output_tokens == 29


def test_generate_via_injected_client_converts_duration_to_milliseconds() -> None:
    fake = _FakeChatClient(_chat_response(total_duration=3_200_000_000))
    provider = _provider(fake)

    result = asyncio.run(provider.generate(_request()))

    assert result.latency_ms == pytest.approx(3200.0)


def test_generate_via_injected_client_rejects_malformed_response() -> None:
    fake = _FakeChatClient({"unexpected": "shape"})
    provider = _provider(fake)

    with pytest.raises(ProviderContractError):
        asyncio.run(provider.generate(_request()))


def test_injected_chat_client_and_httpx_client_use_the_same_default_when_neither_given() -> None:
    """Sanity check: constructing without chat_client= still works exactly
    as before (the httpx-backed default), proving the new parameter is
    purely additive."""
    provider = OllamaProvider(make_config())
    assert provider.provider_id == "ollama-local"
    assert provider.cost_per_call_usd == Decimal("0")
