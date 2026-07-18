from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest, ModelResult
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.providers.protocol import ModelProvider


def make_profile(**overrides: object) -> ModelCapabilityProfile:
    defaults: dict[str, object] = {
        "profile_id": "p",
        "supports_reasoning": False,
        "supports_vision": False,
        "max_context_tokens": 4096,
        "is_local": True,
        "max_privacy_classification": PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        "latency_class": LatencyClass.STANDARD,
        "cost_class": CostClass.LOW,
    }
    defaults.update(overrides)
    return ModelCapabilityProfile.model_validate(defaults)


def make_request(**overrides: object) -> ModelRequest:
    defaults: dict[str, object] = {
        "capability_profile": "any",
        "prompt": "Explain longest prefix match.",
    }
    defaults.update(overrides)
    return ModelRequest.model_validate(defaults)


def make_budget_policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "policy_id": "default",
        "daily_limit_usd": Decimal("3.00"),
        "monthly_limit_usd": Decimal("40.00"),
    }
    defaults.update(overrides)
    return BudgetPolicy.model_validate(defaults)


@dataclass
class CountingProvider:
    """Wraps a ModelProvider to count generate() invocations.

    Used to prove the adapter never retries or falls back — structurally
    satisfies the ModelProvider protocol via duck typing, no inheritance
    needed.
    """

    inner: ModelProvider
    call_count: int = 0

    @property
    def provider_id(self) -> str:
        return self.inner.provider_id

    @property
    def capability_profiles(self) -> tuple[ModelCapabilityProfile, ...]:
        return self.inner.capability_profiles

    @property
    def is_local(self) -> bool:
        return self.inner.is_local

    async def generate(self, request: ModelRequest) -> ModelResult:
        self.call_count += 1
        return await self.inner.generate(request)


@dataclass
class CountingRouter:
    """Wraps a DeterministicRouter to count route() invocations."""

    inner: object
    call_count: int = 0

    def route(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        self.call_count += 1
        return self.inner.route(*args, **kwargs)  # type: ignore[attr-defined]
