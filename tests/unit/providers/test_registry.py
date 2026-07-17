from __future__ import annotations

import pytest

from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelCapabilityProfile
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.providers import (
    FakeHostedProvider,
    FakeLocalProvider,
    ProviderAlreadyRegisteredError,
    ProviderError,
    ProviderNotFoundError,
    ProviderRegistry,
)
from personal_lms.providers.registry import CapabilityFilter


def _profile(**overrides: object) -> ModelCapabilityProfile:
    defaults: dict[str, object] = {
        "profile_id": "p",
        "supports_reasoning": False,
        "supports_vision": False,
        "max_context_tokens": 4096,
        "is_local": True,
        "max_privacy_classification": PrivacyClassification.PUBLIC,
        "latency_class": LatencyClass.STANDARD,
        "cost_class": CostClass.LOW,
    }
    defaults.update(overrides)
    return ModelCapabilityProfile.model_validate(defaults)


@pytest.mark.parametrize(
    "error_cls",
    [ProviderNotFoundError, ProviderAlreadyRegisteredError],
)
def test_registry_errors_subclass_provider_error(error_cls: type[ProviderError]) -> None:
    assert issubclass(error_cls, ProviderError)


def test_register_and_get() -> None:
    registry = ProviderRegistry()
    provider = FakeLocalProvider("local-a")
    registry.register(provider)
    assert registry.get("local-a") is provider


def test_register_duplicate_raises() -> None:
    registry = ProviderRegistry()
    registry.register(FakeLocalProvider("local-a"))
    with pytest.raises(ProviderAlreadyRegisteredError):
        registry.register(FakeLocalProvider("local-a"))


def test_get_unknown_raises() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderNotFoundError):
        registry.get("does-not-exist")


def test_list_providers_is_deterministic_regardless_of_registration_order() -> None:
    registry = ProviderRegistry()
    registry.register(FakeLocalProvider("zeta"))
    registry.register(FakeLocalProvider("alpha"))
    registry.register(FakeHostedProvider("mid"))

    assert [p.provider_id for p in registry.list_providers()] == ["alpha", "mid", "zeta"]


def test_find_filters_by_reasoning_capability() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "reasoning", capability_profiles=(_profile(profile_id="r", supports_reasoning=True),)
        )
    )
    registry.register(FakeLocalProvider("plain", capability_profiles=(_profile(profile_id="p"),)))

    matches = registry.find(CapabilityFilter(requires_reasoning=True))
    assert [p.provider_id for p in matches] == ["reasoning"]


def test_find_filters_by_vision_capability() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "vision", capability_profiles=(_profile(profile_id="v", supports_vision=True),)
        )
    )
    registry.register(FakeLocalProvider("plain", capability_profiles=(_profile(profile_id="p"),)))

    matches = registry.find(CapabilityFilter(requires_vision=True))
    assert [p.provider_id for p in matches] == ["vision"]


def test_find_filters_by_minimum_context_size() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "small", capability_profiles=(_profile(profile_id="s", max_context_tokens=2048),)
        )
    )
    registry.register(
        FakeLocalProvider(
            "large", capability_profiles=(_profile(profile_id="l", max_context_tokens=16384),)
        )
    )

    matches = registry.find(CapabilityFilter(min_context_tokens=8000))
    assert [p.provider_id for p in matches] == ["large"]


def test_find_filters_by_local_only() -> None:
    registry = ProviderRegistry()
    registry.register(FakeLocalProvider("local"))
    registry.register(FakeHostedProvider("hosted"))

    matches = registry.find(CapabilityFilter(local_only=True))
    assert [p.provider_id for p in matches] == ["local"]


def test_find_filters_by_privacy_suitability() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeHostedProvider(
            "public-only",
            capability_profiles=(
                _profile(
                    profile_id="po",
                    is_local=False,
                    max_privacy_classification=PrivacyClassification.PUBLIC,
                ),
            ),
        )
    )
    registry.register(
        FakeLocalProvider(
            "restricted-capable",
            capability_profiles=(
                _profile(
                    profile_id="rc",
                    max_privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
                ),
            ),
        )
    )

    matches = registry.find(
        CapabilityFilter(privacy_classification=PrivacyClassification.SENSITIVE)
    )
    assert [p.provider_id for p in matches] == ["restricted-capable"]


def test_find_filters_by_latency_class() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "fast",
            capability_profiles=(_profile(profile_id="f", latency_class=LatencyClass.INTERACTIVE),),
        )
    )
    registry.register(
        FakeLocalProvider(
            "slow",
            capability_profiles=(_profile(profile_id="sl", latency_class=LatencyClass.BATCH),),
        )
    )

    matches = registry.find(CapabilityFilter(latency_class=LatencyClass.INTERACTIVE))
    assert [p.provider_id for p in matches] == ["fast"]


def test_find_filters_by_cost_class() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeLocalProvider(
            "cheap", capability_profiles=(_profile(profile_id="c", cost_class=CostClass.FREE),)
        )
    )
    registry.register(
        FakeHostedProvider(
            "pricey",
            capability_profiles=(
                _profile(profile_id="pr", is_local=False, cost_class=CostClass.HIGH),
            ),
        )
    )

    matches = registry.find(CapabilityFilter(cost_class=CostClass.LOW))
    assert [p.provider_id for p in matches] == ["cheap"]


def test_find_never_ranks_or_chooses_a_single_winner() -> None:
    registry = ProviderRegistry()
    registry.register(FakeLocalProvider("a"))
    registry.register(
        FakeLocalProvider("b", capability_profiles=(_profile(profile_id="b-profile"),))
    )

    matches = registry.find(CapabilityFilter())
    assert len(matches) == 2
