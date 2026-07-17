from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    CitationIntegrityStatus,
    DrillRecommendation,
    GroundingBundle,
    KnowledgeScope,
    PrivacyClassification,
    SourceCitation,
    SourceVerificationStatus,
    TeachingResponse,
    TutorTeachingRequest,
)


def _citation(**overrides: object) -> SourceCitation:
    defaults: dict[str, object] = {"source_id": "src-1", "title": "Routing Concepts"}
    defaults.update(overrides)
    return SourceCitation.model_validate(defaults)


def _grounding_bundle() -> GroundingBundle:
    return GroundingBundle(request_id=uuid4(), is_sufficient=True)


# --- TutorTeachingRequest -------------------------------------------------


def test_teaching_request_accepts_a_grounding_bundle() -> None:
    request = TutorTeachingRequest(
        agent_request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        grounding_bundle=_grounding_bundle(),
    )
    assert request.grounding_bundle is not None
    assert request.general_knowledge_acknowledged is False


def test_teaching_request_accepts_explicit_general_knowledge_acknowledgement() -> None:
    request = TutorTeachingRequest(
        agent_request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        general_knowledge_acknowledged=True,
    )
    assert request.grounding_bundle is None


def test_teaching_request_rejects_neither_grounding_nor_acknowledgement() -> None:
    with pytest.raises(ValidationError):
        TutorTeachingRequest(
            agent_request_id=uuid4(),
            learning_objective="Explain OSPF DR election",
        )


def test_teaching_request_rejects_empty_learning_objective() -> None:
    with pytest.raises(ValidationError):
        TutorTeachingRequest(
            agent_request_id=uuid4(),
            learning_objective="",
            general_knowledge_acknowledged=True,
        )


def test_teaching_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TutorTeachingRequest(
            agent_request_id=uuid4(),
            learning_objective="x",
            general_knowledge_acknowledged=True,
            model_vendor="anthropic",  # type: ignore[call-arg]
        )


def test_teaching_request_domain_neutral_no_required_scope_field() -> None:
    """Constructs with no certification/course/topic/domain metadata at all."""
    request = TutorTeachingRequest(
        agent_request_id=uuid4(),
        learning_objective="Explain a concept",
        general_knowledge_acknowledged=True,
    )
    assert request.knowledge_scope is None


def test_teaching_request_json_round_trip() -> None:
    request = TutorTeachingRequest(
        agent_request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        grounding_bundle=_grounding_bundle(),
        knowledge_scope=KnowledgeScope(certification="CCNA"),
    )
    restored = TutorTeachingRequest.model_validate_json(request.model_dump_json())
    assert restored == request


# --- retrieve_grounding: explicit third grounding mode ----------------------


def test_teaching_request_accepts_retrieve_grounding_without_acknowledgement() -> None:
    """retrieve_grounding=True is its own valid mode — it must not require,
    and must not be satisfied by, general_knowledge_acknowledged=True."""
    request = TutorTeachingRequest(
        agent_request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        retrieve_grounding=True,
    )
    assert request.retrieve_grounding is True
    assert request.general_knowledge_acknowledged is False
    assert request.grounding_bundle is None


def test_teaching_request_rejects_bundle_and_acknowledgement_together() -> None:
    with pytest.raises(ValidationError):
        TutorTeachingRequest(
            agent_request_id=uuid4(),
            learning_objective="x",
            grounding_bundle=_grounding_bundle(),
            general_knowledge_acknowledged=True,
        )


def test_teaching_request_rejects_bundle_and_retrieve_grounding_together() -> None:
    with pytest.raises(ValidationError):
        TutorTeachingRequest(
            agent_request_id=uuid4(),
            learning_objective="x",
            grounding_bundle=_grounding_bundle(),
            retrieve_grounding=True,
        )


def test_teaching_request_rejects_acknowledgement_and_retrieve_grounding_together() -> None:
    with pytest.raises(ValidationError):
        TutorTeachingRequest(
            agent_request_id=uuid4(),
            learning_objective="x",
            general_knowledge_acknowledged=True,
            retrieve_grounding=True,
        )


def test_teaching_request_rejects_all_three_modes_together() -> None:
    with pytest.raises(ValidationError):
        TutorTeachingRequest(
            agent_request_id=uuid4(),
            learning_objective="x",
            grounding_bundle=_grounding_bundle(),
            general_knowledge_acknowledged=True,
            retrieve_grounding=True,
        )


def test_teaching_request_old_shaped_payload_without_new_fields_still_validates() -> None:
    """A JSON payload shaped like it predates this correction (no
    retrieve_grounding/privacy_classification keys at all) must still
    validate, defaulting to retrieve_grounding=False and the
    previously-implicit INTERNAL privacy ceiling."""
    old_shaped_json = (
        '{"request_id": "' + str(uuid4()) + '", "agent_request_id": "' + str(uuid4()) + '", '
        '"learning_objective": "x", "grounding_bundle": null, '
        '"general_knowledge_acknowledged": true, "knowledge_scope": null, '
        '"created_at": "2026-01-01T00:00:00Z"}'
    )
    request = TutorTeachingRequest.model_validate_json(old_shaped_json)
    assert request.retrieve_grounding is False
    assert request.privacy_classification is PrivacyClassification.INTERNAL


