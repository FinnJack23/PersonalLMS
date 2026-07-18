from __future__ import annotations

from decimal import Decimal

from pydantic import Field, ValidationInfo, field_validator

from personal_lms.domain.base import StrictModel


class BudgetPolicy(StrictModel):
    """Configured spending limits. Performs no billing or payment logic."""

    policy_id: str = Field(min_length=1)
    daily_limit_usd: Decimal = Field(ge=0)
    monthly_limit_usd: Decimal = Field(ge=0)
    warn_at_percent: int = Field(default=70, ge=0, le=100)
    automatic_single_call_limit_usd: Decimal = Field(default=Decimal("0.15"), ge=0)
    approval_single_call_limit_usd: Decimal = Field(default=Decimal("0.50"), ge=0)
    local_only: bool = False

    @field_validator("monthly_limit_usd")
    @classmethod
    def _monthly_at_least_daily(cls, value: Decimal, info: ValidationInfo) -> Decimal:
        daily = info.data.get("daily_limit_usd")
        if daily is not None and value < daily:
            raise ValueError("monthly_limit_usd must be >= daily_limit_usd")
        return value

    @field_validator("approval_single_call_limit_usd")
    @classmethod
    def _approval_at_least_automatic(cls, value: Decimal, info: ValidationInfo) -> Decimal:
        automatic = info.data.get("automatic_single_call_limit_usd")
        if automatic is not None and value < automatic:
            raise ValueError(
                "approval_single_call_limit_usd must be >= automatic_single_call_limit_usd"
            )
        return value
