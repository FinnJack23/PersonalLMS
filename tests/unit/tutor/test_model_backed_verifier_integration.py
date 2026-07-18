from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import pytest

from personal_lms.content import SQLiteContentRepository
from personal_lms.domain import (
    BudgetPolicy,
    CitationIntegrityStatus,
    ContentChunk,
    CorpusDocument,
    GroundingBundle,
    PrivacyClassification,
    RetrievedEvidence,
    SourceCitation,
    SourceProcessingStatus,
    SourceVerificationStatus,
    TutorTeachingRequest,
)
from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest, ModelResult
from personal_lms.librarian import LibrarianContentGroundingService
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers import ProviderRegistry
from personal_lms.source_verification import ModelBackedSourceVerifier
from personal_lms.tutor import EvidenceCheckedTutorService, TutorTeachingCoordinator

_VALID_SHA256 = "a" * 64


def _document(**overrides: object) -> CorpusDocument:
    defaults: dict[str, object] = {
        "document_id": "doc-1",
        "source_id": "src-1",
        "title": "Routing Concepts Module 14",
        "content_hash": _VALID_SHA256,
        "status": SourceProcessingStatus.APPROVED,
    }
    defaults.update(overrides)
    return CorpusDocument.model_validate(defaults)


def _chunk(**overrides: object) -> ContentChunk:
    defaults: dict[str, object] = {
        "chunk_id": "chunk-1",
        "document_id": "doc-1",
        "source_id": "src-1",
        "ordinal": 0,
        "text": "The OSPF DR election is decided by priority, then router ID.",
        "text_hash": _VALID_SHA256,
        "status": SourceProcessingStatus.APPROVED,
        "trusted_for_rag": True,
    }
    defaults.update(overrides)
    return ContentChunk.model_validate(defaults)


def _teaching_request(**overrides: object) -> TutorTeachingRequest:
    defaults: dict[str, object] = {
        "agent_request_id": uuid4(),
        "learning_objective": "OSPF DR election",
        "retrieve_grounding": True,
    }
    defaults.update(overrides)
    return TutorTeachingRequest.model_validate(defaults)


def _budget_policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "policy_id": "default",
        "daily_limit_usd": Decimal("3.00"),
        "monthly_limit_usd": Decimal("40.00"),
    }
    defaults.update(overrides)
    return BudgetPolicy.model_validate(defaults)


def _routed_provider(verification_status: str = "verified", **verification_overrides: object):
    """A fake provider whose output depends on the incoming prompt: it
    answers the *teaching* prompt with a citation-bearing answer, and the
    *verification* prompt (built by ModelBackedSourceVerifier) with a
    strict JSON verification result. Lets one registry/router serve both
    the generation call and the verification call with a single provider,
    exactly as one local model would in a real deployment.
    """
    profile = ModelCapabilityProfile(
        profile_id="local-general",
        supports_reasoning=True,
        max_context_tokens=8192,
        is_local=True,
        max_privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        latency_class=LatencyClass.STANDARD,
        cost_class=CostClass.FREE,
    )

    class _Provider:
        provider_id = "routed-fake"
        capability_profiles = (profile,)
        is_local = True

        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def generate(self, request: ModelRequest) -> ModelResult:
            self.requests.append(request)
            if "source verifier" in request.prompt:
                # This is the verification prompt — extract the echoed
                # request_id so the JSON response correlates correctly.
                marker = "request_id: "
                start = request.prompt.index(marker) + len(marker)
                end = request.prompt.index("\n", start)
                verification_request_id = request.prompt[start:end]
                is_verified = verification_status == "verified"
                payload: dict[str, object] = {
                    "request_id": verification_request_id,
                    "status": verification_status,
                    "claims": [
                        {
                            "claim_id": "C1",
                            "status": "supported" if is_verified else "unsupported",
                            "evidence_labels": ["E1"],
                            "reason_codes": [],
                        }
                    ],
                    "verified_citation_labels": ["E1"] if is_verified else [],
                    "unsupported_claim_count": 0 if is_verified else 1,
                    "conflict_count": 0,
                    "semantic_confidence": None,
                    "reason_codes": [] if is_verified else ["unsupported_claim"],
                }
                payload.update(verification_overrides)
                output_text = json.dumps(payload)
            else:
                output_text = "Priority wins. [E1]"
            return ModelResult(
                request_id=request.request_id,
                capability_profile=request.capability_profile,
                is_local=True,
                output_text=output_text,
                input_tokens=1,
                output_tokens=1,
                latency_ms=1.0,
                finish_reason="stop",
            )

    return _Provider()


@pytest.fixture
def repo() -> SQLiteContentRepository:
    store = SQLiteContentRepository.open(":memory:")
    store.initialize_schema()
    return store


