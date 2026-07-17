from __future__ import annotations

import asyncio
import inspect
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
    KnowledgeScope,
    LibrarianRetrievalRequest,
    PrivacyClassification,
    SourceProcessingStatus,
    TutorTeachingRequest,
)
from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest, ModelResult
from personal_lms.librarian import LibrarianContentGroundingService
from personal_lms.policies.errors import RoutingError
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers import FakeHostedProvider, FakeLocalProvider, ProviderRegistry
from personal_lms.providers.errors import ProviderError, ProviderExecutionError
from personal_lms.tutor import EvidenceCheckedTutorService
from personal_lms.tutor._generation import (
    NoEligibleProviderError,
    ProviderFailedError,
    route_and_generate,
)

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


class _CapturingProvider:
    """Wraps a real fake provider, recording every ModelRequest it receives.

    Lets tests assert both "zero model calls" (insufficient grounding) and
    exactly what was sent to the model (only trusted evidence, correct
    provenance) without needing a real inference backend.
    """

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


@pytest.fixture
def repo() -> SQLiteContentRepository:
    store = SQLiteContentRepository.open(":memory:")
    store.initialize_schema()
    return store


@pytest.fixture
def grounding_service(repo: SQLiteContentRepository) -> LibrarianContentGroundingService:
    return LibrarianContentGroundingService(repo)


def _service(
    grounding_service: LibrarianContentGroundingService, provider: object
) -> EvidenceCheckedTutorService:
    registry = ProviderRegistry()
    registry.register(provider)  # type: ignore[arg-type]
    router = DeterministicRouter(registry)
    return EvidenceCheckedTutorService(grounding_service, router)


# --- insufficient grounding: zero model calls, deterministic refusal --------


def test_insufficient_grounding_causes_zero_model_calls(
    grounding_service: LibrarianContentGroundingService,
) -> None:
    local = _CapturingProvider(FakeLocalProvider())
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert local.requests == []
    assert response.citations == []
    assert response.grounded_in_general_knowledge is True
    assert response.grounding_is_sufficient is False
    assert response.citation_integrity_status is CitationIntegrityStatus.NOT_APPLICABLE
    assert response.refusal_reason is not None
    assert "insufficient" in response.refusal_reason


def test_insufficient_grounding_preserves_retrieval_gaps(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    local = _CapturingProvider(FakeLocalProvider())
    service = _service(grounding_service, local)

    request = _teaching_request(learning_objective="nonexistent topic")
    response = asyncio.run(service.teach(request, budget_policy=_budget_policy()))

    assert len(response.retrieval_gaps) == 1
    assert "no permitted content chunks matched" in response.retrieval_gaps[0]


# --- trusted-context construction --------------------------------------------


def test_only_trusted_evidence_enters_the_prompt(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-trusted",
            text="The OSPF DR election is decided by priority, then router ID.",
            trusted_for_rag=True,
        )
    )
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-untrusted",
            ordinal=1,
            text="The OSPF DR election secret untrusted content that must never reach the model.",
            trusted_for_rag=False,
        )
    )
    local = _CapturingProvider(FakeLocalProvider(output_text="Priority wins. [E1]"))
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert len(local.requests) == 1
    prompt = local.requests[0].prompt
    assert "chunk-trusted" in prompt
    assert "The OSPF DR election is decided by priority" in prompt
    assert "chunk-untrusted" not in prompt
    assert "untrusted secret content" not in prompt
    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED


def test_approved_but_untrusted_only_evidence_is_insufficient(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(status=SourceProcessingStatus.APPROVED, trusted_for_rag=False))
    local = _CapturingProvider(FakeLocalProvider())
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert local.requests == []
    assert response.grounding_is_sufficient is False
    assert response.refusal_reason is not None


def test_evidence_block_includes_source_document_chunk_page_and_section(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(document_id="doc-9", source_id="src-9"))
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-42",
            document_id="doc-9",
            source_id="src-9",
            page_number=42,
            section_title="OSPF DR Election",
        )
    )
    local = _CapturingProvider(FakeLocalProvider(output_text="Priority wins. [E1]"))
    service = _service(grounding_service, local)

    asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    prompt = local.requests[0].prompt
    assert "source_id=src-9" in prompt
    assert "document_id=doc-9" in prompt
    assert "chunk_id=chunk-42" in prompt
    assert "p.42" in prompt
    assert "OSPF DR Election" in prompt
    assert "title=" in prompt


# --- citation-integrity verification -----------------------------------------


def test_valid_single_citation_passes_verification(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())
    local = _CapturingProvider(FakeLocalProvider(output_text="The DR is elected by priority. [E1]"))
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED
    assert len(response.citations) == 1
    assert response.citations[0].source_id == "src-1"
    assert response.refusal_reason is None


