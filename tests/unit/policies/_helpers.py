from __future__ import annotations

from decimal import Decimal

from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest
from personal_lms.domain.privacy import PrivacyClassification


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