@pytest.fixture
def grounding_service(repo: SQLiteContentRepository) -> LibrarianContentGroundingService:
    return LibrarianContentGroundingService(repo)


def test_coordinator_retrieved_grounding_produces_a_verified_teaching_response(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())

    provider = _routed_provider("verified")
    registry = ProviderRegistry()
    registry.register(provider)
    router = DeterministicRouter(registry)
    evidence_checked = EvidenceCheckedTutorService(grounding_service, router)
    coordinator = TutorTeachingCoordinator(evidence_checked, router)
    verifier = ModelBackedSourceVerifier(
        verifier_id="model-backed", router=router, budget_policy=_budget_policy()
    )

    response = asyncio.run(
        coordinator.teach(
            _teaching_request(), budget_policy=_budget_policy(), source_verifier=verifier
        )
    )

    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED
    assert response.source_verification_status is SourceVerificationStatus.VERIFIED
    assert response.explanation == "Priority wins. [E1]"
    assert len(response.citations) == 1
    # exactly two provider calls: one generation, one verification
    assert len(provider.requests) == 2


def test_failed_routed_verification_causes_the_tutor_gate_to_fail_closed(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())

    provider = _routed_provider("rejected")
    registry = ProviderRegistry()
    registry.register(provider)
    router = DeterministicRouter(registry)
    evidence_checked = EvidenceCheckedTutorService(grounding_service, router)
    coordinator = TutorTeachingCoordinator(evidence_checked, router)
    verifier = ModelBackedSourceVerifier(
        verifier_id="model-backed", router=router, budget_policy=_budget_policy()
    )

    response = asyncio.run(
        coordinator.teach(
            _teaching_request(), budget_policy=_budget_policy(), source_verifier=verifier
        )
    )

    assert response.citations == []
    assert response.source_verification_status is SourceVerificationStatus.REJECTED
    assert response.refusal_reason is not None
    assert response.retrieval_gaps == []


def test_evidence_checked_service_can_use_the_concrete_verifier_directly(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())

    provider = _routed_provider("verified")
    registry = ProviderRegistry()
    registry.register(provider)
    router = DeterministicRouter(registry)
    service = EvidenceCheckedTutorService(grounding_service, router)
    verifier = ModelBackedSourceVerifier(
        verifier_id="model-backed", router=router, budget_policy=_budget_policy()
    )

    response = asyncio.run(
        service.teach(_teaching_request(), budget_policy=_budget_policy(), source_verifier=verifier)
    )

    assert response.source_verification_status is SourceVerificationStatus.VERIFIED


def test_supplied_grounding_mode_can_use_the_concrete_verifier(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    provider = _routed_provider("verified")
    registry = ProviderRegistry()
    registry.register(provider)
    router = DeterministicRouter(registry)
    evidence_checked = EvidenceCheckedTutorService(grounding_service, router)
    coordinator = TutorTeachingCoordinator(evidence_checked, router)
    verifier = ModelBackedSourceVerifier(
        verifier_id="model-backed", router=router, budget_policy=_budget_policy()
    )

    bundle = GroundingBundle(
        request_id=uuid4(),
        evidence=[
            RetrievedEvidence(
                citation=SourceCitation(source_id="src-1", title="Routing Concepts"),
                text="The OSPF DR election is decided by priority, then router ID.",
                document_id="doc-1",
                chunk_id="chunk-1",
                trusted_for_rag=True,
            )
        ],
        is_sufficient=True,
    )
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=bundle)

    response = asyncio.run(
        coordinator.teach(request, budget_policy=_budget_policy(), source_verifier=verifier)
    )

    assert response.source_verification_status is SourceVerificationStatus.VERIFIED
    assert len(response.citations) == 1


def test_general_knowledge_mode_never_calls_the_concrete_verifier(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    provider = _routed_provider("verified")
    registry = ProviderRegistry()
    registry.register(provider)
    router = DeterministicRouter(registry)
    evidence_checked = EvidenceCheckedTutorService(grounding_service, router)
    coordinator = TutorTeachingCoordinator(evidence_checked, router)
    verifier = ModelBackedSourceVerifier(
        verifier_id="model-backed", router=router, budget_policy=_budget_policy()
    )
    request = _teaching_request(retrieve_grounding=False, general_knowledge_acknowledged=True)

    response = asyncio.run(
        coordinator.teach(request, budget_policy=_budget_policy(), source_verifier=verifier)
    )

    assert response.source_verification_status is SourceVerificationStatus.NOT_APPLICABLE
    # exactly one provider call: generation only, no verification prompt reached it
    assert len(provider.requests) == 1
    assert "source verifier" not in provider.requests[0].prompt
