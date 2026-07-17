"""Tutor domain contracts: a structured teaching request and teaching response.

Pure data shapes only — see ``docs/product-specs/AGENT_ROSTER_AND_CONTRACTS.md``
for the Tutor's role. The Tutor:

- consumes an ``AgentRequest`` plus a ``GroundingBundle`` (or an explicit
  general-knowledge acknowledgement) via ``TutorTeachingRequest``;
- produces a ``TeachingResponse`` recording confidence, objective mappings,
  memory cues, commands, and follow-up drill recommendations;
- never retrieves directly from storage — evidence only arrives as an
  already-assembled ``GroundingBundle`` from the Librarian;
- never modifies the trusted corpus and never approves a source;
- never claims factual support without citations — ``TeachingResponse``
  structurally requires either at least one citation or an explicit
  ``grounded_in_general_knowledge`` acknowledgement (see the validator
  below), mirroring ``TutorTeachingRequest``'s own input-side requirement;
- does not replace the Source Verifier: this schema records what the Tutor
  claims, not an independent check that the claim is actually supported.
"""

from __future__ import annotations

from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from personal_lms.domain.base import StrictModel, utcnow
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.knowledge_scope import KnowledgeScope
from personal_lms.domain.librarian import GroundingBundle


class DrillRecommendation(StrictModel):
    """A Tutor-recommended follow-up drill. Generating the drill itself is
    the Drill Master's job — this only records that one is recommended, and why."""

    reason: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    knowledge_scope: KnowledgeScope | None = None


class TutorTeachingRequest(StrictModel):
    """The Tutor's structured framing of what to teach.

    Built from the user's ``AgentRequest`` (correlated via
    ``agent_request_id``) and a Librarian-provided ``GroundingBundle`` —
    or an explicit acknowledgement that none is available. Mirrors the
    agent roster's requirement: "a RAG grounding bundle from approved
    sources, or an explicit statement that the explanation is general
    knowledge."
    """

    request_id: UUID = Field(default_factory=uuid4)
    agent_request_id: UUID = Field(
        description="Correlates to the originating AgentRequest.request_id."
    )
    learning_objective: str = Field(min_length=1)
    grounding_bundle: GroundingBundle | None = None
    general_knowledge_acknowledged: bool = Field(
        default=False,
        description=(
            "True when no grounding bundle is available and the Tutor has "
            "explicitly acknowledged the explanation will rely on general "
            "knowledge rather than retrieved evidence."
        ),
    )
    knowledge_scope: KnowledgeScope | None = None
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _requires_grounding_or_explicit_acknowledgement(self) -> Self:
        if self.grounding_bundle is None and not self.general_knowledge_acknowledged:
            raise ValueError(
                "grounding_bundle is required unless general_knowledge_acknowledged is True"
            )
        return self


class TeachingResponse(StrictModel):
    """The Tutor's structured teaching output."""

    response_id: UUID = Field(default_factory=uuid4)
    request_id: UUID = Field(
        description="Correlates to the originating TutorTeachingRequest.request_id."
    )
    learning_objective: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    example: str | None = Field(default=None, min_length=1)
    comprehension_check: str | None = Field(default=None, min_length=1)
    detected_misconception: str | None = Field(default=None, min_length=1)
    citations: list[SourceCitation] = Field(default_factory=list)
    grounded_in_general_knowledge: bool = Field(
        default=False,
        description="True when this response is explicitly not backed by any citation.",
    )
    confidence: float = Field(ge=0, le=1)
    objective_mappings: list[KnowledgeScope] = Field(default_factory=list)
    memory_cues: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    follow_up_drills: list[DrillRecommendation] = Field(default_factory=list)
    recommended_next_step: str | None = Field(default=None, min_length=1)
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _requires_citations_or_explicit_general_knowledge(self) -> Self:
        if not self.citations and not self.grounded_in_general_knowledge:
            raise ValueError("citations is required unless grounded_in_general_knowledge is True")
        return self