# --- privacy_classification: defaults to INTERNAL, backward-compatible ------


def test_teaching_request_privacy_classification_defaults_to_internal() -> None:
    request = TutorTeachingRequest(
        agent_request_id=uuid4(), learning_objective="x", retrieve_grounding=True
    )
    assert request.privacy_classification is PrivacyClassification.INTERNAL


def test_teaching_request_accepts_explicit_privacy_classification() -> None:
    request = TutorTeachingRequest(
        agent_request_id=uuid4(),
        learning_objective="x",
        retrieve_grounding=True,
        privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
    )
    assert request.privacy_classification is PrivacyClassification.RESTRICTED_LOCAL_ONLY


# --- TeachingResponse -------------------------------------------------------


def test_teaching_response_with_citations_does_not_require_acknowledgement() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        explanation="The DR is elected by priority, then router ID.",
        citations=[_citation()],
        confidence=0.9,
    )
    assert response.grounded_in_general_knowledge is False


def test_teaching_response_accepts_explicit_general_knowledge_with_no_citations() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="Explain a general concept",
        explanation="This is general networking knowledge.",
        grounded_in_general_knowledge=True,
        confidence=0.6,
    )
    assert response.citations == []


def test_teaching_response_rejects_no_citations_and_no_acknowledgement() -> None:
    """Never claims factual support without citations or evidence."""
    with pytest.raises(ValidationError):
        TeachingResponse(
            request_id=uuid4(),
            learning_objective="Explain OSPF DR election",
            explanation="The DR is elected by priority.",
            confidence=0.9,
        )


def test_teaching_response_requires_confidence() -> None:
    with pytest.raises(ValidationError):
        TeachingResponse.model_validate(
            {
                "request_id": str(uuid4()),
                "learning_objective": "x",
                "explanation": "y",
                "grounded_in_general_knowledge": True,
            }
        )


def test_teaching_response_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        TeachingResponse(
            request_id=uuid4(),
            learning_objective="x",
            explanation="y",
            grounded_in_general_knowledge=True,
            confidence=1.1,
        )


def test_teaching_response_records_objective_mappings_memory_cues_and_commands() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        explanation="The DR is elected by priority, then router ID.",
        citations=[_citation()],
        confidence=0.85,
        objective_mappings=[KnowledgeScope(certification="CCNA", objective_framework="3.2.a")],
        memory_cues=["Highest priority wins; ties broken by router ID."],
        commands=["show ip ospf neighbor"],
    )
    assert response.objective_mappings[0].certification == "CCNA"
    assert response.memory_cues == ["Highest priority wins; ties broken by router ID."]
    assert response.commands == ["show ip ospf neighbor"]


def test_teaching_response_records_follow_up_drill_recommendations() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        explanation="The DR is elected by priority, then router ID.",
        citations=[_citation()],
        confidence=0.85,
        follow_up_drills=[
            DrillRecommendation(reason="Reinforce priority tie-breaking", topic="OSPF DR election")
        ],
    )
    assert response.follow_up_drills[0].topic == "OSPF DR election"


def test_teaching_response_lists_default_isolated_between_instances() -> None:
    first = TeachingResponse(
        request_id=uuid4(),
        learning_objective="x",
        explanation="y",
        grounded_in_general_knowledge=True,
        confidence=0.5,
    )
    second = TeachingResponse(
        request_id=uuid4(),
        learning_objective="x",
        explanation="y",
        grounded_in_general_knowledge=True,
        confidence=0.5,
    )
    first.memory_cues.append("cue")
    assert second.memory_cues == []


def test_teaching_response_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TeachingResponse(
            request_id=uuid4(),
            learning_objective="x",
            explanation="y",
            grounded_in_general_knowledge=True,
            confidence=0.5,
            model_used="qwen2.5",  # type: ignore[call-arg]
        )


def test_teaching_response_json_round_trip() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        explanation="The DR is elected by priority, then router ID.",
        citations=[_citation()],
        confidence=0.85,
        objective_mappings=[KnowledgeScope(certification="CCNA")],
        follow_up_drills=[DrillRecommendation(reason="reinforce", topic="OSPF")],
    )
    restored = TeachingResponse.model_validate_json(response.model_dump_json())
    assert restored == response


# --- grounding_is_sufficient / citation_integrity_status / retrieval_gaps /
#     refusal_reason: optional, backward-compatible -------------------------


