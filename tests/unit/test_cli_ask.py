"""Tests for the `personal-lms ask` command.

Separate from tests/unit/test_cli.py (which stays core-safe and always
runs): building an `Application` requires `personal_lms.composition`,
which requires the optional `ollama` extra — see cli.py's module docstring
comment on why that import is TYPE_CHECKING-only at module scope.
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import pytest

if importlib.util.find_spec("httpx") is None:
    pytest.skip(
        "httpx (ollama extra) not installed (uv sync --extra ollama)", allow_module_level=True
    )

from personal_lms.cli import _ask_command, _run_ask, main
from personal_lms.composition import Application
from personal_lms.config import OLLAMA_PROVIDER_ID, AppConfig
from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.enums import RoutingOutcome
from personal_lms.domain.models import ModelRequest, ModelResult
from personal_lms.domain.routing import RoutingDecision
from personal_lms.domain.runs import RunState
from personal_lms.flows.personal_assistant import FlowResult, PersonalAssistantFlow
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers.errors import ProviderExecutionError
from personal_lms.providers.fake import FakeHostedProvider, FakeLocalProvider
from personal_lms.providers.ollama import OllamaProviderConfig
from personal_lms.providers.registry import ProviderRegistry

pytestmark = pytest.mark.requires_ollama


class _TrackingCloseProvider:
    """Wraps a fake provider, adding an async `close()` this test suite can observe.

    `Application.aclose()` always calls `ollama_provider.close()` — the
    bundled `FakeLocalProvider`/`FakeHostedProvider` have no such method
    (they model a provider's request/response contract, not its
    lifecycle), so every test double used as `ollama_provider` here needs
    this thin wrapper.
    """

    def __init__(self, inner: FakeLocalProvider | FakeHostedProvider) -> None:
        self._inner = inner
        self.closed = False

    @property
    def provider_id(self) -> str:
        return self._inner.provider_id

    @property
    def capability_profiles(self) -> tuple[Any, ...]:
        return self._inner.capability_profiles

    @property
    def is_local(self) -> bool:
        return self._inner.is_local

    async def generate(self, request: ModelRequest) -> ModelResult:
        return await self._inner.generate(request)

    async def close(self) -> None:
        self.closed = True


def _local_only_budget(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "policy_id": "test-local-only",
        "daily_limit_usd": Decimal("0"),
        "monthly_limit_usd": Decimal("0"),
        "automatic_single_call_limit_usd": Decimal("0"),
        "approval_single_call_limit_usd": Decimal("0"),
        "local_only": True,
    }
    defaults.update(overrides)
    return BudgetPolicy.model_validate(defaults)


def _app_config() -> AppConfig:
    return AppConfig(
        ollama=OllamaProviderConfig(
            provider_id=OLLAMA_PROVIDER_ID, model="qwen2.5:7b", max_context_tokens=8192
        )
    )


def _build_app(
    *,
    providers: Sequence[FakeLocalProvider | FakeHostedProvider] = (),
    budget_policy: BudgetPolicy | None = None,
) -> tuple[Application, _TrackingCloseProvider]:
    """Compose an Application from fakes only — no real provider, no network."""
    registry = ProviderRegistry()
    tracked = [_TrackingCloseProvider(p) for p in providers]
    for provider in tracked:
        registry.register(provider)

    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)
    close_tracker = tracked[0] if tracked else _TrackingCloseProvider(FakeLocalProvider())

    app = Application(
        config=_app_config(),
        registry=registry,
        router=router,
        budget_policy=budget_policy or _local_only_budget(),
        flow=flow,
        ollama_provider=close_tracker,  # type: ignore[arg-type]
    )
    return app, close_tracker


# --- one Flow call, output printing ------------------------------------------


def test_run_ask_calls_flow_run_exactly_once() -> None:
    local = FakeLocalProvider(output_text="hello from local")
    app, _ = _build_app(providers=[local])

    calls: list[int] = []
    original_run = app.flow.run

    async def counting_run(*args: Any, **kwargs: Any) -> FlowResult:
        calls.append(1)
        return await original_run(*args, **kwargs)

    app.flow.run = counting_run  # type: ignore[method-assign]

    exit_code, message = asyncio.run(_run_ask(app, "hi"))

    assert len(calls) == 1
    assert exit_code == 0
    assert message == "hello from local"


def test_full_ask_command_prints_only_model_text_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    local = FakeLocalProvider(output_text="the model's answer")
    app, _ = _build_app(providers=[local])

    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
    monkeypatch.setattr("personal_lms.composition.compose", lambda config: app)

    exit_code = main(["ask", "--prompt", "hi"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == "the model's answer\n"
    assert captured.err == ""


# --- local-only routing -------------------------------------------------


def test_ask_never_selects_a_hosted_provider_even_when_budget_allows_it() -> None:
    """budget_policy.local_only=False alone would allow hosted routing —
    proves local_only=True is passed explicitly by the ask command itself."""
    local = FakeLocalProvider(output_text="local answer")
    hosted = FakeHostedProvider(output_text="hosted answer")
    permissive_budget = _local_only_budget(
        local_only=False,
        daily_limit_usd=Decimal("100"),
        monthly_limit_usd=Decimal("1000"),
        automatic_single_call_limit_usd=Decimal("1"),
        approval_single_call_limit_usd=Decimal("2"),
    )
    app, _ = _build_app(providers=[local, hosted], budget_policy=permissive_budget)

    exit_code, message = asyncio.run(_run_ask(app, "hi"))

    assert exit_code == 0
    assert message == "local answer"


def test_ask_fails_clearly_when_only_a_hosted_provider_is_registered() -> None:
    hosted = FakeHostedProvider(output_text="hosted answer")
    permissive_budget = _local_only_budget(
        local_only=False,
        daily_limit_usd=Decimal("100"),
        monthly_limit_usd=Decimal("1000"),
        automatic_single_call_limit_usd=Decimal("1"),
        approval_single_call_limit_usd=Decimal("2"),
    )
    app, _ = _build_app(providers=[hosted], budget_policy=permissive_budget)

    exit_code, message = asyncio.run(_run_ask(app, "hi"))

    assert exit_code == 1
    assert "routing error" in message.lower()


# --- error handling: routing, provider, missing output ---------------------


def test_routing_failure_returns_nonzero_with_useful_message() -> None:
    app, _ = _build_app(providers=[])  # empty registry -> no compatible provider

    exit_code, message = asyncio.run(_run_ask(app, "hi"))

    assert exit_code == 1
    assert "routing error" in message.lower()


def test_provider_failure_returns_nonzero_with_useful_message() -> None:
    failing = FakeLocalProvider(fail_with=ProviderExecutionError("fake-local", "simulated failure"))
    app, _ = _build_app(providers=[failing])

    exit_code, message = asyncio.run(_run_ask(app, "hi"))

    assert exit_code == 1
    assert "provider error" in message.lower()
    assert "simulated failure" in message


def test_missing_model_output_returns_nonzero_with_useful_message() -> None:
    """Exercises _run_ask's defensive branch for a FlowResult with no
    model_result, via a stand-in flow — DeterministicRouter's own local_only
    path never actually produces this outcome for the ask command, since
    local_only=True forbids the approval-required/hosted branches that are
    the only source of a None model_result. This proves the CLI handles that
    shape correctly regardless."""
    app, _ = _build_app(providers=[FakeLocalProvider()])

    class _NoOutputFlow:
        async def run(self, request: ModelRequest, **kwargs: Any) -> FlowResult:
            return FlowResult(
                request_id=request.request_id,
                run_state=RunState(workflow_name="test"),
                decision=RoutingDecision(
                    outcome=RoutingOutcome.TIER_0_DETERMINISTIC,
                    reasons=["deterministic_task_declared"],
                ),
                provider_id=None,
                model_result=None,
            )

    # Application is a frozen dataclass; swap in the stand-in flow by
    # building a fresh instance that shares every other composed field.
    app = Application(
        config=app.config,
        registry=app.registry,
        router=app.router,
        budget_policy=app.budget_policy,
        flow=_NoOutputFlow(),  # type: ignore[arg-type]
        ollama_provider=app.ollama_provider,
    )

    exit_code, message = asyncio.run(_run_ask(app, "hi"))

    assert exit_code == 1
    assert "no model output" in message.lower()


def test_empty_prompt_returns_nonzero_with_useful_message() -> None:
    app, _ = _build_app(providers=[FakeLocalProvider()])

    exit_code, message = asyncio.run(_run_ask(app, ""))

    assert exit_code == 2
    assert "invalid prompt" in message.lower()


def test_configuration_failure_returns_nonzero_with_useful_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    exit_code = _ask_command("hi")

    assert exit_code != 0
    assert "OLLAMA_MODEL" in capsys.readouterr().err


# --- cleanup --------------------------------------------------------------


def test_cleanup_closes_the_provider_on_success() -> None:
    local = FakeLocalProvider()
    app, tracker = _build_app(providers=[local])

    asyncio.run(_run_ask(app, "hi"))

    assert tracker.closed is True


def test_cleanup_closes_the_provider_on_routing_failure() -> None:
    app, tracker = _build_app(providers=[])

    asyncio.run(_run_ask(app, "hi"))

    assert tracker.closed is True


def test_cleanup_closes_the_provider_on_provider_failure() -> None:
    failing = FakeLocalProvider(fail_with=ProviderExecutionError("fake-local", "boom"))
    app, tracker = _build_app(providers=[failing])

    asyncio.run(_run_ask(app, "hi"))

    assert tracker.closed is True


def test_cleanup_closes_the_provider_on_invalid_prompt() -> None:
    app, tracker = _build_app(providers=[FakeLocalProvider()])

    asyncio.run(_run_ask(app, ""))

    assert tracker.closed is True
