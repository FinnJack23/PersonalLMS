from __future__ import annotations

from dataclasses import dataclass, replace

from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.enums import CostClass, LatencyClass, RoutingOutcome
from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.routing import RoutingDecision
from personal_lms.policies.errors import (
    BudgetPolicyDeniedError,
    LocalProviderRequiredError,
    NoCompatibleProviderError,
    PrivacyPolicyDeniedError,
)
from personal_lms.providers.protocol import ModelProvider
from personal_lms.providers.registry import CapabilityFilter, ProviderRegistry


def _rank[EnumT: (LatencyClass, CostClass, PrivacyClassification)](
    enum_cls: type[EnumT], member: EnumT
) -> int:
    """Position of ``member`` in its enum's declaration order.

    Mirrors ``personal_lms.providers.registry._rank``. Duplicated locally
    (rather than imported) so the router does not depend on the registry's
    private internals — see the Commit 4 boundary note in the module
    docstring below.
    """
    return list(enum_cls).index(member)


def _profile_satisfies(
    profile: ModelCapabilityProfile, capability_filter: CapabilityFilter
) -> bool:
    """Whether a single profile meets a capability filter's thresholds.

    The registry's ``find()`` already confirms a *provider* qualifies (at
    least one of its profiles matches). The router re-checks per profile so
    it can identify which specific profile qualified — a provider's
    ``capability_profiles`` may mix qualifying and non-qualifying entries —
    for ranking and for the ``RoutingDecision.capability_profile`` it
    records.
    """
    if capability_filter.requires_reasoning and not profile.supports_reasoning:
        return False
    if capability_filter.requires_vision and not profile.supports_vision:
        return False
    if profile.max_context_tokens < capability_filter.min_context_tokens:
        return False
    if capability_filter.privacy_classification is not None and _rank(
        PrivacyClassification, profile.max_privacy_classification
    ) < _rank(PrivacyClassification, capability_filter.privacy_classification):
        return False
    if capability_filter.latency_class is not None and _rank(
        LatencyClass, profile.latency_class
    ) > _rank(LatencyClass, capability_filter.latency_class):
        return False
    if capability_filter.cost_class is None:
        return True
    return _rank(CostClass, profile.cost_class) <= _rank(CostClass, capability_filter.cost_class)


def _best_profile(
    provider: ModelProvider, capability_filter: CapabilityFilter
) -> ModelCapabilityProfile | None:
    """The cheapest, then fastest, then lowest-profile-id qualifying profile.

    Order-independent with respect to ``provider.capability_profiles`` —
    ``min()`` over a fully-specified key never depends on input order.
    """
    matching = [p for p in provider.capability_profiles if _profile_satisfies(p, capability_filter)]
    if not matching:
        return None
    return min(
        matching,
        key=lambda p: (
            _rank(CostClass, p.cost_class),
            _rank(LatencyClass, p.latency_class),
            p.profile_id,
        ),
    )


@dataclass(frozen=True, slots=True)
class _ScoredCandidate:
    cost_rank: int
    latency_rank: int
    provider_id: str
    provider: ModelProvider
    profile: ModelCapabilityProfile


def _select_best(
    candidates: tuple[ModelProvider, ...], capability_filter: CapabilityFilter
) -> tuple[ModelProvider, ModelCapabilityProfile, list[str]]:
    """Rank already-qualified candidates and pick exactly one, deterministically.

    Precedence: cheapest cost class, then fastest latency class, then
    ``provider_id`` as the final explicit tie-breaker. Never depends on
    registration order, dict order, or capability-profile order — the sort
    key is fully determined by ``(cost_rank, latency_rank, provider_id)``.
    """
    scored = [
        _ScoredCandidate(
            cost_rank=_rank(CostClass, profile.cost_class),
            latency_rank=_rank(LatencyClass, profile.latency_class),
            provider_id=provider.provider_id,
            provider=provider,
            profile=profile,
        )
        for provider in candidates
        if (profile := _best_profile(provider, capability_filter)) is not None
    ]
    scored.sort(key=lambda c: (c.cost_rank, c.latency_rank, c.provider_id))
    winner = scored[0]
    fallback_profiles = [c.profile.profile_id for c in scored[1:]]
    return winner.provider, winner.profile, fallback_profiles


@dataclass(frozen=True, slots=True)
class RoutingResult:
    """Router output: the audit decision, plus the concrete provider to use.

    ``provider`` is ``None`` exactly when ``decision.outcome`` is
    ``tier_0_deterministic`` or ``approval_required`` — no execution should
    follow without further action in either case. This pairing (rather than
    adding a provider identifier to ``RoutingDecision``) is what keeps the
    domain schema vendor-neutral per ADR-0002 while still letting the
    router hand back a concrete, executable provider.
    """

    decision: RoutingDecision
    provider: ModelProvider | None


