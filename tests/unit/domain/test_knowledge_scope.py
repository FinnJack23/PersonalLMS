from __future__ import annotations

import pytest
from pydantic import ValidationError

from personal_lms.domain import KnowledgeScope


def test_knowledge_scope_all_fields_optional() -> None:
    scope = KnowledgeScope()
    assert scope.knowledge_domain is None
    assert scope.certification is None
    assert scope.course is None
    assert scope.topic is None
    assert scope.objective_framework is None


def test_knowledge_scope_accepts_partial_metadata() -> None:
    scope = KnowledgeScope(certification="CCNA", objective_framework="1.1.a")
    assert scope.certification == "CCNA"
    assert scope.objective_framework == "1.1.a"
    assert scope.knowledge_domain is None


def test_knowledge_scope_rejects_empty_string_fields() -> None:
    with pytest.raises(ValidationError):
        KnowledgeScope(certification="")


def test_knowledge_scope_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        KnowledgeScope(exam_board="Pearson VUE")  # type: ignore[call-arg]


def test_knowledge_scope_strips_whitespace() -> None:
    scope = KnowledgeScope(topic="  OSI Model  ")
    assert scope.topic == "OSI Model"


def test_knowledge_scope_json_round_trip() -> None:
    scope = KnowledgeScope(knowledge_domain="networking", certification="CCNA")
    restored = KnowledgeScope.model_validate_json(scope.model_dump_json())
    assert restored == scope
