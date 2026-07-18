from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from personal_lms.domain.base import utcnow
from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.enums import CostClass, LatencyClass, RoutingOutcome, RunStatus
from personal_lms.domain.models import ModelRequest, ModelResult
from personal_lms.domain.routing import RoutingDecision
from personal_lms.domain.runs import RunState
from personal_lms.policies.errors import RoutingError
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers.errors import (
    ProviderContractError,
    ProviderError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def _mark_failed(run_state: RunState, exc: Exception) -> None:
    """Record a failure on the run's audit trail. Never carries prompt text."""
    run_state.status = RunStatus.FAILED
    run_state.error_message = f"{type(exc).__name__}: {exc}"
    run_state.updated_at = utcnow()


@dataclass(frozen=True, slots=True)
class FlowResult:
    """Personal Assistant Flow output for a successful (non-raising) run.

    ``provider_id`` and ``model_result`` live here rather than on
    ``RoutingDecision`` or the ``RunState`` schema — both domain schemas
    (Commit 2) stay unchanged and vendor-neutral (ADR-0002). This mirrors
    Commit 4's ``RoutingResult`` pairing one level up the call stack.
    """

    request_id: UUID
    run_state: RunState
    decision: RoutingDecision
    provider_id: str | None
    model_result: ModelResult | None


class PersonalAssistantFlow:
    """The smallest flow that routes a request and, when appropriate, executes it.

    Coordinates ``DeterministicRouter`` and a single selected
    ``ModelProvider`` only — no CrewAI, no HTTP, no persistence beyond an
    in-memory ``RunState``, no retry, no fallback, and no approval-workflow
    execution (those are later commits). ``router.route()`` and, when a
    provider is selected, ``provider.generate()`` are each called at most
    once per ``run()``.
    """

    def __init__(
        self, router: DeterministicRouter, *, workflow_name: str = "personal_assistant_v0"
    ) -> None:
        self._router = router
        self._workflow_name = workflow_name
        self.run_state: RunState | None = None

    async def run(
        self,
        request: ModelRequest,
        *,
        budget_policy: BudgetPolicy,
        deterministic_capable: bool = False,
        requires_reasoning: bool = False,
        local_only: bool = False,
        max_cost_class: CostClass = CostClass.HIGH,
        max_latency_class: LatencyClass = LatencyClass.BATCH,
    ) -> FlowResult:
        """Route ``request`` and execute it if routing selected a provider.

        Routing preferences (``requires_reasoning``, ``local_only``,
        ``max_cost_class``, ``max_latency_class``, ``deterministic_capable``)
        are explicit inputs, exactly as accepted by
        ``DeterministicRouter.route()`` — this flow never inspects
        ``request.prompt`` to infer them.

        Raises the original ``RoutingError`` or ``ProviderError`` subtype
        on failure rather than returning a result; ``self.run_state``
        remains inspectable either way.
        """
        run_state = RunState(workflow_name=self._workflow_name)
        self.run_state = run_state
        run_state.status = RunStatus.IN_PROGRESS
        run_state.updated_at = utcnow()

        try:
            routing_result = self._router.route(
                request,
                budget_policy=budget_policy,
                deterministic_capable=deterministic_capable,
                requires_reasoning=requires_reasoning,
                local_only=local_only,
                max_cost_class=max_cost_class,
                max_latency_class=max_latency_class,
            )
        except RoutingError as exc:
            _mark_failed(run_state, exc)
            raise

        decision = routing_result.decision
        run_state.completed_steps.append(f"routing_decided:{decision.outcome.value}")
        run_state.updated_at = utcnow()

        if decision.outcome is RoutingOutcome.APPROVAL_REQUIRED:
            run_state.status = RunStatus.WAITING_FOR_APPROVAL
            run_state.updated_at = utcnow()
            return FlowResult(
                request_id=request.request_id,
                run_state=run_state,
                decision=decision,
                provider_id=None,
                model_result=None,
            )

        if routing_result.provider is None:
            # tier_0_deterministic: no model call is needed at all.
            run_state.status = RunStatus.COMPLETED
            run_state.updated_at = utcnow()
            return FlowResult(
                request_id=request.request_id,
                run_state=run_state,
                decision=decision,
                provider_id=None,
                model_result=None,
            )

        provider = routing_result.provider
        run_state.completed_steps.append("provider_selected")
        run_state.updated_at = utcnow()

        try:
            model_result = await provider.generate(request)
        except ProviderTimeoutError as exc:
            _mark_failed(run_state, exc)
            raise
        except ProviderUnavailableError as exc:
            _mark_failed(run_state, exc)
            raise
        except ProviderExecutionError as exc:
            _mark_failed(run_state, exc)
            raise
        except ProviderContractError as exc:
            _mark_failed(run_state, exc)
            raise
        except ProviderError as exc:
            _mark_failed(run_state, exc)
            raise

        run_state.completed_steps.append("generation_completed")
        run_state.status = RunStatus.COMPLETED
        run_state.updated_at = utcnow()

        return FlowResult(
            request_id=request.request_id,
            run_state=run_state,
            decision=decision,
            provider_id=provider.provider_id,
            model_result=model_result,
        )
