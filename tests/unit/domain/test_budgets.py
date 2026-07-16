from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from personal_lms.domain import BudgetPolicy


def test_budget_policy_valid_construction() -> None:
    policy = BudgetPolicy(
        policy_id="default",
        daily_limit_usd=Decimal("3.00"),
        monthly_limit_usd=Decimal("40.00"),
    )
    assert policy.warn_at_percent == 70
    assert policy.local_only is False


def test_budget_policy_rejects_negative_limit() -> None:
    with pytest.raises(ValidationError):
        BudgetPolicy(
            policy_id="default",
            daily_limit_usd=Decimal("-1"),
            monthly_limit_usd=Decimal("40.00"),
        )


def test_budget_policy_rejects_monthly_below_daily() -> None:
    with pytest.raises(ValidationError):
        BudgetPolicy(
            policy_id="default",
            daily_limit_usd=Decimal("10.00"),
            monthly_limit_usd=Decimal("5.00"),
        )


def test_budget_policy_rejects_approval_limit_below_automatic_limit() -> None:
    with pytest.raises(ValidationError):
        BudgetPolicy(
            policy_id="default",
            daily_limit_usd=Decimal("3.00"),
            monthly_limit_usd=Decimal("40.00"),
            automatic_single_call_limit_usd=Decimal("0.50"),
            approval_single_call_limit_usd=Decimal("0.15"),
        )


def test_budget_policy_allows_zero_limit_for_hard_local_only() -> None:
    policy = BudgetPolicy(
        policy_id="local-only",
        daily_limit_usd=Decimal("0"),
        monthly_limit_usd=Decimal("0"),
        local_only=True,
    )
    assert policy.daily_limit_usd == Decimal("0")


def test_budget_policy_json_round_trip() -> None:
    policy = BudgetPolicy(
        policy_id="default",
        daily_limit_usd=Decimal("3.00"),
        monthly_limit_usd=Decimal("40.00"),
    )
    restored = BudgetPolicy.model_validate_json(policy.model_dump_json())
    assert restored == policy
