from __future__ import annotations

from decimal import Decimal
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from personal_lms.domain.base import StrictModel, utcnow
from personal_lms.domain.enums import ApprovalActionType, ApprovalStatus

_DECIDED_STATUSES = (ApprovalStatus.APPROVED, ApprovalStatus.DENIED)


class ApprovalRequest(StrictModel):
    """Describes a proposed action awaiting human approval.

    Constructing this object never executes the action it describes.
    """

    approval_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    action_type: ApprovalActionType
    summary: str = Field(min_length=1)
    requested_at: AwareDatetime = Field(default_factory=utcnow)
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: AwareDatetime | None = None
    decided_by: str | None = None
    estimated_cost_usd: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _decision_consistency(self) -> Self:
        if self.status in _DECIDED_STATUSES and self.decided_at is None:
            raise ValueError("decided_at is required once an approval has been approved or denied")
        if self.status == ApprovalStatus.PENDING and self.decided_at is not None:
            raise ValueError("decided_at must be unset while status is pending")
        return self
