from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from personal_lms.content import SQLiteContentRepository
from personal_lms.domain import (
    BudgetPolicy,
    CitationIntegrityStatus,
    ClaimSupportStatus,
    ClaimVerification,
    ContentChunk,
    CorpusDocument,
    GroundingBundle,
    PrivacyClassification,
    RetrievedEvidence,
    SourceCitation,
    SourceProcessingStatus,
    SourceVerificationResult,
    SourceVerificationStatus,
    TutorTeachingRequest,
)
from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest, ModelResult
from personal_lms.librarian import LibrarianContentGroundingService
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers import FakeHostedProvider, FakeLocalProvider, ProviderRegistry
from personal_lms.providers.errors import ProviderExecutionError
from personal_lms.source_verification.errors import SourceVerificationExecutionError
from personal_lms.source_verification.fake import FakeSourceVerifier
from personal_lms.tutor import EvidenceCheckedTutorService, TutorTeachingCoordinator
from personal_lms.tutor._generation import answer_from_bundle

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


def _evidence(**overrides: object) -> RetrievedEvidence:
    defaults: dict[str, object] = {
        "citation": SourceCitation(source_id="src-1", title="Routing Concepts", approved=True),
        "text": "The OSPF DR election is decided by priority, then router ID.",
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "trusted_for_rag": True,
    }
    defaults.update(overrides)
    return RetrievedEvidence.model_validate(defaults)


def _bundle(**overrides: object) -> GroundingBundle:
    defaults: dict[str, object] = {
        "request_id": uuid4(),
        "evidence": [_evidence()],
        "is_sufficient": True,
    }
    defaults.update(overrides)
    return GroundingBundle.model_validate(defaults)


def _claim(**overrides: object) -> ClaimVerification:
    defaults: dict[str, object] = {
        "claim_id": "claim-1",
        "status": ClaimSupportStatus.SUPPORTED,
        "evidence_labels": ("E1",),
    }
    defaults.update(overrides)
    return ClaimVerification.model_validate(defaults)


def _verification_result(**overrides: object) -> SourceVerificationResult:
    defaults: dict[str, object] = {
        "request_id": "placeholder",
        "status": SourceVerificationStatus.VERIFIED,
        "claims": (_claim(),),
        "verified_citation_labels": ("E1",),
        "unsupported_claim_count": 0,
        "conflict_count": 0,
    }
    defaults.update(overrides)
    return SourceVerificationResult.model_validate(defaults)


class _CapturingProvider:
    """Wraps a real fake provider, recording every ModelRequest it receives."""

    def __init__(self, inner: FakeLocalProvider | FakeHostedProvider) -> None:
        self._inner = inner
        self.requests: list[ModelRequest] = []

    @property
    def provider_id(self) -> str:
        return self._inner.provider_id

    @property
    def capability_profiles(self) -> tuple[object, ...]:
        return self._inner.capability_profiles

    @property
    def is_local(self) -> bool:
        return self._inner.is_local

    async def generate(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        return await self._inner.generate(request)


def _hosted_profile_for(privacy: PrivacyClassification) -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        profile_id="hosted-approval-required",
        max_context_tokens=4096,
        is_local=False,
        max_privacy_classification=privacy,
        latency_class=LatencyClass.STANDARD,
        cost_class=CostClass.MEDIUM,
    )


@pytest.fixture
def repo() -> SQLiteContentRepository:
    store = SQLiteContentRepository.open(":memory:")
    store.initialize_schema()
    return store


@pytest.fixture
def grounding_service(repo: SQLiteContentRepository) -> LibrarianContentGroundingService:
    return LibrarianContentGroundingService(repo)


def _router_for(provider: object) -> tuple[DeterministicRouter, _CapturingProvider]:
    capturing = _CapturingProvider(provider)  # type: ignore[arg-type]
    registry = ProviderRegistry()
    registry.register(capturing)  # type: ignore[arg-type]
    return DeterministicRouter(registry), capturing


# --- delegation / wiring: verifier reached exactly once per public entry point -


