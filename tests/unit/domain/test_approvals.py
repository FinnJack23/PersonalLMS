from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import ApprovalActionType, ApprovalRequest, ApprovalStatus
from personal_lms.domain.base import utcnow


def test_approval_request_defaults() -> None:
    approval = ApprovalRequest(
        run_id=uuid4(),
        action_type=ApprovalActionType.HOSTED_ESCALATION,
        summary="Escalate ambiguous diagram to hosted vision model.",
    )
    assert isinstance(approval.approval_id, UUID)
    assert approval.status == ApprovalStatus.PENDING
    assert approval.decided_at is None
    assert approval.requested_at.tzinfo is not None


def test_approval_request_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        ApprovalRequest(
            run_id=uuid4(),
            action_type=ApprovalActionType.VAULT_WRITE,
            summary="Write candidate note.",
            requested_at=datetime(2026, 7, 16, 12, 0, 0),
        )


def test_approval_request_requires_decided_at_when_approved() -> None:
    with pytest.raises(ValidationError):
        ApprovalRequest(
            run_id=uuid4(),
            action_type=ApprovalActionType.VAULT_WRITE,
            summary="Write candidate note.",
            status=ApprovalStatus.APPROVED,
        )


def test_approval_request_accepts_decision_with_decided_at() -> None:
    approval = ApprovalRequest(
        run_id=uuid4(),
        action_type=ApprovalActionType.VAULT_WRITE,
        summary="Write candidate note.",
        status=ApprovalStatus.APPROVED,
        decided_at=utcnow(),
        decided_by="alan",
    )
    assert approval.status == ApprovalStatus.APPROVED


def test_approval_request_rejects_negative_estimated_cost() -> None:
    with pytest.raises(ValidationError):
        ApprovalRequest(
            run_id=uuid4(),
            action_type=ApprovalActionType.LARGE_BATCH_CALL,
            summary="Batch call.",
            estimated_cost_usd=Decimal("-0.01"),
        )


def test_approval_request_json_round_trip() -> None:
    approval = ApprovalRequest(
        run_id=uuid4(),
        action_type=ApprovalActionType.HOSTED_ESCALATION,
        summary="Escalate ambiguous diagram to hosted vision model.",
        estimated_cost_usd=Decimal("0.12"),
    )
    restored = ApprovalRequest.model_validate_json(approval.model_dump_json())
    assert restored == approval
