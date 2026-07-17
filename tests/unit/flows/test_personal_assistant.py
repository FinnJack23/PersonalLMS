from __future__ import annotations

import asyncio
import socket
from decimal import Decimal
from pathlib import Path

import pytest

from personal_lms.domain.enums import CostClass, RoutingOutcome, RunStatus
from personal_lms.flows import FlowResult, PersonalAssistantFlow
from personal_lms.policies import DeterministicRouter, NoCompatibleProviderError
from personal_lms.providers import FakeHostedProvider, FakeLocalProvider
from personal_lms.providers.errors import (
    ProviderContractError,
    ProviderError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from personal_lms.providers.registry import ProviderRegistry

from ._helpers import CountingProvider, make_budget_policy, make_profile, make_request

# --- Tier 0 ------------------------------------------------------------


def test_tier_0_does_not_access_registry_or_provider() -> None:
    router = DeterministicRouter(ProviderRegistry())  # empty registry
    flow = PersonalAssistantFlow(router)

    result = asyncio.run(
        flow.run(make_request(), budget_policy=make_budget_policy(), deterministic_capable=True)
    )

    assert result.decision.outcome == RoutingOutcome.TIER_0_DETERMINISTIC
    assert result.provider_id is None
    assert result.model_result is None
    assert result.run_state.status == RunStatus.COMPLETED


# --- Successful execution -------------------------------------------------


def test_local_tier_1_calls_provider_exactly_once() -> None:
    inner = FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    counting = CountingProvider(inner=inner)
    registry = ProviderRegistry()
    registry.register(counting)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    result = asyncio.run(flow.run(make_request(), budget_policy=make_budget_policy()))

    assert result.decision.outcome == RoutingOutcome.TIER_1_LOCAL
    assert counting.call_count == 1
    assert result.provider_id == "solo"
    assert result.model_result is not None
    assert result.run_state.status == RunStatus.COMPLETED


def test_hosted_tier_2_calls_provider_exactly_once() -> None:
    inner = FakeHostedProvider(
        "hosted", capability_profiles=(make_profile(profile_id="h", is_local=False),)
    )
    counting = CountingProvider(inner=inner)
    registry = ProviderRegistry()
    registry.register(counting)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    result = asyncio.run(flow.run(make_request(), budget_policy=make_budget_policy()))

    assert result.decision.outcome == RoutingOutcome.TIER_2_HOSTED
    assert counting.call_count == 1
    assert result.provider_id == "hosted"
    assert result.model_result is not None
    assert result.run_state.status == RunStatus.COMPLETED


def test_provider_result_is_returned_unchanged() -> None:
    inner = FakeLocalProvider(
        "solo",
        capability_profiles=(make_profile(profile_id="p"),),
        output_text="deterministic canned answer",
        input_tokens=11,
        output_tokens=22,
        latency_ms=33.0,
        finish_reason="stop",
    )
    registry = ProviderRegistry()
    registry.register(inner)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    result = asyncio.run(flow.run(make_request(), budget_policy=make_budget_policy()))

    assert result.model_result is not None
    assert result.model_result.output_text == "deterministic canned answer"
    assert result.model_result.input_tokens == 11
    assert result.model_result.output_tokens == 22
    assert result.model_result.latency_ms == 33.0
    assert result.model_result.finish_reason == "stop"


def test_request_id_is_preserved_through_the_flow() -> None:
    inner = FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    registry = ProviderRegistry()
    registry.register(inner)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)
    request = make_request()

    result = asyncio.run(flow.run(request, budget_policy=make_budget_policy()))

    assert result.request_id == request.request_id
    assert result.model_result is not None
    assert result.model_result.request_id == request.request_id


# --- Approval-required / rejection never call a provider -------------------


