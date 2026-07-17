from __future__ import annotations

from decimal import Decimal

import pytest

from personal_lms.domain.enums import CostClass, LatencyClass, RoutingOutcome
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.policies import (
    BudgetPolicyDeniedError,
    DeterministicRouter,
    LocalProviderRequiredError,
    NoCompatibleProviderError,
    PrivacyPolicyDeniedError,
    RoutingError,
)
from personal_lms.providers import FakeHostedProvider, FakeLocalProvider
from personal_lms.providers.registry import ProviderRegistry

from ._helpers import make_budget_policy, make_profile, make_request

# --- Tier 0 ------------------------------------------------------------


def test_deterministic_capable_short_circuits_to_tier_0() -> None:
    router = DeterministicRouter(ProviderRegistry())

    result = router.route(
        make_request(), budget_policy=make_budget_policy(), deterministic_capable=True
    )

    assert result.decision.outcome == RoutingOutcome.TIER_0_DETERMINISTIC
    assert result.decision.capability_profile is None
    assert result.provider is None


# --- Basic selection -----------------------------------------------------


def test_single_compatible_provider_is_selected() -> None:
    registry = ProviderRegistry()
    provider = FakeLocalProvider(
        "only", capability_profiles=(make_profile(profile_id="only-profile"),)
    )
    registry.register(provider)
    router = DeterministicRouter(registry)

    result = router.route(make_request(), budget_policy=make_budget_policy())

    assert result.decision.outcome == RoutingOutcome.TIER_1_LOCAL
    assert result.decision.capability_profile == "only-profile"
    assert result.provider is provider


def test_cheapest_provider_wins_among_multiple() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "expensive",
            capability_profiles=(make_profile(profile_id="e", cost_class=CostClass.MEDIUM),),
        )
    )
    registry.register(
        FakeLocalProvider(
            "cheap", capability_profiles=(make_profile(profile_id="c", cost_class=CostClass.FREE),)
        )
    )
    router = DeterministicRouter(registry)

    result = router.route(make_request(), budget_policy=make_budget_policy())

    assert result.provider is not None
    assert result.provider.provider_id == "cheap"
    assert result.decision.fallback_profiles == ["e"]


def test_fastest_provider_wins_when_cost_tied() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "slow",
            capability_profiles=(
                make_profile(
                    profile_id="s", cost_class=CostClass.LOW, latency_class=LatencyClass.BATCH
                ),
            ),
        )
    )
    registry.register(
        FakeLocalProvider(
            "fast",
            capability_profiles=(
                make_profile(
                    profile_id="f", cost_class=CostClass.LOW, latency_class=LatencyClass.INTERACTIVE
                ),
            ),
        )
    )
    router = DeterministicRouter(registry)

    result = router.route(make_request(), budget_policy=make_budget_policy())

    assert result.provider is not None
    assert result.provider.provider_id == "fast"


def test_provider_id_is_final_tiebreaker() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("zeta", capability_profiles=(make_profile(profile_id="z"),))
    )
    registry.register(
        FakeLocalProvider("alpha", capability_profiles=(make_profile(profile_id="a"),))
    )
    router = DeterministicRouter(registry)

    result = router.route(make_request(), budget_policy=make_budget_policy())

    assert result.provider is not None
    assert result.provider.provider_id == "alpha"


@pytest.mark.parametrize("registration_order", [("alpha", "zeta"), ("zeta", "alpha")])
def test_selection_is_independent_of_registration_order(
    registration_order: tuple[str, str],
) -> None:
    providers = {
        "alpha": FakeLocalProvider("alpha", capability_profiles=(make_profile(profile_id="a"),)),
        "zeta": FakeLocalProvider("zeta", capability_profiles=(make_profile(profile_id="z"),)),
    }
    registry = ProviderRegistry()
    for name in registration_order:
        registry.register(providers[name])
    router = DeterministicRouter(registry)

    result = router.route(make_request(), budget_policy=make_budget_policy())

    assert result.provider is not None
    assert result.provider.provider_id == "alpha"


@pytest.mark.parametrize("profile_order", [("cheap", "pricey"), ("pricey", "cheap")])
def test_selection_is_independent_of_capability_profile_order(
    profile_order: tuple[str, str],
) -> None:
    profiles = {
        "cheap": make_profile(profile_id="cheap", cost_class=CostClass.FREE),
        "pricey": make_profile(profile_id="pricey", cost_class=CostClass.HIGH),
    }
    ordered = tuple(profiles[name] for name in profile_order)
    registry = ProviderRegistry()
    registry.register(FakeLocalProvider("solo", capability_profiles=ordered))
    router = DeterministicRouter(registry)

    result = router.route(make_request(), budget_policy=make_budget_policy())

    assert result.decision.capability_profile == "cheap"