def test_retrieved_grounding_mode_calls_verifier_exactly_once_after_structural_validation(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    service = EvidenceCheckedTutorService(grounding_service, router)
    verifier = FakeSourceVerifier(result=_verification_result())

    response = asyncio.run(
        service.teach(_teaching_request(), budget_policy=_budget_policy(), source_verifier=verifier)
    )

    assert verifier.call_count == 1
    assert len(provider.requests) == 1
    assert response.source_verification_status is SourceVerificationStatus.VERIFIED


def test_supplied_grounding_mode_calls_verifier_exactly_once_after_structural_validation() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    empty_repo = SQLiteContentRepository.open(":memory:")
    empty_repo.initialize_schema()
    evidence_checked = EvidenceCheckedTutorService(
        LibrarianContentGroundingService(empty_repo), router
    )
    coordinator = TutorTeachingCoordinator(evidence_checked, router)
    verifier = FakeSourceVerifier(result=_verification_result())
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=_bundle())

    response = asyncio.run(
        coordinator.teach(request, budget_policy=_budget_policy(), source_verifier=verifier)
    )

    assert verifier.call_count == 1
    assert len(provider.requests) == 1
    assert response.source_verification_status is SourceVerificationStatus.VERIFIED


def test_general_knowledge_mode_calls_verifier_zero_times() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="A general answer."))
    empty_repo = SQLiteContentRepository.open(":memory:")
    empty_repo.initialize_schema()
    evidence_checked = EvidenceCheckedTutorService(
        LibrarianContentGroundingService(empty_repo), router
    )
    coordinator = TutorTeachingCoordinator(evidence_checked, router)
    verifier = FakeSourceVerifier(result=_verification_result())
    request = _teaching_request(retrieve_grounding=False, general_knowledge_acknowledged=True)

    response = asyncio.run(
        coordinator.teach(request, budget_policy=_budget_policy(), source_verifier=verifier)
    )

    assert verifier.call_count == 0
    assert len(provider.requests) == 1
    assert response.source_verification_status is SourceVerificationStatus.NOT_APPLICABLE


# --- gate-skip: verifier never reached before structural validation succeeds --


def test_insufficient_grounding_calls_verifier_zero_times() -> None:
    router, provider = _router_for(FakeLocalProvider())
    verifier = FakeSourceVerifier(result=_verification_result())
    bundle = _bundle(evidence=[], is_sufficient=False, gaps=["nothing matched"])

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert verifier.call_count == 0
    assert provider.requests == []
    assert response.retrieval_gaps == ["nothing matched"]


def test_routing_rejection_calls_verifier_zero_times() -> None:
    registry = ProviderRegistry()
    router = DeterministicRouter(registry)  # empty: NoCompatibleProviderError
    verifier = FakeSourceVerifier(result=_verification_result())
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert verifier.call_count == 0
    assert response.citations == []


def test_approval_required_calls_verifier_zero_times() -> None:
    router, provider = _router_for(
        FakeHostedProvider(
            capability_profiles=(_hosted_profile_for(PrivacyClassification.INTERNAL),)
        )
    )
    verifier = FakeSourceVerifier(result=_verification_result())
    bundle = _bundle()
    budget_policy = _budget_policy(automatic_single_call_limit_usd=Decimal("0"))

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=budget_policy,
            source_verifier=verifier,
        )
    )

    assert verifier.call_count == 0
    assert provider.requests == []
    assert response.citations == []


def test_provider_failure_calls_verifier_zero_times() -> None:
    router, provider = _router_for(
        FakeLocalProvider(fail_with=ProviderExecutionError("fake-local", "simulated failure"))
    )
    verifier = FakeSourceVerifier(result=_verification_result())
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert verifier.call_count == 0
    assert response.citations == []


def test_structural_citation_failure_calls_verifier_zero_times() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="No citation here at all."))
    verifier = FakeSourceVerifier(result=_verification_result())
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert verifier.call_count == 0
    assert response.citation_integrity_status is CitationIntegrityStatus.FAILED
    assert response.citations == []


# --- verification request propagation -------------------------------------------


