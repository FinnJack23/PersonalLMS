from __future__ import annotations

import asyncio
import importlib.util
import inspect
from decimal import Decimal

import pytest

if importlib.util.find_spec("httpx") is None:
    pytest.skip(
        "httpx (ollama extra) not installed (uv sync --extra ollama)", allow_module_level=True
    )

import httpx

from personal_lms.composition import Application, compose
from personal_lms.config import OLLAMA_PROVIDER_ID, AppConfig
from personal_lms.domain.models import ModelRequest
from personal_lms.flows.personal_assistant import PersonalAssistantFlow
from personal_lms.policies.errors import LocalProviderRequiredError
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers.errors import ProviderAlreadyRegisteredError
from personal_lms.providers.ollama import OllamaProvider, OllamaProviderConfig

pytestmark = pytest.mark.requires_ollama


def _config(**overrides: object) -> AppConfig:
    ollama_kwargs: dict[str, object] = {
        "provider_id": OLLAMA_PROVIDER_ID,
        "model": "qwen2.5:7b",
        "max_context_tokens": 8192,
    }
    ollama_kwargs.update(overrides)
    return AppConfig(ollama=OllamaProviderConfig.model_validate(ollama_kwargs))


# --- composition shape -------------------------------------------------------


def test_compose_returns_an_application() -> None:
    app = compose(_config())
    assert isinstance(app, Application)


def test_ollama_provider_is_registered_exactly_once() -> None:
    app = compose(_config())

    providers = app.registry.list_providers()

    assert len(providers) == 1
    assert providers[0].provider_id == OLLAMA_PROVIDER_ID
    assert isinstance(app.ollama_provider, OllamaProvider)
    assert providers[0] is app.ollama_provider


def test_registering_the_same_provider_id_twice_still_raises() -> None:
    """Sanity check that compose() relies on real registry semantics, not a
    special case: composing twice into the same registry would collide."""
    app = compose(_config())

    with pytest.raises(ProviderAlreadyRegisteredError):
        app.registry.register(app.ollama_provider)


def test_router_and_flow_are_composed() -> None:
    app = compose(_config())

    assert isinstance(app.router, DeterministicRouter)
    assert isinstance(app.flow, PersonalAssistantFlow)


def test_router_selects_the_registered_ollama_provider() -> None:
    """Proves the router is wired to the same registry the provider is in,
    not just independently constructed."""
    app = compose(_config())
    request = ModelRequest(capability_profile=OLLAMA_PROVIDER_ID, prompt="hello")

    result = app.router.route(request, budget_policy=app.budget_policy)

    assert result.provider is app.ollama_provider


# --- budget policy: local-only, zero hosted spend -----------------------


def test_budget_policy_is_local_only() -> None:
    app = compose(_config())
    assert app.budget_policy.local_only is True


def test_budget_policy_has_zero_hosted_spending_limits() -> None:
    app = compose(_config())

    assert app.budget_policy.daily_limit_usd == Decimal("0")
    assert app.budget_policy.monthly_limit_usd == Decimal("0")
    assert app.budget_policy.automatic_single_call_limit_usd == Decimal("0")
    assert app.budget_policy.approval_single_call_limit_usd == Decimal("0")


def test_budget_policy_blocks_hosted_routing_when_no_local_provider_qualifies() -> None:
    """End-to-end proof, not just a field check: routing a request that no
    local provider can satisfy is rejected rather than falling through to a
    hosted candidate — because none is registered, and because local_only
    forces the rejection regardless."""
    app = compose(_config())
    request = ModelRequest(
        capability_profile=OLLAMA_PROVIDER_ID,
        prompt="hello",
        context_token_estimate=10_000_000,  # exceeds every registered profile
    )

    with pytest.raises(LocalProviderRequiredError):
        app.router.route(request, budget_policy=app.budget_policy)


# --- no network access during composition ---------------------------------


def test_compose_is_a_synchronous_function() -> None:
    """compose() never awaits anything, so it cannot perform network I/O —
    the strongest structural guarantee available for this claim."""
    assert inspect.iscoroutinefunction(compose) is False


def test_compose_never_sends_an_http_request() -> None:
    original_send = httpx.AsyncClient.send

    async def _forbidden_send(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        raise AssertionError("compose() must never send an HTTP request")

    httpx.AsyncClient.send = _forbidden_send  # type: ignore[method-assign]
    try:
        compose(_config())
    finally:
        httpx.AsyncClient.send = original_send  # type: ignore[method-assign]


def test_compose_works_with_an_unreachable_base_url() -> None:
    """If composition made any network call it would raise (connection
    refused/timeout) against an address nothing listens on. It doesn't."""
    app = compose(_config(base_url="http://127.0.0.1:1"))
    assert isinstance(app, Application)


# --- cleanup ------------------------------------------------------------


def test_aclose_closes_the_owned_provider() -> None:
    app = compose(_config())

    asyncio.run(app.aclose())

    request = ModelRequest(capability_profile=OLLAMA_PROVIDER_ID, prompt="hello")
    with pytest.raises(RuntimeError):
        asyncio.run(app.ollama_provider.generate(request))
