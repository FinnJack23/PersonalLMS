from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field

from personal_lms.domain.base import StrictModel
from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.privacy import PrivacyClassification


class ModelCapabilityProfile(StrictModel):
    """What a model tier can do. Never which vendor provides it (see ADR-0002)."""

    profile_id: str = Field(min_length=1)
    supports_reasoning: bool = False
    supports_vision: bool = False
    supports_structured_output: bool = True
    max_context_tokens: int = Field(gt=0)
    is_local: bool
    max_privacy_classification: PrivacyClassification
    latency_class: LatencyClass
    cost_class: CostClass


class ModelRequest(StrictModel):
    """A request for capability-based inference. May request a profile, never a vendor."""

    request_id: UUID = Field(default_factory=uuid4)
    capability_profile: str = Field(
        min_length=1,
        description="Requested ModelCapabilityProfile.profile_id, e.g. 'local_general'.",
    )
    prompt: str = Field(min_length=1)
    requires_vision: bool = False
    max_output_tokens: int | None = Field(default=None, gt=0)
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    context_token_estimate: int = Field(default=0, ge=0)


class ModelResult(StrictModel):
    """Structured outcome of a model invocation, independent of provider."""

    request_id: UUID
    capability_profile: str = Field(min_length=1)
    is_local: bool
    output_text: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    finish_reason: str = Field(min_length=1)