# --- Local vs hosted -----------------------------------------------------


def test_local_preferred_over_hosted_even_when_hosted_is_cheaper() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "local",
            capability_profiles=(make_profile(profile_id="l", cost_class=CostClass.MEDIUM),),
        )
    )
    registry.register(
        FakeHostedProvider(
            "hosted",
            capability_profiles=(
                make_profile(profile_id="h", is_local=False, cost_class=CostClass.FREE),
            ),
        )
    )
    router = DeterministicRouter(registry)

    result = router.route(make_request(), budget_policy=make_budget_policy())

    assert result.decision.outcome == RoutingOutcome.TIER_1_LOCAL
    assert result.provider is not None
    assert result.provider.provider_id == "local"


def test_hosted_selected_when_no_local_candidate() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeHostedProvider(
            "hosted", capability_profiles=(make_profile(profile_id="h", is_local=False),)
        )
    )
    router = DeterministicRouter(registry)

    result = router.route(make_request(), budget_policy=make_budget_policy())

    assert result.decision.outcome == RoutingOutcome.TIER_2_HOSTED
    assert result.decision.redaction_required is True
    assert result.provider is not None
    assert result.provider.provider_id == "hosted"


def test_local_only_requests_never_select_hosted_providers() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeHostedProvider(
            "hosted", capability_profiles=(make_profile(profile_id="h", is_local=False),)
        )
    )
    router = DeterministicRouter(registry)

    with pytest.raises(LocalProviderRequiredError):
        router.route(make_request(), budget_policy=make_budget_policy(), local_only=True)


# --- Privacy boundaries ----------------------------------------------------


@pytest.mark.parametrize(
    ("provider_max_privacy", "request_privacy", "should_match"),
    [
        (PrivacyClassification.PUBLIC, PrivacyClassification.PUBLIC, True),
        (PrivacyClassification.PUBLIC, PrivacyClassification.INTERNAL, False),
        (PrivacyClassification.INTERNAL, PrivacyClassification.PUBLIC, True),
        (PrivacyClassification.INTERNAL, PrivacyClassification.INTERNAL, True),
        (PrivacyClassification.INTERNAL, PrivacyClassification.SENSITIVE, False),
        (PrivacyClassification.SENSITIVE, PrivacyClassification.SENSITIVE, True),
        (PrivacyClassification.SENSITIVE, PrivacyClassification.RESTRICTED_LOCAL_ONLY, False),
        (
            PrivacyClassification.RESTRICTED_LOCAL_ONLY,
            PrivacyClassification.RESTRICTED_LOCAL_ONLY,
            True,
        ),
    ],
)
def test_privacy_classification_boundary(
    provider_max_privacy: PrivacyClassification,
    request_privacy: PrivacyClassification,
    should_match: bool,
) -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "solo",
            capability_profiles=(
                make_profile(profile_id="p", max_privacy_classification=provider_max_privacy),
            ),
        )
    )
    router = DeterministicRouter(registry)
    request = make_request(privacy_classification=request_privacy)

    if should_match:
        result = router.route(request, budget_policy=make_budget_policy())
        assert result.provider is not None
    else:
        with pytest.raises(RoutingError):
            router.route(request, budget_policy=make_budget_policy())


def test_restricted_local_only_privacy_forces_local_routing_regardless_of_flag() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "local",
            capability_profiles=(
                make_profile(
                    profile_id="l",
                    max_privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
                ),
            ),
        )
    )
    registry.register(
        FakeHostedProvider(
            "hosted",
            capability_profiles=(
                make_profile(profile_id="h", is_local=False, cost_class=CostClass.FREE),
            ),
        )
    )
    router = DeterministicRouter(registry)
    request = make_request(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY)

    result = router.route(request, budget_policy=make_budget_policy(), local_only=False)

    assert result.decision.outcome == RoutingOutcome.TIER_1_LOCAL
    assert result.provider is not None
    assert result.provider.provider_id == "local"


