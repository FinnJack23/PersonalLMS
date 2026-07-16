from __future__ import annotations

import pytest
from pydantic import ValidationError

from personal_lms.domain import RoutingDecision, RoutingOutcome


def test_tier_0_decision() -> None:
    decision = RoutingDecision(
        outcome=RoutingOutcome.TIER_0_DETERMINISTIC,
        reasons=["deterministic_calculation"],
    )
    assert decision.capability_profile is None
    assert decision.approval_required is False


def test_tier_1_decision_requires_capability_profile() -> None:
    decision = RoutingDecision(
        outcome=RoutingOutcome.TIER_1_LOCAL,
        capability_profile="local_general",
        reasons=["routine_text_task", "local_model_available"],
    )
    assert decision.capability_profile == "local_general"


def test_tier_1_decision_without_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(outcome=RoutingOutcome.TIER_1_LOCAL, reasons=["routine_text_task"])


def test_tier_2_decision() -> None:
    decision = RoutingDecision(
        outcome=RoutingOutcome.TIER_2_HOSTED,
        capability_profile="hosted_reasoning",
        reasons=["complex_vision"],
        redaction_required=True,
    )
    assert decision.redaction_required is True


def test_tier_0_decision_with_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(
            outcome=RoutingOutcome.TIER_0_DETERMINISTIC,
            capability_profile="local_general",
            reasons=["deterministic_calculation"],
        )


def test_approval_required_decision() -> None:
    decision = RoutingDecision(
        outcome=RoutingOutcome.APPROVAL_REQUIRED,
        reasons=["approval_single_call_limit_exceeded"],
        approval_required=True,
    )
    assert decision.approval_required is True


def test_rejected_decision() -> None:
    decision = RoutingDecision(
        outcome=RoutingOutcome.REJECTED,
        reasons=["contains_private_financial_data"],
    )
    assert decision.outcome == RoutingOutcome.REJECTED


def test_decision_requires_at_least_one_reason() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(outcome=RoutingOutcome.TIER_0_DETERMINISTIC, reasons=[])


def test_invalid_outcome_value_rejected() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(outcome="tier_3_quantum", reasons=["x"])  # type: ignore[arg-type]


def test_routing_decision_json_round_trip() -> None:
    decision = RoutingDecision(
        outcome=RoutingOutcome.TIER_2_HOSTED,
        capability_profile="hosted_reasoning",
        reasons=["complex_vision"],
        fallback_profiles=["local_reasoning"],
    )
    restored = RoutingDecision.model_validate_json(decision.model_dump_json())
    assert restored == decision
