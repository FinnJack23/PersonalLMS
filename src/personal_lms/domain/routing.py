from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from personal_lms.domain.base import StrictModel
from personal_lms.domain.enums import RoutingOutcome

_PROFILE_REQUIRED_OUTCOMES = (RoutingOutcome.TIER_1_LOCAL, RoutingOutcome.TIER_2_HOSTED)


class RoutingDecision(StrictModel):
    """Deterministic router output. Carries reasons only, never a side effect."""

    outcome: RoutingOutcome
    capability_profile: str | None = Field(
        default=None,
        description="Selected ModelCapabilityProfile.profile_id for tier_1/tier_2 outcomes.",
    )
    reasons: list[str] = Field(min_length=1)
    approval_required: bool = False
    redaction_required: bool = False
    fallback_profiles: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _capability_profile_matches_outcome(self) -> Self:
        needs_profile = self.outcome in _PROFILE_REQUIRED_OUTCOMES
        if needs_profile and not self.capability_profile:
            raise ValueError(
                "capability_profile is required when outcome is tier_1_local or tier_2_hosted"
            )
        if not needs_profile and self.capability_profile is not None:
            raise ValueError(
                "capability_profile must be unset for tier_0, approval_required, "
                "or rejected outcomes"
            )
        return self