def test_privacy_policy_denied_when_only_a_hosted_candidate_would_otherwise_qualify() -> None:
    """No local candidate is registered at all, so privacy — not local
    availability — is specifically what blocks routing to the hosted
    candidate that would otherwise have qualified."""
    registry = ProviderRegistry()
    registry.register(
        FakeHostedProvider(
            "hosted",
            capability_profiles=(
                make_profile(profile_id="h", is_local=False, cost_class=CostClass.FREE),
            ),
        )
    )
    router = DeterministicRouter(registry)
    request = make_request(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY)

    with pytest.raises(PrivacyPolicyDeniedError) as exc_info:
        router.route(request, budget_policy=make_budget_policy())

    assert exc_info.value.decision.outcome == RoutingOutcome.REJECTED
    assert "privacy_policy_prevents_hosted_routing" in exc_info.value.decision.reasons


# --- Cost-class boundaries ----------------------------------------------


@pytest.mark.parametrize(
    ("provider_cost", "ceiling", "should_match"),
    [
        (CostClass.FREE, CostClass.FREE, True),
        (CostClass.LOW, CostClass.FREE, False),
        (CostClass.FREE, CostClass.HIGH, True),
        (CostClass.MEDIUM, CostClass.MEDIUM, True),
        (CostClass.HIGH, CostClass.MEDIUM, False),
    ],
)
def test_cost_class_boundary(
    provider_cost: CostClass, ceiling: CostClass, should_match: bool
) -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "solo", capability_profiles=(make_profile(profile_id="p", cost_class=provider_cost),)
        )
    )
    router = DeterministicRouter(registry)

    if should_match:
        result = router.route(
            make_request(), budget_policy=make_budget_policy(), max_cost_class=ceiling
        )
        assert result.provider is not None
    else:
        with pytest.raises(RoutingError):
            router.route(make_request(), budget_policy=make_budget_policy(), max_cost_class=ceiling)


# --- Latency-class boundaries --------------------------------------------


@pytest.mark.parametrize(
    ("provider_latency", "ceiling", "should_match"),
    [
        (LatencyClass.INTERACTIVE, LatencyClass.INTERACTIVE, True),
        (LatencyClass.STANDARD, LatencyClass.INTERACTIVE, False),
        (LatencyClass.INTERACTIVE, LatencyClass.BATCH, True),
        (LatencyClass.BATCH, LatencyClass.STANDARD, False),
        (LatencyClass.STANDARD, LatencyClass.STANDARD, True),
    ],
)
def test_latency_class_boundary(
    provider_latency: LatencyClass, ceiling: LatencyClass, should_match: bool
) -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "solo",
            capability_profiles=(make_profile(profile_id="p", latency_class=provider_latency),),
        )
    )
    router = DeterministicRouter(registry)

    if should_match:
        result = router.route(
            make_request(), budget_policy=make_budget_policy(), max_latency_class=ceiling
        )
        assert result.provider is not None
    else:
        with pytest.raises(RoutingError):
            router.route(
                make_request(), budget_policy=make_budget_policy(), max_latency_class=ceiling
            )


# --- Context-size boundaries ----------------------------------------------


def test_context_size_exact_boundary_matches() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "solo", capability_profiles=(make_profile(profile_id="p", max_context_tokens=4096),)
        )
    )
    router = DeterministicRouter(registry)

    result = router.route(
        make_request(context_token_estimate=4096), budget_policy=make_budget_policy()
    )
    assert result.provider is not None


def test_context_size_one_below_boundary_fails() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "solo", capability_profiles=(make_profile(profile_id="p", max_context_tokens=4096),)
        )
    )
    router = DeterministicRouter(registry)

    with pytest.raises(RoutingError):
        router.route(make_request(context_token_estimate=4097), budget_policy=make_budget_policy())


# --- Reasoning / vision requirements --------------------------------------


def test_reasoning_requirement_filters_out_non_reasoning_providers() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "plain", capability_profiles=(make_profile(profile_id="p", supports_reasoning=False),)
        )
    )
    registry.register(
        FakeLocalProvider(
            "reasoner", capability_profiles=(make_profile(profile_id="r", supports_reasoning=True),)
        )
    )
    router = DeterministicRouter(registry)

    result = router.route(
        make_request(), budget_policy=make_budget_policy(), requires_reasoning=True
    )

    assert result.provider is not None
    assert result.provider.provider_id == "reasoner"


def test_vision_requirement_filters_out_non_vision_providers() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "plain", capability_profiles=(make_profile(profile_id="p", supports_vision=False),)
        )
    )
    registry.register(
        FakeLocalProvider(
            "seer", capability_profiles=(make_profile(profile_id="s", supports_vision=True),)
        )
    )
    router = DeterministicRouter(registry)

    result = router.route(make_request(requires_vision=True), budget_policy=make_budget_policy())

    assert result.provider is not None
    assert result.provider.provider_id == "seer"


