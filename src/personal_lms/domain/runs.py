from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field

from personal_lms.domain.base import StrictModel, utcnow
from personal_lms.domain.enums import RunStatus


class RunState(StrictModel):
    """Workflow execution state. Models progress only — no orchestration dependency."""

    run_id: UUID = Field(default_factory=uuid4)
    workflow_name: str = Field(min_length=1)
    status: RunStatus = RunStatus.PENDING
    created_at: AwareDatetime = Field(default_factory=utcnow)
    updated_at: AwareDatetime = Field(default_factory=utcnow)
    completed_steps: list[str] = Field(default_factory=list)
    pending_approval_ids: list[UUID] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    error_message: str | None = None
