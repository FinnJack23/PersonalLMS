from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    EvidenceConflict,
    GroundingBundle,
    KnowledgeScope,
    LibrarianRetrievalRequest,
    RetrievedEvidence,
    SourceCitation,
)

# --- LibrarianRetrievalRequest -----------------------------------------------


def test_retrieval_request_domain_neutral_minimal_construction() -> None:
    """No knowledge-pack or scope field is required — the request is valid
    with none of them set."""
    request = LibrarianRetrievalRequest(interpreted_query="OSPF DR election")
    assert request.knowledge_scope is None
    assert request.knowledge_packs == []
    assert request.raw_query is None
    assert request.max_results is None


def test_retrieval_request_accepts_multiple_knowledge_packs() -> None:
    request = LibrarianRetrievalRequest(
        interpreted_query="subnetting basics",
        knowledge_packs=["ccna", "network-plus"],
    )
    assert request.knowledge_packs == ["ccna", "network-plus"]


def test_retrieval_request_rejects_empty_query() -> None:
    with pytest.raises(ValidationError):
        LibrarianRetrievalRequest(interpreted_query="")


def test_retrieval_request_rejects_non_positive_max_results() -> None:
    with pytest.raises(ValidationError):
        LibrarianRetrievalRequest(interpreted_query="q", max_results=0)


def test_retrieval_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LibrarianRetrievalRequest(interpreted_query="q", vector_collection="ccna")  # type: ignore[call-arg]


def test_retrieval_request_knowledge_packs_default_is_isolated_between_instances() -> None:
    first = LibrarianRetrievalRequest(interpreted_query="a")
    second = LibrarianRetrievalRequest(interpreted_query="b")
    first.knowledge_packs.append("ccna")
    assert second.knowledge_packs == []


def test_retrieval_request_json_round_trip() -> None:
    request = LibrarianRetrievalRequest(
        interpreted_query="OSPF DR election",
        knowledge_scope=KnowledgeScope(certification="CCNA"),
    )
    restored = LibrarianRetrievalRequest.model_validate_json(request.model_dump_json())
    assert restored == request


# --- RetrievedEvidence --------------------------------------------------


def _citation(**overrides: object) -> SourceCitation:
    defaults: dict[str, object] = {"source_id": "src-1", "title": "Routing Concepts"}
    defaults.update(overrides)
    return SourceCitation.model_validate(defaults)


def test_retrieved_evidence_minimal_construction() -> None:
    evidence = RetrievedEvidence(citation=_citation())
    assert evidence.is_duplicate is False
    assert evidence.is_superseded is False
    assert evidence.relevance_score is None


def test_retrieved_evidence_rejects_relevance_score_out_of_range() -> None:
    with pytest.raises(ValidationError):
        RetrievedEvidence(citation=_citation(), relevance_score=1.5)


def test_retrieved_evidence_records_supersession() -> None:
    evidence = RetrievedEvidence(
        citation=_citation(), is_superseded=True, superseded_by_source_id="src-2"
    )
    assert evidence.is_superseded is True
    assert evidence.superseded_by_source_id == "src-2"


# --- EvidenceConflict -----------------------------------------------------


def test_evidence_conflict_requires_at_least_two_sources() -> None:
    with pytest.raises(ValidationError):
        EvidenceConflict(description="disagreement", conflicting_source_ids=["src-1"])


def test_evidence_conflict_valid_construction() -> None:
    conflict = EvidenceConflict(
        description="src-1 and src-2 disagree on default OSPF hello timer",
        conflicting_source_ids=["src-1", "src-2"],
    )
    assert conflict.conflicting_source_ids == ["src-1", "src-2"]


# --- GroundingBundle -----------------------------------------------------


def test_grounding_bundle_empty_evidence_is_valid_and_explicit() -> None:
    bundle = GroundingBundle(request_id=uuid4(), is_sufficient=False, gaps=["no CCNA source"])
    assert bundle.evidence == []
    assert bundle.is_sufficient is False


def test_grounding_bundle_sufficiency_is_not_inferred_from_evidence_count() -> None:
    """is_sufficient is an explicit Librarian judgment, not derived — a
    bundle with evidence can still be marked insufficient."""
    bundle = GroundingBundle(
        request_id=uuid4(),
        evidence=[RetrievedEvidence(citation=_citation())],
        is_sufficient=False,
        gaps=["evidence found but does not cover the full objective"],
    )
    assert len(bundle.evidence) == 1
    assert bundle.is_sufficient is False


def test_grounding_bundle_requires_is_sufficient() -> None:
    with pytest.raises(ValidationError):
        GroundingBundle.model_validate({"request_id": str(uuid4())})


def test_grounding_bundle_records_conflicts() -> None:
    bundle = GroundingBundle(
        request_id=uuid4(),
        evidence=[
            RetrievedEvidence(citation=_citation(source_id="src-1")),
            RetrievedEvidence(citation=_citation(source_id="src-2")),
        ],
        is_sufficient=True,
        conflicts=[
            EvidenceConflict(
                description="conflicting hello timer defaults",
                conflicting_source_ids=["src-1", "src-2"],
            )
        ],
    )
    assert len(bundle.conflicts) == 1


def test_grounding_bundle_gaps_default_is_isolated_between_instances() -> None:
    first = GroundingBundle(request_id=uuid4(), is_sufficient=False)
    second = GroundingBundle(request_id=uuid4(), is_sufficient=False)
    first.gaps.append("missing source")
    assert second.gaps == []


def test_grounding_bundle_json_round_trip() -> None:
    bundle = GroundingBundle(
        request_id=uuid4(),
        evidence=[RetrievedEvidence(citation=_citation())],
        is_sufficient=True,
        knowledge_scope=KnowledgeScope(certification="CCNA"),
    )
    restored = GroundingBundle.model_validate_json(bundle.model_dump_json())
    assert restored == bundle
