from __future__ import annotations

import os
from typing import Any

from crewai.flow.flow import Flow, FlowState, start

from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelRequest
from personal_lms.flows.personal_assistant import PersonalAssistantFlow
from personal_lms.policies.errors import RoutingError
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers.errors import (
    ProviderContractError,
    ProviderError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def _apply_offline_defaults() -> None:
    """Keep CrewAI's own network/telemetry behavior off by default.

    ``OTEL_SDK_DISABLED`` alone does not cover everything: CrewAI also
    performs a PyPI network call to check for a newer version and persists
    a tracing-consent preference, independently of the OTel setting. These
    are ``setdefault`` calls, not overrides — an operator who has
    deliberately opted into tracing or version checks via their real
    environment is respected.
    """
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    os.environ.setdefault("CREWAI_DISABLE_VERSION_CHECK", "true")
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    os.environ.setdefault("CREWAI_DISABLE_TRACKING", "true")


class PersonalAssistantFlowState(FlowState):
    """Audit-safe projection of a run, for CrewAI's Flow state only.

    This is a projection, not a second source of truth: the domain
    ``RunState`` (Commit 5) remains authoritative and lives on the wrapped
    ``PersonalAssistantFlow`` instance. Every field here is a plain
    identifier or enum value — never a prompt, source-document excerpt,
    credential, path, or unrestricted metadata dictionary.
    """

    request_id: str | None = None
    run_id: str | None = None
    run_status: str | None = None
    routing_outcome: str | None = None
    provider_id: str | None = None
    error_type: str | None = None


class CrewAIPersonalAssistantFlow(Flow[PersonalAssistantFlowState]):
    """CrewAI orchestration adapter over the framework-neutral ``PersonalAssistantFlow``.

    This class exists only to prove the existing, independently-tested
    routing and execution logic can run through a real CrewAI ``Flow``
    boundary. It contains no routing algorithm, no provider-selection
    logic, and no domain-schema construction of its own — every one of
    those decisions is delegated, unchanged, to ``PersonalAssistantFlow``.
    No CrewAI ``Agent``, ``Crew``, ``Task``, or ``LLM`` is instantiated
    here or reachable from here.
    """

    def __init__(
        self,
        router: DeterministicRouter,
        request: ModelRequest,
        budget_policy: BudgetPolicy,
        *,
        deterministic_capable: bool = False,
        requires_reasoning: bool = False,
        local_only: bool = False,
        max_cost_class: CostClass = CostClass.HIGH,
        max_latency_class: LatencyClass = LatencyClass.BATCH,
        **kwargs: Any,
    ) -> None:
        _apply_offline_defaults()
        kwargs.setdefault("tracing", False)
        kwargs.setdefault("suppress_flow_events", True)
        super().__init__(**kwargs)

        self._app_flow = PersonalAssistantFlow(router)
        self._request = request
        self._budget_policy = budget_policy
        self._deterministic_capable = deterministic_capable
        self._requires_reasoning = requires_reasoning
        self._local_only = local_only
        self._max_cost_class = max_cost_class
        self._max_latency_class = max_latency_class

    @start()
    async def run_personal_assistant(self) -> None:
        """Delegate exactly once to ``PersonalAssistantFlow.run()``.

        Never inspects ``self._request.prompt``; every routing preference
        is an explicit constructor argument, exactly as
        ``PersonalAssistantFlow.run()`` and ``DeterministicRouter.route()``
        already require (see Commits 4-5). Re-raises the original
        ``RoutingError``/``ProviderError`` unchanged on failure — this
        adapter adds no retry, fallback, or error translation.
        """
        self.state.request_id = str(self._request.request_id)

        try:
            result = await self._app_flow.run(
                self._request,
                budget_policy=self._budget_policy,
                deterministic_capable=self._deterministic_capable,
                requires_reasoning=self._requires_reasoning,
                local_only=self._local_only,
                max_cost_class=self._max_cost_class,
                max_latency_class=self._max_latency_class,
            )
        except (
            RoutingError,
            ProviderUnavailableError,
            ProviderTimeoutError,
            ProviderExecutionError,
            ProviderContractError,
            ProviderError,
        ) as exc:
            run_state = self._app_flow.run_state
            if run_state is not None:
                self.state.run_id = str(run_state.run_id)
                self.state.run_status = run_state.status.value
            self.state.error_type = type(exc).__name__
            raise

        self.state.run_id = str(result.run_state.run_id)
        self.state.run_status = result.run_state.status.value
        self.state.routing_outcome = result.decision.outcome.value
        self.state.provider_id = result.provider_id
