from __future__ import annotations

import importlib.util
import socket
from decimal import Decimal
from pathlib import Path

import pytest

if importlib.util.find_spec("crewai") is None:
    pytest.skip("crewai extra not installed (uv sync --extra crewai)", allow_module_level=True)

from personal_lms.adapters.crewai import CrewAIPersonalAssistantFlow
from personal_lms.domain.enums import CostClass, RoutingOutcome, RunStatus
from personal_lms.flows.personal_assistant import PersonalAssistantFlow
from personal_lms.policies import DeterministicRouter, NoCompatibleProviderError
from personal_lms.providers import FakeHostedProvider, FakeLocalProvider
from personal_lms.providers.errors import ProviderExecutionError
from personal_lms.providers.registry import ProviderRegistry

from ._helpers import (
    CountingProvider,
    CountingRouter,
    make_budget_policy,
    make_profile,
    make_request,
)

pytestmark = pytest.mark.requires_crewai

# --- Framework boundary -----------------------------------------------------


def test_flow_is_backed_by_crewai_flow_abstraction() -> None:
    from crewai.flow.flow import Flow

    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    assert isinstance(flow, Flow)


def test_flow_delegates_to_the_framework_neutral_personal_assistant_flow() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    assert isinstance(flow._app_flow, PersonalAssistantFlow)


def test_framework_neutral_flow_remains_directly_usable_without_crewai() -> None:
    """Regression guard: PersonalAssistantFlow.run() must still work standalone,
    proving the adapter did not fold routing/execution logic into itself."""
    import asyncio

    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    router = DeterministicRouter(registry)
    plain_flow = PersonalAssistantFlow(router)

    result = asyncio.run(plain_flow.run(make_request(), budget_policy=make_budget_policy()))

    assert result.decision.outcome == RoutingOutcome.TIER_1_LOCAL
    assert result.provider_id == "solo"


# --- Outcome handling --------------------------------------------------------


def test_tier_0_completes_without_registry_or_provider_access() -> None:
    router = DeterministicRouter(ProviderRegistry())  # empty registry
    flow = CrewAIPersonalAssistantFlow(
        router, make_request(), make_budget_policy(), deterministic_capable=True
    )

    flow.kickoff()

    assert flow.state.routing_outcome == RoutingOutcome.TIER_0_DETERMINISTIC.value
    assert flow.state.provider_id is None
    assert flow.state.run_status == RunStatus.COMPLETED.value


def test_tier_1_local_uses_fake_local_provider_exactly_once() -> None:
    inner = FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    counting = CountingProvider(inner=inner)
    registry = ProviderRegistry()
    registry.register(counting)
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    flow.kickoff()

    assert flow.state.routing_outcome == RoutingOutcome.TIER_1_LOCAL.value
    assert flow.state.provider_id == "solo"
    assert counting.call_count == 1


def test_tier_2_hosted_uses_fake_hosted_provider_exactly_once() -> None:
    inner = FakeHostedProvider(
        "hosted", capability_profiles=(make_profile(profile_id="h", is_local=False),)
    )
    counting = CountingProvider(inner=inner)
    registry = ProviderRegistry()
    registry.register(counting)
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    flow.kickoff()

    assert flow.state.routing_outcome == RoutingOutcome.TIER_2_HOSTED.value
    assert flow.state.provider_id == "hosted"
    assert counting.call_count == 1


def test_approval_required_reaches_waiting_for_approval_without_generation() -> None:
    inner = FakeHostedProvider(
        "hosted", capability_profiles=(make_profile(profile_id="h", is_local=False),)
    )
    counting = CountingProvider(inner=inner)
    registry = ProviderRegistry()
    registry.register(counting)
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(
        router,
        make_request(),
        make_budget_policy(automatic_single_call_limit_usd=Decimal("0")),
    )

    flow.kickoff()

    assert flow.state.routing_outcome == RoutingOutcome.APPROVAL_REQUIRED.value
    assert flow.state.run_status == RunStatus.WAITING_FOR_APPROVAL.value
    assert flow.state.provider_id is None
    assert counting.call_count == 0


def test_rejected_routing_preserves_the_original_routing_error() -> None:
    router = DeterministicRouter(ProviderRegistry())  # empty -> no compatible provider
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    with pytest.raises(NoCompatibleProviderError):
        flow.kickoff()

    assert flow.state.error_type == "NoCompatibleProviderError"
    assert flow.state.provider_id is None


def test_provider_error_preserves_the_original_subtype() -> None:
    inner = FakeLocalProvider(
        "solo",
        capability_profiles=(make_profile(profile_id="p"),),
        fail_with=ProviderExecutionError("solo", "simulated failure"),
    )
    registry = ProviderRegistry()
    registry.register(inner)
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    with pytest.raises(ProviderExecutionError):
        flow.kickoff()

    assert flow.state.error_type == "ProviderExecutionError"