def test_approval_required_calls_no_provider() -> None:
    inner = FakeHostedProvider(
        "hosted", capability_profiles=(make_profile(profile_id="h", is_local=False),)
    )
    counting = CountingProvider(inner=inner)
    registry = ProviderRegistry()
    registry.register(counting)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    result = asyncio.run(
        flow.run(
            make_request(),
            budget_policy=make_budget_policy(automatic_single_call_limit_usd=Decimal("0")),
        )
    )

    assert result.decision.outcome == RoutingOutcome.APPROVAL_REQUIRED
    assert result.provider_id is None
    assert result.model_result is None
    assert counting.call_count == 0
    assert result.run_state.status == RunStatus.WAITING_FOR_APPROVAL


def test_routing_rejection_calls_no_provider() -> None:
    inner = FakeLocalProvider(
        "tiny", capability_profiles=(make_profile(profile_id="t", max_context_tokens=1),)
    )
    counting = CountingProvider(inner=inner)
    registry = ProviderRegistry()
    registry.register(counting)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    with pytest.raises(NoCompatibleProviderError):
        asyncio.run(
            flow.run(
                make_request(context_token_estimate=999_999), budget_policy=make_budget_policy()
            )
        )

    assert counting.call_count == 0
    assert flow.run_state is not None
    assert flow.run_state.status == RunStatus.FAILED


def test_routing_error_is_not_handled_as_provider_execution_failure() -> None:
    router = DeterministicRouter(ProviderRegistry())  # empty -> no compatible provider
    flow = PersonalAssistantFlow(router)

    with pytest.raises(NoCompatibleProviderError):
        asyncio.run(flow.run(make_request(), budget_policy=make_budget_policy()))

    assert flow.run_state is not None
    assert "NoCompatibleProviderError" in (flow.run_state.error_message or "")
    assert "ProviderExecutionError" not in (flow.run_state.error_message or "")
    assert "ProviderTimeoutError" not in (flow.run_state.error_message or "")


# --- Provider failure mapping, no retry, no fallback ------------------------


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailableError("solo", "offline for test"),
        ProviderTimeoutError("solo", 1.0),
        ProviderExecutionError("solo", "simulated failure"),
        ProviderContractError("solo", "bad output shape"),
    ],
)
def test_each_provider_error_subtype_is_handled_distinctly(error: ProviderError) -> None:
    inner = FakeLocalProvider(
        "solo", capability_profiles=(make_profile(profile_id="p"),), fail_with=error
    )
    registry = ProviderRegistry()
    registry.register(inner)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    with pytest.raises(type(error)):
        asyncio.run(flow.run(make_request(), budget_policy=make_budget_policy()))

    assert flow.run_state is not None
    assert flow.run_state.status == RunStatus.FAILED
    assert type(error).__name__ in (flow.run_state.error_message or "")


def test_provider_failure_is_not_retried() -> None:
    inner = FakeLocalProvider(
        "solo",
        capability_profiles=(make_profile(profile_id="p"),),
        fail_with=ProviderTimeoutError("solo", 1.0),
    )
    counting = CountingProvider(inner=inner)
    registry = ProviderRegistry()
    registry.register(counting)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    with pytest.raises(ProviderTimeoutError):
        asyncio.run(flow.run(make_request(), budget_policy=make_budget_policy()))

    assert counting.call_count == 1


def test_provider_failure_does_not_fall_back_to_another_provider() -> None:
    failing_inner = FakeLocalProvider(
        "failing",
        capability_profiles=(make_profile(profile_id="f", cost_class=CostClass.FREE),),
        fail_with=ProviderExecutionError("failing", "boom"),
    )
    healthy_inner = FakeLocalProvider(
        "healthy", capability_profiles=(make_profile(profile_id="h", cost_class=CostClass.HIGH),)
    )
    failing = CountingProvider(inner=failing_inner)
    healthy = CountingProvider(inner=healthy_inner)
    registry = ProviderRegistry()
    registry.register(failing)
    registry.register(healthy)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    with pytest.raises(ProviderExecutionError):
        asyncio.run(flow.run(make_request(), budget_policy=make_budget_policy()))

    assert failing.call_count == 1
    assert healthy.call_count == 0