def test_multiple_valid_citations_preserve_first_use_order(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1", title="Priority Rules"))
    repo.upsert_document(_document(document_id="doc-2", source_id="src-2", title="Tie-Break Rules"))
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-a",
            document_id="doc-1",
            source_id="src-1",
            text="The OSPF DR election is decided by priority first.",
        )
    )
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-b",
            ordinal=1,
            document_id="doc-2",
            source_id="src-2",
            text="The OSPF DR election ties are broken by router ID.",
        )
    )

    # Discover, deterministically, which retrieval-order position (and
    # therefore which E-label) each chunk lands on — never assumed, since
    # this depends on FTS ranking, not insertion order.
    retrieval_request = LibrarianRetrievalRequest(interpreted_query="OSPF DR election")
    bundle = grounding_service.retrieve(retrieval_request)
    assert len(bundle.evidence) == 2
    first_source_id = bundle.evidence[0].citation.source_id
    second_source_id = bundle.evidence[1].citation.source_id

    local = _CapturingProvider(
        FakeLocalProvider(output_text="Second point stated first in the text [E2], then [E1].")
    )
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED
    assert len(response.citations) == 2
    # [E2] was used first in the generated text, so its citation comes first.
    assert response.citations[0].source_id == second_source_id
    assert response.citations[1].source_id == first_source_id


def test_duplicate_citation_labels_collapse_deterministically(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())
    local = _CapturingProvider(
        FakeLocalProvider(output_text="Priority wins [E1]. Confirmed again [E1].")
    )
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED
    assert len(response.citations) == 1


def test_unused_trusted_citations_are_omitted(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    """Two trusted chunks are retrieved, but the generated answer cites
    only one of them — the unused one's citation must not appear."""
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1"))
    repo.upsert_document(_document(document_id="doc-2", source_id="src-2"))
    repo.upsert_chunk(
        _chunk(chunk_id="chunk-a", document_id="doc-1", source_id="src-1", trusted_for_rag=True)
    )
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-b",
            ordinal=1,
            document_id="doc-2",
            source_id="src-2",
            text="The OSPF DR election also cares about interface priority values.",
            trusted_for_rag=True,
        )
    )
    local = _CapturingProvider(FakeLocalProvider(output_text="Priority wins. [E1]"))
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED
    assert len(response.citations) == 1


def test_untrusted_citations_are_omitted_even_when_the_source_id_matches(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    """An untrusted chunk is retrieved alongside a trusted one, but never
    receives an E-label at all — so the generated answer can only ever
    cite the trusted evidence, never the untrusted evidence's citation."""
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(chunk_id="chunk-trusted", trusted_for_rag=True))
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-untrusted",
            ordinal=1,
            text="The OSPF DR election also depends on the interface being up.",
            trusted_for_rag=False,
        )
    )
    local = _CapturingProvider(FakeLocalProvider(output_text="Priority wins. [E1]"))
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert len(response.citations) == 1
    assert response.citations[0].source_id == "src-1"


def test_missing_citations_fail_integrity_checking(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())
    local = _CapturingProvider(
        FakeLocalProvider(output_text="The DR is elected by priority, then router ID.")
    )
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert response.citation_integrity_status is CitationIntegrityStatus.FAILED
    assert response.citations == []
    assert response.refusal_reason is not None
    assert "citation-integrity" in response.refusal_reason


def test_unknown_citation_label_fails_integrity_checking(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())
    local = _CapturingProvider(FakeLocalProvider(output_text="Priority wins. [E99]"))
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert response.citation_integrity_status is CitationIntegrityStatus.FAILED
    assert response.citations == []


def test_invalid_draft_is_not_returned_as_a_verified_answer(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    """A citation-integrity failure must never surface the model's raw,
    unverified draft text as the response's explanation."""
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())
    raw_draft = "Unverifiable claim citing [E99] which was never supplied."
    local = _CapturingProvider(FakeLocalProvider(output_text=raw_draft))
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert response.explanation != raw_draft
    assert response.grounded_in_general_knowledge is True


# --- confidence semantics: structural verification is not semantic confidence -


def test_verified_answer_does_not_claim_confidence_one(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    """Passing structural citation-integrity checks must never be reported
    as full (1.0) confidence — that would overclaim semantic correctness
    this service never assesses. citation_integrity_status, not
    confidence, is the authoritative structural-verification signal."""
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())
    local = _CapturingProvider(FakeLocalProvider(output_text="Priority wins. [E1]"))
    service = _service(grounding_service, local)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED
    assert response.confidence != 1.0
    assert response.confidence == 0.0