# --- Exactly-once / at-most-once ---------------------------------------------


def test_routing_occurs_exactly_once() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    inner_router = DeterministicRouter(registry)
    counting_router = CountingRouter(inner=inner_router)
    flow = CrewAIPersonalAssistantFlow(counting_router, make_request(), make_budget_policy())  # type: ignore[arg-type]

    flow.kickoff()

    assert counting_router.call_count == 1


def test_provider_generation_occurs_at_most_once_and_never_falls_back() -> None:
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
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    with pytest.raises(ProviderExecutionError):
        flow.kickoff()

    assert failing.call_count == 1
    assert healthy.call_count == 0


# --- State safety -------------------------------------------------------------


def test_crewai_state_contains_only_audit_safe_fields() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    flow.kickoff()

    assert set(flow.state.model_fields.keys()) == {
        "id",  # CrewAI's own flow-instance identifier, not a domain field
        "request_id",
        "run_id",
        "run_status",
        "routing_outcome",
        "provider_id",
        "error_type",
    }


def test_prompt_text_never_appears_in_state_or_errors() -> None:
    secret = "correct horse battery staple do-not-leak"
    inner = FakeLocalProvider(
        "solo",
        capability_profiles=(make_profile(profile_id="p"),),
        fail_with=ProviderExecutionError("solo", "boom"),
    )
    registry = ProviderRegistry()
    registry.register(inner)
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(prompt=secret), make_budget_policy())

    with pytest.raises(ProviderExecutionError) as exc_info:
        flow.kickoff()

    assert secret not in str(exc_info.value)
    assert secret not in flow.state.model_dump_json()
    assert flow._app_flow.run_state is not None
    assert secret not in flow._app_flow.run_state.model_dump_json()
    for step in flow._app_flow.run_state.completed_steps:
        assert secret not in step


def test_prompt_text_absent_on_successful_run() -> None:
    secret = "correct horse battery staple do-not-leak"
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(prompt=secret), make_budget_policy())

    flow.kickoff()

    assert secret not in flow.state.model_dump_json()


# --- No CrewAI Agent/Crew/Task/LLM ------------------------------------------


def test_no_agent_crew_task_or_llm_is_instantiated(monkeypatch: pytest.MonkeyPatch) -> None:
    import crewai

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not instantiate this CrewAI class in this commit")

    monkeypatch.setattr(crewai.Agent, "__init__", _blocked)
    monkeypatch.setattr(crewai.Crew, "__init__", _blocked)
    monkeypatch.setattr(crewai.Task, "__init__", _blocked)
    monkeypatch.setattr(crewai.LLM, "__init__", _blocked)

    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    flow.kickoff()

    assert flow.state.routing_outcome == RoutingOutcome.TIER_1_LOCAL.value


# --- Isolation: network, filesystem, environment ----------------------------


def test_no_filesystem_effect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Blocks Path.write_text/write_bytes globally — the exact methods
    CrewAI's telemetry/user-data and version-cache modules use — rather
    than only checking an unrelated scratch directory. A pass here
    empirically proves the offline defaults prevent every write path
    discovered during development, not just the ones known in advance."""

    def _blocked(self: Path, *args: object, **kwargs: object) -> int:
        raise AssertionError(f"unexpected filesystem write to {self}")

    monkeypatch.setattr(Path, "write_text", _blocked)
    monkeypatch.setattr(Path, "write_bytes", _blocked)

    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    flow.kickoff()

    assert flow.state.routing_outcome == RoutingOutcome.TIER_1_LOCAL.value
    assert list(tmp_path.iterdir()) == []


def test_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blocks socket.create_connection specifically — the function urllib
    (and therefore CrewAI's PyPI version-check) uses for outbound TCP
    connections — rather than socket.socket itself, which asyncio's own
    event-loop machinery legitimately needs for its loopback self-pipe.
    A pass here also empirically proves the offline defaults applied in
    CrewAIPersonalAssistantFlow.__init__ actually prevent the version-check
    network call this adapter discovered during development."""

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("no outbound network connection is permitted")

    monkeypatch.setattr(socket, "create_connection", _blocked)

    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    flow.kickoff()

    assert flow.state.routing_outcome == RoutingOutcome.TIER_1_LOCAL.value


def test_independent_of_environment_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example.invalid")
    monkeypatch.setenv("HOSTED_MODEL_API_KEY", "should-not-matter")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "/should/not/be/read")

    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    router = DeterministicRouter(registry)
    flow = CrewAIPersonalAssistantFlow(router, make_request(), make_budget_policy())

    flow.kickoff()

    assert flow.state.routing_outcome == RoutingOutcome.TIER_1_LOCAL.value