# --- Mixed-compatibility profiles ------------------------------------------


def test_provider_with_mixed_compatible_and_incompatible_profiles() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "mixed",
            capability_profiles=(
                make_profile(
                    profile_id="too-small", max_context_tokens=100, cost_class=CostClass.FREE
                ),
                make_profile(
                    profile_id="big-enough", max_context_tokens=8192, cost_class=CostClass.MEDIUM
                ),
            ),
        )
    )
    router = DeterministicRouter(registry)

    result = router.route(
        make_request(context_token_estimate=4096), budget_policy=make_budget_policy()
    )

    assert result.decision.capability_profile == "big-enough"


# --- Budget behavior -------------------------------------------------------


def test_budget_local_only_blocks_hosted_and_raises_when_no_local_available() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeHostedProvider(
            "hosted", capability_profiles=(make_profile(profile_id="h", is_local=False),)
        )
    )
    router = DeterministicRouter(registry)

    with pytest.raises(LocalProviderRequiredError):
        router.route(make_request(), budget_policy=make_budget_policy(local_only=True))


def test_zero_daily_limit_blocks_hosted_routing() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeHostedProvider(
            "hosted", capability_profiles=(make_profile(profile_id="h", is_local=False),)
        )
    )
    router = DeterministicRouter(registry)

    with pytest.raises(BudgetPolicyDeniedError):
        router.route(
            make_request(),
            budget_policy=make_budget_policy(
                daily_limit_usd=Decimal("0"), monthly_limit_usd=Decimal("0")
            ),
        )


def test_zero_automatic_limit_requires_approval_instead_of_denial() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeHostedProvider(
            "hosted", capability_profiles=(make_profile(profile_id="h", is_local=False),)
        )
    )
    router = DeterministicRouter(registry)

    result = router.route(
        make_request(),
        budget_policy=make_budget_policy(automatic_single_call_limit_usd=Decimal("0")),
    )

    assert result.decision.outcome == RoutingOutcome.APPROVAL_REQUIRED
    assert result.decision.approval_required is True
    assert result.decision.capability_profile is None
    assert result.provider is None


def test_nonzero_automatic_limit_allows_hosted_routing() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeHostedProvider(
            "hosted", capability_profiles=(make_profile(profile_id="h", is_local=False),)
        )
    )
    router = DeterministicRouter(registry)

    result = router.route(make_request(), budget_policy=make_budget_policy())

    assert result.decision.outcome == RoutingOutcome.TIER_2_HOSTED
    assert result.provider is not None


# --- No-provider failures ---------------------------------------------------


def test_empty_registry_raises_no_compatible_provider_error() -> None:
    """An empty registry is a valid environment with nothing configured yet —
    not a malformed request — so it raises the same error type as any other
    unsatisfiable routing request, with a distinguishing reason."""
    router = DeterministicRouter(ProviderRegistry())

    with pytest.raises(NoCompatibleProviderError) as exc_info:
        router.route(make_request(), budget_policy=make_budget_policy())

    assert exc_info.value.decision.outcome == RoutingOutcome.REJECTED
    assert exc_info.value.decision.reasons == ["provider_registry_empty"]


def test_no_compatible_provider_when_none_match() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "tiny", capability_profiles=(make_profile(profile_id="t", max_context_tokens=10),)
        )
    )
    router = DeterministicRouter(registry)

    with pytest.raises(NoCompatibleProviderError):
        router.route(
            make_request(context_token_estimate=999_999), budget_policy=make_budget_policy()
        )


# --- Prompt-leak safety ------------------------------------------------------


def test_prompt_text_is_absent_from_successful_decision() -> None:
    secret = "correct horse battery staple do-not-leak"
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider("solo", capability_profiles=(make_profile(profile_id="p"),))
    )
    router = DeterministicRouter(registry)

    result = router.route(make_request(prompt=secret), budget_policy=make_budget_policy())

    assert secret not in result.decision.model_dump_json()


def test_prompt_text_is_absent_from_routing_errors() -> None:
    secret = "correct horse battery staple do-not-leak"
    router = DeterministicRouter(ProviderRegistry())

    with pytest.raises(NoCompatibleProviderError) as exc_info:
        router.route(make_request(prompt=secret), budget_policy=make_budget_policy())

    assert secret not in str(exc_info.value)
    assert secret not in exc_info.value.decision.model_dump_json()