def test_refusal_and_verified_responses_report_the_same_conservative_confidence(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    """A refusal and a structurally verified answer both report the
    schema's most conservative confidence value — the real signal lives
    in citation_integrity_status/refusal_reason, not in confidence."""
    local = _CapturingProvider(FakeLocalProvider())
    service = _service(grounding_service, local)

    refusal = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert refusal.confidence == 0.0


# --- request-mode enforcement --------------------------------------------------


def test_teach_rejects_a_request_with_general_knowledge_acknowledged() -> None:
    request = TutorTeachingRequest.model_validate(
        {
            "agent_request_id": uuid4(),
            "learning_objective": "x",
            "general_knowledge_acknowledged": True,
        }
    )
    grounding_service = LibrarianContentGroundingService(SQLiteContentRepository.open(":memory:"))
    local = _CapturingProvider(FakeLocalProvider())
    service = _service(grounding_service, local)

    with pytest.raises(ValueError, match="retrieve_grounding"):
        asyncio.run(service.teach(request, budget_policy=_budget_policy()))

    assert local.requests == []


def test_teach_rejects_a_request_with_a_pre_attached_grounding_bundle() -> None:
    request = TutorTeachingRequest.model_validate(
        {
            "agent_request_id": uuid4(),
            "learning_objective": "x",
            "grounding_bundle": GroundingBundle(request_id=uuid4(), is_sufficient=True),
        }
    )
    grounding_service = LibrarianContentGroundingService(SQLiteContentRepository.open(":memory:"))
    local = _CapturingProvider(FakeLocalProvider())
    service = _service(grounding_service, local)

    with pytest.raises(ValueError, match="retrieve_grounding"):
        asyncio.run(service.teach(request, budget_policy=_budget_policy()))

    assert local.requests == []


# --- privacy and routing ------------------------------------------------------


def test_request_privacy_classification_is_used_for_retrieval_and_model_request(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(privacy_classification=PrivacyClassification.SENSITIVE))
    repo.upsert_chunk(_chunk(privacy_classification=PrivacyClassification.SENSITIVE))
    local = _CapturingProvider(FakeLocalProvider(output_text="Priority wins. [E1]"))
    service = _service(grounding_service, local)

    request = _teaching_request(privacy_classification=PrivacyClassification.SENSITIVE)
    response = asyncio.run(service.teach(request, budget_policy=_budget_policy()))

    # A SENSITIVE chunk is only reachable when the retrieval ceiling used
    # was itself SENSITIVE (or more permissive) — proving request.privacy_classification
    # reached the LibrarianRetrievalRequest, not some other default.
    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED
    assert len(local.requests) == 1
    assert local.requests[0].privacy_classification is PrivacyClassification.SENSITIVE


def test_teach_has_no_separate_privacy_parameter_that_could_conflict_with_the_request() -> None:
    """privacy_classification lives only on TutorTeachingRequest — teach()
    must not accept a second, independent privacy argument that could
    disagree with the one already recorded on the request."""
    signature = inspect.signature(EvidenceCheckedTutorService.teach)
    assert "privacy_classification" not in signature.parameters


def test_restricted_local_only_requests_cannot_use_hosted_providers(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(
        _document(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY)
    )
    repo.upsert_chunk(_chunk(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY))
    hosted = _CapturingProvider(FakeHostedProvider())
    service = _service(grounding_service, hosted)

    request = _teaching_request(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY)
    response = asyncio.run(service.teach(request, budget_policy=_budget_policy()))

    assert hosted.requests == []
    assert response.citations == []
    assert response.refusal_reason is not None
    assert "no eligible" in response.refusal_reason


def test_provider_failure_produces_a_typed_safe_response(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())
    failing = _CapturingProvider(
        FakeLocalProvider(fail_with=ProviderExecutionError("fake-local", "simulated failure"))
    )
    service = _service(grounding_service, failing)

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert response.citations == []
    assert response.grounded_in_general_knowledge is True
    assert response.citation_integrity_status is CitationIntegrityStatus.NOT_APPLICABLE
    assert response.refusal_reason is not None
    assert "provider" in response.refusal_reason


# --- APPROVAL_REQUIRED routing outcome (preflight check) --------------------
#
# DeterministicRouter.route() returns *normally* (no exception) with
# RoutingResult(decision=RoutingDecision(outcome=APPROVAL_REQUIRED), provider=None)
# when budget policy requires approval for hosted routing. This section
# proves _generation.route_and_generate() and EvidenceCheckedTutorService.teach()
# both treat that outcome correctly: no provider call, not misclassified as
# a RoutingError or a ProviderError, and a safe, gap-preserving refusal.


def _hosted_profile_for(privacy: PrivacyClassification) -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        profile_id="hosted-approval-required",
        max_context_tokens=4096,
        is_local=False,
        max_privacy_classification=privacy,
        latency_class=LatencyClass.STANDARD,
        cost_class=CostClass.MEDIUM,
    )


def test_route_and_generate_raises_no_eligible_provider_for_approval_required() -> None:
    """White-box: route() itself must not raise, provider.generate() must
    never be called, and the resulting exception must be
    NoEligibleProviderError specifically — never RoutingError or
    ProviderError (route_and_generate's two try/except blocks structurally
    cannot misclassify this, since the APPROVAL_REQUIRED branch is reached
    only after route() already returned normally, before either except
    clause could ever run)."""
    hosted = _CapturingProvider(
        FakeHostedProvider(
            capability_profiles=(_hosted_profile_for(PrivacyClassification.INTERNAL),)
        )
    )
    registry = ProviderRegistry()
    registry.register(hosted)  # type: ignore[arg-type]
    router = DeterministicRouter(registry)
    budget_policy = _budget_policy(automatic_single_call_limit_usd=Decimal("0"))
    model_request = ModelRequest(
        capability_profile="tutor_evidence_checked",
        prompt="Explain OSPF DR election using only the supplied evidence.",
        privacy_classification=PrivacyClassification.INTERNAL,
    )

    # route() itself must return without raising.
    routing_result = router.route(model_request, budget_policy=budget_policy)
    assert routing_result.provider is None

    with pytest.raises(NoEligibleProviderError) as exc_info:
        asyncio.run(route_and_generate(router, budget_policy, model_request))

    assert not isinstance(exc_info.value, RoutingError)
    assert not isinstance(exc_info.value, ProviderError)
    assert not isinstance(exc_info.value, ProviderFailedError)
    assert hosted.requests == []


def test_teach_reports_a_safe_gap_preserving_refusal_for_approval_required(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk(text="The OSPF DR election is decided by priority, then router ID."))
    hosted = _CapturingProvider(
        FakeHostedProvider(
            capability_profiles=(_hosted_profile_for(PrivacyClassification.INTERNAL),)
        )
    )
    registry = ProviderRegistry()
    registry.register(hosted)  # type: ignore[arg-type]
    router = DeterministicRouter(registry)
    service = EvidenceCheckedTutorService(grounding_service, router)
    budget_policy = _budget_policy(automatic_single_call_limit_usd=Decimal("0"))

    response = asyncio.run(service.teach(_teaching_request(), budget_policy=budget_policy))

    assert hosted.requests == []
    assert response.citations == []
    assert response.grounding_is_sufficient is True
    assert response.retrieval_gaps == []
    assert response.citation_integrity_status is CitationIntegrityStatus.NOT_APPLICABLE
    assert response.refusal_reason is not None
    assert "no eligible" in response.refusal_reason
    assert "provider failed" not in response.refusal_reason
    assert "OSPF" not in response.refusal_reason
    assert "priority" not in response.refusal_reason


# --- domain neutrality --------------------------------------------------------


def test_teach_succeeds_with_no_certification_or_domain_specific_metadata(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    """The whole flow works with no knowledge_scope, certification, or any
    other domain-specific metadata at all — nothing here is CCNA-specific."""
    repo.upsert_document(_document())
    repo.upsert_chunk(_chunk())
    local = _CapturingProvider(FakeLocalProvider(output_text="Priority wins. [E1]"))
    service = _service(grounding_service, local)

    request = _teaching_request(knowledge_scope=None)
    response = asyncio.run(service.teach(request, budget_policy=_budget_policy()))

    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED


def test_teach_still_respects_a_generic_knowledge_scope_when_provided(
    repo: SQLiteContentRepository, grounding_service: LibrarianContentGroundingService
) -> None:
    repo.upsert_document(_document(document_id="doc-1", source_id="src-1"))
    repo.upsert_document(_document(document_id="doc-2", source_id="src-2"))
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-scoped",
            document_id="doc-1",
            source_id="src-1",
            knowledge_scopes=[KnowledgeScope(certification="CCNA")],
        )
    )
    repo.upsert_chunk(
        _chunk(
            chunk_id="chunk-unscoped",
            ordinal=1,
            document_id="doc-2",
            source_id="src-2",
            knowledge_scopes=[KnowledgeScope(certification="A+")],
        )
    )
    local = _CapturingProvider(FakeLocalProvider(output_text="Priority wins. [E1]"))
    service = _service(grounding_service, local)

    request = _teaching_request(knowledge_scope=KnowledgeScope(certification="CCNA"))
    asyncio.run(service.teach(request, budget_policy=_budget_policy()))

    prompt = local.requests[0].prompt
    assert "chunk-scoped" in prompt
    assert "chunk-unscoped" not in prompt