class DeterministicRouter:
    """Pure, stateless routing policy over a ``ProviderRegistry``.

    No network, filesystem, environment, clock, or random-selection
    dependency. The registry narrows structurally compatible candidates
    (``ProviderRegistry.find``); this class owns policy evaluation,
    ranking, selection, and ``RoutingDecision`` construction — see
    ``docs/product-specs/MODEL_ROUTER_AND_COST_CONTROLS.md`` for the
    decision order this mirrors, restricted to what can be decided without
    executing a request, retrieving RAG evidence, or checking provider
    health (later commits).
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def route(
        self,
        request: ModelRequest,
        *,
        budget_policy: BudgetPolicy,
        deterministic_capable: bool = False,
        requires_reasoning: bool = False,
        local_only: bool = False,
        max_cost_class: CostClass = CostClass.HIGH,
        max_latency_class: LatencyClass = LatencyClass.BATCH,
    ) -> RoutingResult:
        """Decide how ``request`` should be executed.

        ``deterministic_capable`` is a caller-supplied signal — set when a
        Tier 0 Python service can already complete the task — never
        inferred from ``request.prompt`` content. ``requires_reasoning``,
        ``local_only``, ``max_cost_class``, and ``max_latency_class`` are
        likewise caller-supplied routing preferences with no matching field
        on ``ModelRequest`` (see the Commit 4 report for why these are
        plain parameters rather than domain-schema changes).
        """
        if deterministic_capable:
            decision = RoutingDecision(
                outcome=RoutingOutcome.TIER_0_DETERMINISTIC,
                reasons=["deterministic_task_declared"],
            )
            return RoutingResult(decision=decision, provider=None)

        if not self._registry.list_providers():
            decision = RoutingDecision(
                outcome=RoutingOutcome.REJECTED,
                reasons=["provider_registry_empty"],
            )
            raise NoCompatibleProviderError(decision)

        privacy_forces_local = (
            request.privacy_classification is PrivacyClassification.RESTRICTED_LOCAL_ONLY
        )
        explicit_local_only = local_only or budget_policy.local_only
        effective_local_only = explicit_local_only or privacy_forces_local

        capability_filter = CapabilityFilter(
            requires_reasoning=requires_reasoning,
            requires_vision=request.requires_vision,
            min_context_tokens=request.context_token_estimate,
            local_only=effective_local_only,
            privacy_classification=request.privacy_classification,
            latency_class=max_latency_class,
            cost_class=max_cost_class,
        )
        matches = self._registry.find(capability_filter)

        if not matches:
            if privacy_forces_local:
                relaxed = replace(capability_filter, local_only=explicit_local_only)
                if self._registry.find(relaxed):
                    decision = RoutingDecision(
                        outcome=RoutingOutcome.REJECTED,
                        reasons=[
                            "privacy_policy_prevents_hosted_routing",
                            f"privacy_classification={request.privacy_classification.value}",
                        ],
                    )
                    raise PrivacyPolicyDeniedError(decision)
            if effective_local_only:
                decision = RoutingDecision(
                    outcome=RoutingOutcome.REJECTED,
                    reasons=["local_provider_required_but_unavailable"],
                )
                raise LocalProviderRequiredError(decision)
            decision = RoutingDecision(
                outcome=RoutingOutcome.REJECTED,
                reasons=["no_compatible_provider"],
            )
            raise NoCompatibleProviderError(decision)

        local_matches = tuple(p for p in matches if p.is_local)
        if local_matches:
            provider, profile, fallback_profiles = _select_best(local_matches, capability_filter)
            reasons = ["local_candidate_available"]
            reasons.append(
                "local_only_required" if effective_local_only else "local_preferred_over_hosted"
            )
            decision = RoutingDecision(
                outcome=RoutingOutcome.TIER_1_LOCAL,
                capability_profile=profile.profile_id,
                reasons=reasons,
                fallback_profiles=fallback_profiles,
            )
            return RoutingResult(decision=decision, provider=provider)

        hosted_matches = tuple(p for p in matches if not p.is_local)

        # budget_policy.local_only is already folded into effective_local_only
        # above, so if it were True, hosted_matches would always be empty and
        # this branch would be unreachable — no need to recheck it here.
        # BudgetPolicy also enforces monthly_limit_usd >= daily_limit_usd, so
        # a zero monthly limit already implies a zero daily limit — checking
        # daily_limit_usd alone covers both.
        if budget_policy.daily_limit_usd == 0:
            decision = RoutingDecision(
                outcome=RoutingOutcome.REJECTED,
                reasons=[
                    "budget_policy_blocks_hosted_routing",
                    f"budget_policy_id={budget_policy.policy_id}",
                ],
            )
            raise BudgetPolicyDeniedError(decision)

        if budget_policy.automatic_single_call_limit_usd == 0:
            decision = RoutingDecision(
                outcome=RoutingOutcome.APPROVAL_REQUIRED,
                approval_required=True,
                reasons=[
                    "hosted_routing_requires_approval",
                    f"budget_policy_id={budget_policy.policy_id}",
                ],
            )
            return RoutingResult(decision=decision, provider=None)

        provider, profile, fallback_profiles = _select_best(hosted_matches, capability_filter)
        decision = RoutingDecision(
            outcome=RoutingOutcome.TIER_2_HOSTED,
            capability_profile=profile.profile_id,
            redaction_required=True,
            reasons=["hosted_escalation_selected", "no_local_candidate_available"],
            fallback_profiles=fallback_profiles,
        )
        return RoutingResult(decision=decision, provider=provider)