def test_teaching_response_old_shaped_payload_without_new_fields_still_validates() -> None:
    """A JSON payload shaped like it predates this milestone (no
    grounding_is_sufficient/citation_integrity_status/retrieval_gaps/
    refusal_reason keys at all) must still validate."""
    old_shaped_json = (
        '{"response_id": "' + str(uuid4()) + '", "request_id": "' + str(uuid4()) + '", '
        '"learning_objective": "x", "explanation": "y", "example": null, '
        '"comprehension_check": null, "detected_misconception": null, '
        '"citations": [], "grounded_in_general_knowledge": true, "confidence": 0.5, '
        '"objective_mappings": [], "memory_cues": [], "commands": [], '
        '"follow_up_drills": [], "recommended_next_step": null, '
        '"created_at": "2026-01-01T00:00:00Z"}'
    )
    response = TeachingResponse.model_validate_json(old_shaped_json)
    assert response.grounding_is_sufficient is None
    assert response.citation_integrity_status is None
    assert response.retrieval_gaps == []
    assert response.refusal_reason is None
    assert response.source_verification_status is SourceVerificationStatus.NOT_ASSESSED


def test_teaching_response_exposes_grounding_and_citation_integrity_fields() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        explanation="The DR is elected by priority, then router ID.",
        citations=[_citation()],
        confidence=1.0,
        grounding_is_sufficient=True,
        citation_integrity_status=CitationIntegrityStatus.VERIFIED,
        retrieval_gaps=[],
    )
    assert response.grounding_is_sufficient is True
    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED
    assert response.refusal_reason is None


def test_teaching_response_records_a_deterministic_refusal() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        explanation="This question cannot be answered from approved, trusted sources.",
        grounded_in_general_knowledge=True,
        confidence=0.0,
        grounding_is_sufficient=False,
        citation_integrity_status=CitationIntegrityStatus.NOT_APPLICABLE,
        retrieval_gaps=["no permitted content chunks matched the query: 'x'"],
        refusal_reason="insufficient approved, trusted evidence was retrieved",
    )
    assert response.citations == []
    assert response.refusal_reason is not None
    assert response.retrieval_gaps == ["no permitted content chunks matched the query: 'x'"]


def test_teaching_response_json_round_trip_with_new_fields() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        explanation="The DR is elected by priority, then router ID.",
        citations=[_citation()],
        confidence=1.0,
        grounding_is_sufficient=True,
        citation_integrity_status=CitationIntegrityStatus.VERIFIED,
        retrieval_gaps=["a gap"],
    )
    restored = TeachingResponse.model_validate_json(response.model_dump_json())
    assert restored == response


# --- source_verification_status: optional, backward-compatible --------------


def test_teaching_response_source_verification_status_defaults_to_not_assessed() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="x",
        explanation="y",
        grounded_in_general_knowledge=True,
        confidence=0.0,
    )
    assert response.source_verification_status is SourceVerificationStatus.NOT_ASSESSED


def test_teaching_response_exposes_source_verification_status() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        explanation="The DR is elected by priority, then router ID.",
        citations=[_citation()],
        confidence=0.0,
        citation_integrity_status=CitationIntegrityStatus.VERIFIED,
        source_verification_status=SourceVerificationStatus.VERIFIED,
    )
    assert response.source_verification_status is SourceVerificationStatus.VERIFIED
    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED


def test_teaching_response_source_verification_status_distinct_from_citation_integrity() -> None:
    """A response can be structurally citation-verified while semantic
    source verification failed closed — two distinct, independently
    inspectable signals."""
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="x",
        explanation="This question cannot be answered from approved, trusted sources.",
        grounded_in_general_knowledge=True,
        confidence=0.0,
        citation_integrity_status=CitationIntegrityStatus.VERIFIED,
        source_verification_status=SourceVerificationStatus.REJECTED,
        refusal_reason="the generated answer did not pass semantic source verification",
    )
    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED
    assert response.source_verification_status is SourceVerificationStatus.REJECTED


def test_teaching_response_json_round_trip_with_source_verification_status() -> None:
    response = TeachingResponse(
        request_id=uuid4(),
        learning_objective="Explain OSPF DR election",
        explanation="The DR is elected by priority, then router ID.",
        citations=[_citation()],
        confidence=0.9,
        citation_integrity_status=CitationIntegrityStatus.VERIFIED,
        source_verification_status=SourceVerificationStatus.VERIFIED,
    )
    restored = TeachingResponse.model_validate_json(response.model_dump_json())
    assert restored == response


# --- DrillRecommendation ----------------------------------------------------


def test_drill_recommendation_rejects_empty_fields() -> None:
    with pytest.raises(ValidationError):
        DrillRecommendation(reason="", topic="OSPF")


def test_drill_recommendation_valid_construction() -> None:
    recommendation = DrillRecommendation(reason="reinforce", topic="OSPF DR election")
    assert recommendation.knowledge_scope is None
