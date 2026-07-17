from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    DrillRecommendation,
    GroundingBundle,
    KnowledgeScope,
    SourceCitation,
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


# --- DrillRecommendation ----------------------------------------------------


def test_drill_recommendation_rejects_empty_fields() -> None:
    with pytest.raises(ValidationError):
        DrillRecommendation(reason="", topic="OSPF")


def test_drill_recommendation_valid_construction() -> None:
    recommendation = DrillRecommendation(reason="reinforce", topic="OSPF DR election")
    assert recommendation.knowledge_scope is None