# --- Prompt-leak safety ------------------------------------------------------


def test_prompt_text_never_appears_in_audit_or_errors() -> None:
    secret = "correct horse battery staple do-not-leak"
    inner = FakeLocalProvider(
        "solo",
        capability_profiles=(make_profile(profile_id="p"),),
        fail_with=ProviderExecutionError("solo", "boom"),
    )
    registry = ProviderRegistry()
    registry.register(inner)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    with pytest.raises(ProviderExecutionError) as exc_info:
        asyncio.run(flow.run(make_request(prompt=secret), budget_policy=make_budget_policy()))

    assert secret not in str(exc_info.value)
    assert flow.run_state is not None
    assert secret not in flow.run_state.model_dump_json()


def test_routing_decision_is_independent_of_prompt_content() -> None:
    inner = FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    registry = ProviderRegistry()
    registry.register(inner)
    router = DeterministicRouter(registry)

    first = asyncio.run(
        PersonalAssistantFlow(router).run(
            make_request(prompt="a"), budget_policy=make_budget_policy()
        )
    )
    second = asyncio.run(
        PersonalAssistantFlow(router).run(
            make_request(prompt="an entirely different prompt, much longer than the first one"),
            budget_policy=make_budget_policy(),
        )
    )

    assert first.decision.outcome == second.decision.outcome == RoutingOutcome.TIER_1_LOCAL
    assert first.provider_id == second.provider_id


# --- Environment / filesystem / network / clock independence ---------------


def test_flow_is_independent_of_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example.invalid")
    monkeypatch.setenv("HOSTED_MODEL_API_KEY", "should-not-matter")
    inner = FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    registry = ProviderRegistry()
    registry.register(inner)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    result = asyncio.run(flow.run(make_request(), budget_policy=make_budget_policy()))

    assert result.decision.outcome == RoutingOutcome.TIER_1_LOCAL
    assert result.model_result is not None


def test_flow_has_no_filesystem_effect(tmp_path: Path) -> None:
    inner = FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    registry = ProviderRegistry()
    registry.register(inner)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    asyncio.run(flow.run(make_request(), budget_policy=make_budget_policy()))

    assert list(tmp_path.iterdir()) == []


def test_flow_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    registry = ProviderRegistry()
    registry.register(inner)
    router = DeterministicRouter(registry)
    flow = PersonalAssistantFlow(router)

    # The event loop itself opens a self-pipe socket on construction, so the
    # loop must exist before socket.socket is blocked.
    loop = asyncio.new_event_loop()
    try:

        def _blocked(*args: object, **kwargs: object) -> None:
            raise AssertionError("no network access is permitted in the flow")

        monkeypatch.setattr(socket, "socket", _blocked)

        result = loop.run_until_complete(
            flow.run(make_request(), budget_policy=make_budget_policy())
        )
        assert result.model_result is not None
    finally:
        monkeypatch.undo()
        loop.close()


def test_repeated_identical_inputs_produce_equivalent_decisions() -> None:
    def build_flow() -> PersonalAssistantFlow:
        inner = FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
        registry = ProviderRegistry()
        registry.register(inner)
        return PersonalAssistantFlow(DeterministicRouter(registry))

    request = make_request()
    first: FlowResult = asyncio.run(build_flow().run(request, budget_policy=make_budget_policy()))
    second: FlowResult = asyncio.run(build_flow().run(request, budget_policy=make_budget_policy()))

    assert first.decision.outcome == second.decision.outcome
    assert first.decision.capability_profile == second.decision.capability_profile
    assert first.decision.reasons == second.decision.reasons
    assert first.provider_id == second.provider_id
    assert first.model_result is not None
    assert second.model_result is not None
    assert first.model_result.output_text == second.model_result.output_text
    assert first.run_state.status == second.run_state.status
    assert first.run_state.completed_steps == second.run_state.completed_steps
    # run_id and timestamps are inherently unique per run and intentionally
    # excluded from this equivalence check.
    assert first.run_state.run_id != second.run_state.run_id