def test_verification_request_propagates_request_id() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    verifier = FakeSourceVerifier(result=_verification_result())
    bundle = _bundle()
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=bundle)

    asyncio.run(
        answer_from_bundle(
            request=request,
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert len(verifier.calls) == 1
    assert verifier.calls[0].request_id == str(request.request_id)


def test_verification_request_propagates_privacy_classification() -> None:
    router, provider = _router_for(
        FakeLocalProvider(
            output_text="Priority wins. [E1]",
            capability_profiles=(
                ModelCapabilityProfile(
                    profile_id="local-sensitive",
                    max_context_tokens=4096,
                    is_local=True,
                    max_privacy_classification=PrivacyClassification.SENSITIVE,
                    latency_class=LatencyClass.STANDARD,
                    cost_class=CostClass.LOW,
                ),
            ),
        )
    )
    verifier = FakeSourceVerifier(result=_verification_result())
    bundle = _bundle()
    request = _teaching_request(
        retrieve_grounding=False,
        grounding_bundle=bundle,
        privacy_classification=PrivacyClassification.SENSITIVE,
    )

    asyncio.run(
        answer_from_bundle(
            request=request,
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert verifier.calls[0].privacy_classification is PrivacyClassification.SENSITIVE
    assert provider.requests[0].privacy_classification is PrivacyClassification.SENSITIVE


def test_verification_request_contains_only_structurally_used_citation_labels() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins [E1]. [E1]"))
    verifier = FakeSourceVerifier(result=_verification_result())
    bundle = _bundle(
        evidence=[
            _evidence(chunk_id="chunk-1", citation=SourceCitation(source_id="src-1", title="A")),
            _evidence(
                chunk_id="chunk-2",
                citation=SourceCitation(source_id="src-2", title="B"),
                text="A second, unused piece of evidence.",
            ),
        ]
    )
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=bundle)

    asyncio.run(
        answer_from_bundle(
            request=request,
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    # Output text cites only [E1] (twice) — E2 was retrieved but never used.
    assert verifier.calls[0].used_citation_labels == ("E1",)


# --- verified outcomes ---------------------------------------------------------


def test_verified_result_returns_the_generated_answer_and_used_citations() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    verifier = FakeSourceVerifier(result=_verification_result())
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert response.explanation == "Priority wins. [E1]"
    assert len(response.citations) == 1
    assert response.citations[0].source_id == "src-1"
    assert response.source_verification_status is SourceVerificationStatus.VERIFIED
    assert response.refusal_reason is None


def test_verified_result_with_assessed_confidence_propagates_that_value() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    verifier = FakeSourceVerifier(result=_verification_result(semantic_confidence=0.73))
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert response.confidence == 0.73


def test_verified_result_without_assessed_confidence_retains_zero() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    verifier = FakeSourceVerifier(result=_verification_result())
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert response.confidence == 0.0


# --- non-verified outcomes: fail closed -----------------------------------------


def test_partially_verified_result_fails_closed() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    result = _verification_result(
        status=SourceVerificationStatus.PARTIALLY_VERIFIED,
        claims=(_claim(status=ClaimSupportStatus.PARTIALLY_SUPPORTED),),
        verified_citation_labels=(),
        unsupported_claim_count=0,
        conflict_count=0,
    )
    verifier = FakeSourceVerifier(result=result)
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert response.citations == []
    assert response.source_verification_status is SourceVerificationStatus.PARTIALLY_VERIFIED
    assert response.refusal_reason is not None


def test_unsupported_result_fails_closed() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    result = _verification_result(
        status=SourceVerificationStatus.REJECTED,
        claims=(_claim(status=ClaimSupportStatus.UNSUPPORTED),),
        verified_citation_labels=(),
        unsupported_claim_count=1,
        conflict_count=0,
        reason_codes=("unsupported_claim",),
    )
    verifier = FakeSourceVerifier(result=result)
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert response.citations == []
    assert response.source_verification_status is SourceVerificationStatus.REJECTED


def test_conflicting_result_fails_closed() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    result = _verification_result(
        status=SourceVerificationStatus.REJECTED,
        claims=(_claim(status=ClaimSupportStatus.CONFLICTING),),
        verified_citation_labels=(),
        unsupported_claim_count=0,
        conflict_count=1,
        reason_codes=("conflicting_claim",),
    )
    verifier = FakeSourceVerifier(result=result)
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert response.citations == []
    assert response.source_verification_status is SourceVerificationStatus.REJECTED


# --- verifier unavailable/failed ------------------------------------------------


def test_verifier_failure_returns_no_citations() -> None:
    router, provider = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    verifier = FakeSourceVerifier(
        fail_with=SourceVerificationExecutionError("fake-verifier", "boom")
    )
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert response.citations == []
    assert response.source_verification_status is SourceVerificationStatus.FAILED
    assert response.refusal_reason is not None


# --- retrieval gaps preserved across every failure path -------------------------


def test_every_failed_verification_preserves_retrieval_gaps() -> None:
    gap = "only partial coverage of the objective"

    # provider failure
    router, _ = _router_for(
        FakeLocalProvider(fail_with=ProviderExecutionError("fake-local", "boom"))
    )
    bundle = _bundle(gaps=[gap])
    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
        )
    )
    assert response.retrieval_gaps == [gap]

    # citation-integrity failure
    router, _ = _router_for(FakeLocalProvider(output_text="No citation."))
    bundle = _bundle(gaps=[gap])
    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
        )
    )
    assert response.retrieval_gaps == [gap]

    # verifier failure
    router, _ = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    bundle = _bundle(gaps=[gap])
    verifier = FakeSourceVerifier(fail_with=SourceVerificationExecutionError("v", "boom"))
    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )
    assert response.retrieval_gaps == [gap]

    # non-VERIFIED verifier result
    router, _ = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    bundle = _bundle(gaps=[gap])
    verifier = FakeSourceVerifier(
        result=_verification_result(
            status=SourceVerificationStatus.PARTIALLY_VERIFIED,
            claims=(),
            verified_citation_labels=(),
        )
    )
    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )
    assert response.retrieval_gaps == [gap]


