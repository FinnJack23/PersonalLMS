from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field

from personal_lms.domain.base import StrictModel, utcnow
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.privacy import PrivacyClassification


class AgentRequest(StrictModel):
    """A bounded request routed to the Personal Assistant or a specialist agent.

    ``agent_id`` names a role, never a model vendor (see ADR-0002).
    """

    request_id: UUID = Field(default_factory=uuid4)
    agent_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    local_only: bool = False
    context: dict[str, str] = Field(default_factory=dict)
    created_at: AwareDatetime = Field(default_factory=utcnow)


class AgentResponse(StrictModel):
    """A structured, source-grounded reply from an agent."""

    request_id: UUID
    agent_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    citations: list[SourceCitation] = Field(default_factory=list)
    next_action: str | None = None
    approval_required: bool = False
    created_at: AwareDatetime = Field(default_factory=utcnow)
