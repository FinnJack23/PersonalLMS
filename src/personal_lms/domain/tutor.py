"""Tutor domain contracts: a structured teaching request and teaching response.

Pure data shapes only — see ``docs/product-specs/AGENT_ROSTER_AND_CONTRACTS.md``
for the Tutor's role. The Tutor:

- consumes an ``AgentRequest`` plus a ``GroundingBundle`` (or an explicit
  general-knowledge acknowledgement, or an explicit request that a
  consuming service retrieve one itself) via ``TutorTeachingRequest``;
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

``grounding_is_sufficient``, ``citation_integrity_status``, ``retrieval_gaps``,
and ``refusal_reason`` on ``TeachingResponse`` are optional additions (all
defaulting to ``None`` or an empty list) that let a retrieval-backed,
citation-checked Tutor flow — see
``personal_lms.tutor.evidence_checked.EvidenceCheckedTutorService`` —
report its own grounding/verification/refusal state without breaking older
callers or stored JSON that predates them.

``TutorTeachingRequest.retrieve_grounding`` (default ``False``) and
``TutorTeachingRequest.privacy_classification`` (default
``PrivacyClassification.INTERNAL``) are likewise backward-compatible
additions — older stored request JSON without either key still validates,
selecting the pre-existing grounding-bundle/general-knowledge modes and
the previously-implicit ``INTERNAL`` privacy ceiling respectively.

``TeachingResponse.source_verification_status`` (default
``SourceVerificationStatus.NOT_ASSESSED``) exposes the separate, optional
Source Verifier's semantic claim-support judgment — see
``personal_lms.domain.source_verification`` and
``personal_lms.tutor._generation`` for the gate that populates it. It is
never a substitute for ``citation_integrity_status``, which remains the
structural citation-label signal; both can be inspected independently.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from personal_lms.domain.base import StrictModel, utcnow
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.knowledge_scope import KnowledgeScope
from personal_lms.domain.librarian import GroundingBundle
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_verification import SourceVerificationStatus


class CitationIntegrityStatus(StrEnum):
    """Structural citation-integrity verification outcome for one ``TeachingResponse``.

    Confirms only that every inline ``[E<n>]`` citation label a generated
    answer uses names evidence that was actually supplied to the model, and
    that a substantive answer contains at least one such citation — never a
    semantic or factual-correctness judgment (that remains the future
    Source Verifier's job, out of scope here; see
    ``personal_lms.tutor._generation`` for the verification this status
    records, shared by ``EvidenceCheckedTutorService`` and
    ``TutorTeachingCoordinator``).

    ``NOT_APPLICABLE`` means citation-integrity checking never ran, for
    either of two reasons: no generation happened at all (e.g. grounding
    was insufficient or no provider was eligible), or generation happened
    but with no evidence to verify against at all (the general-knowledge
    mode, which never claims verified citation integrity) — distinct from
    ``FAILED``, which means checking ran, evidence was supplied, and the
    draft was rejected.
    """

    NOT_APPLICABLE = "not_applicable"
    VERIFIED = "verified"
    FAILED = "failed"


class DrillRecommendation(StrictModel):
    """A Tutor-recommended follow-up drill. Generating the drill itself is
    the Drill Master's job — this only records that one is recommended, and why."""

    reason: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    knowledge_scope: KnowledgeScope | None = None


class TutorTeachingRequest(StrictModel):
    """The Tutor's structured framing of what to teach.

    Selects exactly one of three mutually exclusive grounding modes (see
    the validator below):

    a. ``grounding_bundle`` is supplied — an already-assembled
       ``GroundingBundle`` (e.g. built by an orchestrating Flow);
    b. ``general_knowledge_acknowledged=True`` — an explicit statement
       that no grounding bundle is available and the explanation will
       rely on general knowledge instead;
    c. ``retrieve_grounding=True`` — the consuming service (see
       ``personal_lms.tutor.evidence_checked.EvidenceCheckedTutorService``)
       must perform its own fresh retrieval. Distinct from (b): this is
       *not* a general-knowledge acknowledgement, so it must never be
       satisfied by (and must never be conflated with) a false
       ``general_knowledge_acknowledged``.

    Mirrors the agent roster's requirement: "a RAG grounding bundle from
    approved sources, or an explicit statement that the explanation is
    general knowledge" — extended with the explicit retrieval mode (c)
    for services that are themselves responsible for retrieval.
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
    retrieve_grounding: bool = Field(
        default=False,
        description=(
            "True when the consuming service must perform its own fresh "
            "retrieval rather than being handed an already-assembled "
            "grounding_bundle. Mutually exclusive with both "
            "grounding_bundle and general_knowledge_acknowledged=True — "
            "never require or substitute a false "
            "general_knowledge_acknowledged to satisfy validation instead "
            "of using this mode."
        ),
    )
    knowledge_scope: KnowledgeScope | None = None
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _selects_exactly_one_grounding_mode(self) -> Self:
        modes_selected = (
            int(self.grounding_bundle is not None)
            + int(self.general_knowledge_acknowledged is True)
            + int(self.retrieve_grounding is True)
        )
        if modes_selected != 1:
            raise ValueError(
                "exactly one of grounding_bundle, general_knowledge_acknowledged=True, "
                "or retrieve_grounding=True must be set"
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
    grounding_is_sufficient: bool | None = Field(
        default=None,
        description=(
            "Mirrors the retrieval GroundingBundle.is_sufficient this response was "
            "built from. None for responses that predate this field or that never "
            "went through a retrieval-backed flow (e.g. hand-authored general "
            "knowledge) — never fabricated as True or False."
        ),
    )
    citation_integrity_status: CitationIntegrityStatus | None = Field(
        default=None,
        description=(
            "Structural citation-integrity verification outcome — see "
            "CitationIntegrityStatus. None for responses that predate this field "
            "or were never structurally verified."
        ),
    )
    source_verification_status: SourceVerificationStatus = Field(
        default=SourceVerificationStatus.NOT_ASSESSED,
        description=(
            "Semantic claim-support verification outcome — distinct from and never "
            "a substitute for citation_integrity_status, which remains the "
            "structural citation signal. Defaults to NOT_ASSESSED (no Source "
            "Verifier was configured/run) for both new responses built without a "
            "verifier and older stored responses that predate this field entirely — "
            "never fabricated as VERIFIED merely because citation syntax passed."
        ),
    )
    retrieval_gaps: list[str] = Field(
        default_factory=list,
        description="Preserved verbatim from the retrieval GroundingBundle.gaps.",
    )
    refusal_reason: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Set only for a deterministic refusal/failure response (insufficient "
            "grounding, no eligible provider, provider failure, or failed "
            "citation-integrity verification) — never set alongside a real "
            "generated explanation."
        ),
    )
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
