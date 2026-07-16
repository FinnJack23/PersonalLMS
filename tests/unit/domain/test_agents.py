from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import AgentRequest, AgentResponse, PrivacyClassification


def test_agent_request_valid_construction() -> None:
    request = AgentRequest(agent_id="tutor", instruction="Explain OSPF DR elections.")
    assert isinstance(request.request_id, UUID)
    assert request.privacy_classification == PrivacyClassification.INTERNAL
    assert request.created_at.tzinfo is not None


def test_agent_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AgentRequest(agent_id="tutor", instruction="Explain OSPF.", vendor="openai")  # type: ignore[call-arg]


def test_agent_request_strips_whitespace() -> None:
    request = AgentRequest(agent_id="  tutor  ", instruction="Explain OSPF.")
    assert request.agent_id == "tutor"


def test_agent_request_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        AgentRequest(
            agent_id="tutor",
            instruction="Explain OSPF.",
            created_at=datetime(2026, 7, 16, 12, 0, 0),
        )


def test_agent_request_context_default_is_isolated_between_instances() -> None:
    first = AgentRequest(agent_id="tutor", instruction="a")
    second = AgentRequest(agent_id="tutor", instruction="b")
    first.context["k"] = "v"
    assert second.context == {}


def test_agent_request_json_round_trip() -> None:
    request = AgentRequest(agent_id="tutor", instruction="Explain OSPF DR elections.")
    restored = AgentRequest.model_validate_json(request.model_dump_json())
    assert restored == request


def test_agent_response_json_round_trip() -> None:
    response = AgentResponse(
        request_id=uuid4(),
        agent_id="tutor",
        content="OSPF DR is elected based on priority then router ID.",
    )
    restored = AgentResponse.model_validate_json(response.model_dump_json())
    assert restored == response