# --- no failure reason ever contains prompt/response/evidence text -------------


def test_no_failure_reason_contains_prompt_text() -> None:
    marker = "SECRET-PROMPT-MARKER"
    router, _ = _router_for(FakeLocalProvider(fail_with=ProviderExecutionError("p", "boom")))
    bundle = _bundle(evidence=[_evidence(text=f"{marker} evidence text.")])

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(
                retrieve_grounding=False,
                grounding_bundle=bundle,
                learning_objective=f"Explain {marker}",
            ),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
        )
    )

    assert response.refusal_reason is not None
    assert marker not in response.refusal_reason


def test_no_failure_reason_contains_generated_response_text() -> None:
    marker = "SECRET-GENERATED-RESPONSE-MARKER"
    router, _ = _router_for(FakeLocalProvider(output_text=f"{marker} no citation here."))
    bundle = _bundle()

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
        )
    )

    assert response.citation_integrity_status is CitationIntegrityStatus.FAILED
    assert response.refusal_reason is not None
    assert marker not in response.refusal_reason


def test_no_failure_reason_contains_evidence_text() -> None:
    marker = "SECRET-EVIDENCE-TEXT-MARKER"
    router, _ = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
    bundle = _bundle(evidence=[_evidence(text=f"The OSPF DR election. {marker}")])
    verifier = FakeSourceVerifier(
        result=_verification_result(
            status=SourceVerificationStatus.REJECTED,
            claims=(_claim(status=ClaimSupportStatus.UNSUPPORTED),),
            verified_citation_labels=(),
            unsupported_claim_count=1,
            reason_codes=("unsupported_claim",),
        )
    )

    response = asyncio.run(
        answer_from_bundle(
            request=_teaching_request(retrieve_grounding=False, grounding_bundle=bundle),
            bundle=bundle,
            router=router,
            budget_policy=_budget_policy(),
            source_verifier=verifier,
        )
    )

    assert response.refusal_reason is not None
    assert marker not in response.refusal_reason


# --- determinism -----------------------------------------------------------------


def test_equivalent_inputs_and_fake_results_produce_equivalent_outputs() -> None:
    def build() -> tuple[DeterministicRouter, GroundingBundle, FakeSourceVerifier]:
        router, _ = _router_for(FakeLocalProvider(output_text="Priority wins. [E1]"))
        bundle = _bundle()
        verifier = FakeSourceVerifier(result=_verification_result(semantic_confidence=0.6))
        return router, bundle, verifier

    request = _teaching_request(retrieve_grounding=False, grounding_bundle=_bundle())

    router_a, bundle_a, verifier_a = build()
    first = asyncio.run(
        answer_from_bundle(
            request=request,
            bundle=bundle_a,
            router=router_a,
            budget_policy=_budget_policy(),
            source_verifier=verifier_a,
        )
    )

    router_b, bundle_b, verifier_b = build()
    second = asyncio.run(
        answer_from_bundle(
            request=request,
            bundle=bundle_b,
            router=router_b,
            budget_policy=_budget_policy(),
            source_verifier=verifier_b,
        )
    )

    assert first.model_dump(exclude={"response_id", "created_at"}) == second.model_dump(
        exclude={"response_id", "created_at"}
    )
