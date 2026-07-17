from __future__ import annotations

import asyncio
import socket
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from personal_lms.content import SQLiteContentRepository
from personal_lms.domain import (
    BudgetPolicy,
    CitationIntegrityStatus,
    ContentChunk,
    CorpusDocument,
    GroundingBundle,
    LibrarianRetrievalRequest,
    PrivacyClassification,
    RetrievedEvidence,
    SourceCitation,
    SourceProcessingStatus,
    TutorTeachingRequest,
)
from personal_lms.domain.models import ModelRequest, ModelResult
from personal_lms.librarian import LibrarianContentGroundingService
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers import FakeHostedProvider, FakeLocalProvider, ProviderRegistry
from personal_lms.providers.errors import ProviderExecutionError
from personal_lms.tutor import EvidenceCheckedTutorService, TutorTeachingCoordinator


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


class _CountingGroundingService:
    """Wraps a real LibrarianContentGroundingService, counting retrieve() calls.

    Used to prove the supplied-bundle and general-knowledge modes never
    trigger retrieval — not even indirectly through the injected
    EvidenceCheckedTutorService.
    """

    def __init__(self, inner: LibrarianContentGroundingService) -> None:
        self._inner = inner
        self.retrieve_calls: list[LibrarianRetrievalRequest] = []

    def retrieve(self, request: LibrarianRetrievalRequest, **kwargs: object) -> GroundingBundle:
        self.retrieve_calls.append(request)
        return self._inner.retrieve(request, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def repo() -> SQLiteContentRepository:
    store = SQLiteContentRepository.open(":memory:")
    store.initialize_schema()
    return store


def _coordinator(
    provider: object, *, repo: SQLiteContentRepository | None = None
) -> tuple[TutorTeachingCoordinator, _CapturingProvider, _CountingGroundingService]:
    store = repo if repo is not None else SQLiteContentRepository.open(":memory:")
    if repo is None:
        store.initialize_schema()
    counting_grounding = _CountingGroundingService(LibrarianContentGroundingService(store))
    registry = ProviderRegistry()
    capturing = _CapturingProvider(provider)  # type: ignore[arg-type]
    registry.register(capturing)  # type: ignore[arg-type]
    router = DeterministicRouter(registry)
    evidence_checked = EvidenceCheckedTutorService(counting_grounding, router)  # type: ignore[arg-type]
    coordinator = TutorTeachingCoordinator(evidence_checked, router)
    return coordinator, capturing, counting_grounding


# --- Mode 1: retrieve_grounding delegates unchanged --------------------------


def test_retrieve_grounding_delegates_exactly_once_to_evidence_checked_service(
    repo: SQLiteContentRepository,
) -> None:
    repo.upsert_document(
        CorpusDocument.model_validate(
            {
                "document_id": "doc-1",
                "source_id": "src-1",
                "title": "Routing Concepts",
                "content_hash": "a" * 64,
                "status": SourceProcessingStatus.APPROVED,
            }
        )
    )
    repo.upsert_chunk(
        ContentChunk.model_validate(
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "source_id": "src-1",
                "ordinal": 0,
                "text": "The OSPF DR election is decided by priority, then router ID.",
                "text_hash": "a" * 64,
                "status": SourceProcessingStatus.APPROVED,
                "trusted_for_rag": True,
            }
        )
    )
    local = _CapturingProvider(FakeLocalProvider(output_text="Priority wins. [E1]"))
    counting_grounding = _CountingGroundingService(LibrarianContentGroundingService(repo))
    registry = ProviderRegistry()
    registry.register(local)  # type: ignore[arg-type]
    router = DeterministicRouter(registry)
    evidence_checked = EvidenceCheckedTutorService(counting_grounding, router)  # type: ignore[arg-type]
    coordinator = TutorTeachingCoordinator(evidence_checked, router)

    response = asyncio.run(coordinator.teach(_teaching_request(), budget_policy=_budget_policy()))

    assert len(counting_grounding.retrieve_calls) == 1
    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED
    assert len(local.requests) == 1


def test_retrieve_grounding_mode_does_not_perform_duplicate_retrieval(
    repo: SQLiteContentRepository,
) -> None:
    local = _CapturingProvider(FakeLocalProvider())
    counting_grounding = _CountingGroundingService(LibrarianContentGroundingService(repo))
    registry = ProviderRegistry()
    registry.register(local)  # type: ignore[arg-type]
    router = DeterministicRouter(registry)
    evidence_checked = EvidenceCheckedTutorService(counting_grounding, router)  # type: ignore[arg-type]
    coordinator = TutorTeachingCoordinator(evidence_checked, router)

    asyncio.run(coordinator.teach(_teaching_request(), budget_policy=_budget_policy()))

    # Empty repo: insufficient grounding, zero model calls, but retrieve()
    # itself must still have been called exactly once, never twice.
    assert len(counting_grounding.retrieve_calls) == 1
    assert local.requests == []


# --- Mode 2: supplied grounding bundle ----------------------------------------


def test_supplied_bundle_performs_zero_retrieval_calls() -> None:
    coordinator, local, counting_grounding = _coordinator(FakeLocalProvider())
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=_bundle())

    asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert counting_grounding.retrieve_calls == []


def test_supplied_bundle_routes_and_generates_exactly_once() -> None:
    coordinator, local, _ = _coordinator(FakeLocalProvider(output_text="Priority wins. [E1]"))
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=_bundle())

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert len(local.requests) == 1
    assert response.citation_integrity_status is CitationIntegrityStatus.VERIFIED


def test_supplied_bundle_returns_only_used_trusted_citations() -> None:
    evidence = [
        _evidence(chunk_id="chunk-1", citation=SourceCitation(source_id="src-1", title="A")),
        _evidence(chunk_id="chunk-2", citation=SourceCitation(source_id="src-2", title="B")),
    ]
    bundle = _bundle(evidence=evidence)
    coordinator, local, _ = _coordinator(FakeLocalProvider(output_text="Priority wins. [E1]"))
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=bundle)

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert len(response.citations) == 1
    assert response.citations[0].source_id == "src-1"


def test_supplied_bundle_excludes_unused_citations() -> None:
    evidence = [
        _evidence(chunk_id="chunk-1", citation=SourceCitation(source_id="src-1", title="A")),
        _evidence(chunk_id="chunk-2", citation=SourceCitation(source_id="src-2", title="B")),
    ]
    bundle = _bundle(evidence=evidence)
    coordinator, local, _ = _coordinator(FakeLocalProvider(output_text="Priority wins. [E1]"))
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=bundle)

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    cited_source_ids = [c.source_id for c in response.citations]
    assert "src-2" not in cited_source_ids


def test_supplied_bundle_rejects_unknown_citation_labels() -> None:
    coordinator, local, _ = _coordinator(FakeLocalProvider(output_text="Priority wins. [E99]"))
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=_bundle())

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert response.citation_integrity_status is CitationIntegrityStatus.FAILED
    assert response.citations == []


def test_supplied_bundle_preserves_retrieval_gaps() -> None:
    bundle = _bundle(gaps=["only partial coverage of the objective"])
    coordinator, local, _ = _coordinator(FakeLocalProvider(output_text="Priority wins. [E1]"))
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=bundle)

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert response.retrieval_gaps == ["only partial coverage of the objective"]


def test_supplied_bundle_with_only_untrusted_evidence_is_insufficient() -> None:
    bundle = _bundle(evidence=[_evidence(trusted_for_rag=False)])
    coordinator, local, _ = _coordinator(FakeLocalProvider())
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=bundle)

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert local.requests == []
    assert response.citations == []
    assert response.refusal_reason is not None


def test_supplied_bundle_does_not_get_supplemented_with_fresh_retrieval() -> None:
    """Even when the supplied bundle alone is insufficient, the coordinator
    must not silently fall back to retrieving more evidence itself."""
    bundle = _bundle(evidence=[], is_sufficient=False, gaps=["nothing supplied"])
    coordinator, local, counting_grounding = _coordinator(FakeLocalProvider())
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=bundle)

    asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert counting_grounding.retrieve_calls == []
    assert local.requests == []


# --- Mode 3: general knowledge acknowledged -----------------------------------


def test_general_knowledge_mode_performs_zero_retrieval_calls() -> None:
    coordinator, local, counting_grounding = _coordinator(FakeLocalProvider())
    request = _teaching_request(retrieve_grounding=False, general_knowledge_acknowledged=True)

    asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert counting_grounding.retrieve_calls == []


def test_general_knowledge_mode_routes_and_generates_once() -> None:
    coordinator, local, _ = _coordinator(FakeLocalProvider(output_text="General answer."))
    request = _teaching_request(retrieve_grounding=False, general_knowledge_acknowledged=True)

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert len(local.requests) == 1
    assert response.explanation == "General answer."


def test_general_knowledge_mode_returns_no_citations() -> None:
    coordinator, local, _ = _coordinator(
        FakeLocalProvider(output_text="A general answer citing nothing real [E1].")
    )
    request = _teaching_request(retrieve_grounding=False, general_knowledge_acknowledged=True)

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert response.citations == []
    assert response.grounded_in_general_knowledge is True


def test_general_knowledge_mode_does_not_claim_verified_citation_integrity() -> None:
    coordinator, local, _ = _coordinator(FakeLocalProvider(output_text="A general answer."))
    request = _teaching_request(retrieve_grounding=False, general_knowledge_acknowledged=True)

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert response.citation_integrity_status is not CitationIntegrityStatus.VERIFIED
    assert response.citation_integrity_status is CitationIntegrityStatus.NOT_APPLICABLE
    assert response.grounding_is_sufficient is None


def test_general_knowledge_mode_keeps_conservative_confidence() -> None:
    coordinator, local, _ = _coordinator(FakeLocalProvider(output_text="A general answer."))
    request = _teaching_request(retrieve_grounding=False, general_knowledge_acknowledged=True)

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert response.confidence == 0.0


# --- privacy propagation across all three modes -------------------------------


def test_privacy_classification_propagates_identically_in_all_three_modes() -> None:
    retrieve_coordinator, retrieve_local, _ = _coordinator(FakeLocalProvider())
    retrieve_request = _teaching_request(privacy_classification=PrivacyClassification.SENSITIVE)

    supplied_coordinator, supplied_local, _ = _coordinator(
        FakeLocalProvider(output_text="Priority wins. [E1]")
    )
    supplied_request = _teaching_request(
        retrieve_grounding=False,
        grounding_bundle=_bundle(),
        privacy_classification=PrivacyClassification.SENSITIVE,
    )

    general_coordinator, general_local, _ = _coordinator(FakeLocalProvider())
    general_request = _teaching_request(
        retrieve_grounding=False,
        general_knowledge_acknowledged=True,
        privacy_classification=PrivacyClassification.SENSITIVE,
    )

    asyncio.run(retrieve_coordinator.teach(retrieve_request, budget_policy=_budget_policy()))
    asyncio.run(supplied_coordinator.teach(supplied_request, budget_policy=_budget_policy()))
    asyncio.run(general_coordinator.teach(general_request, budget_policy=_budget_policy()))

    # Mode 1's repo is empty so grounding is insufficient and no model call
    # happens; the other two modes do reach the model.
    assert supplied_local.requests[0].privacy_classification is PrivacyClassification.SENSITIVE
    assert general_local.requests[0].privacy_classification is PrivacyClassification.SENSITIVE


def test_restricted_local_only_cannot_route_to_a_hosted_provider_in_any_mode() -> None:
    hosted = FakeHostedProvider()

    supplied_coordinator, supplied_local, _ = _coordinator(hosted)
    supplied_request = _teaching_request(
        retrieve_grounding=False,
        grounding_bundle=_bundle(),
        privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
    )
    supplied_response = asyncio.run(
        supplied_coordinator.teach(supplied_request, budget_policy=_budget_policy())
    )
    assert supplied_local.requests == []
    assert supplied_response.refusal_reason is not None

    general_coordinator, general_local, _ = _coordinator(FakeHostedProvider())
    general_request = _teaching_request(
        retrieve_grounding=False,
        general_knowledge_acknowledged=True,
        privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
    )
    general_response = asyncio.run(
        general_coordinator.teach(general_request, budget_policy=_budget_policy())
    )
    assert general_local.requests == []
    assert general_response.refusal_reason is not None


# --- request_id propagation ----------------------------------------------------


def test_request_id_propagates_in_all_three_modes() -> None:
    retrieve_coordinator, _, _ = _coordinator(FakeLocalProvider())
    retrieve_request = _teaching_request()
    retrieve_response = asyncio.run(
        retrieve_coordinator.teach(retrieve_request, budget_policy=_budget_policy())
    )
    assert retrieve_response.request_id == retrieve_request.request_id

    supplied_coordinator, _, _ = _coordinator(FakeLocalProvider(output_text="Priority wins. [E1]"))
    supplied_request = _teaching_request(retrieve_grounding=False, grounding_bundle=_bundle())
    supplied_response = asyncio.run(
        supplied_coordinator.teach(supplied_request, budget_policy=_budget_policy())
    )
    assert supplied_response.request_id == supplied_request.request_id

    general_coordinator, _, _ = _coordinator(FakeLocalProvider())
    general_request = _teaching_request(
        retrieve_grounding=False, general_knowledge_acknowledged=True
    )
    general_response = asyncio.run(
        general_coordinator.teach(general_request, budget_policy=_budget_policy())
    )
    assert general_response.request_id == general_request.request_id


# --- failure handling -----------------------------------------------------------


def test_routing_errors_are_not_handled_as_provider_execution_failures() -> None:
    """No provider at all is registered, so routing itself fails
    (NoCompatibleProviderError) — this must never reach the provider and
    must never be reported as a provider-failure reason."""
    registry = ProviderRegistry()
    router = DeterministicRouter(registry)
    store = SQLiteContentRepository.open(":memory:")
    store.initialize_schema()
    grounding_service = LibrarianContentGroundingService(store)
    evidence_checked = EvidenceCheckedTutorService(grounding_service, router)
    coordinator = TutorTeachingCoordinator(evidence_checked, router)
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=_bundle())

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert response.citations == []
    assert response.refusal_reason is not None
    assert "no eligible" in response.refusal_reason
    assert "provider failed" not in response.refusal_reason


def test_provider_failures_return_no_citations() -> None:
    failing = FakeLocalProvider(fail_with=ProviderExecutionError("fake-local", "simulated failure"))
    coordinator, local, _ = _coordinator(failing)
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=_bundle())

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert response.citations == []
    assert response.refusal_reason is not None
    assert "provider" in response.refusal_reason


def test_prompt_text_is_absent_from_safe_failure_details() -> None:
    registry = ProviderRegistry()
    router = DeterministicRouter(registry)
    store = SQLiteContentRepository.open(":memory:")
    store.initialize_schema()
    grounding_service = LibrarianContentGroundingService(store)
    evidence_checked = EvidenceCheckedTutorService(grounding_service, router)
    coordinator = TutorTeachingCoordinator(evidence_checked, router)
    bundle = _bundle(
        evidence=[
            _evidence(
                text="SECRET-PROMPT-CONTENT-MARKER-12345",
                citation=SourceCitation(source_id="src-1", title="A"),
            )
        ]
    )
    request = _teaching_request(retrieve_grounding=False, grounding_bundle=bundle)

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert response.refusal_reason is not None
    assert "SECRET-PROMPT-CONTENT-MARKER-12345" not in response.refusal_reason


# --- legacy request compatibility -----------------------------------------------


def test_old_serialized_valid_requests_continue_to_execute_through_the_coordinator() -> None:
    """A request JSON payload shaped like it predates privacy_classification/
    retrieve_grounding (general_knowledge_acknowledged mode only) still
    executes correctly end to end through the coordinator."""
    old_shaped_json = (
        '{"request_id": "'
        + str(uuid4())
        + '", "agent_request_id": "'
        + str(uuid4())
        + '", "learning_objective": "x", "grounding_bundle": null, '
        '"general_knowledge_acknowledged": true, "knowledge_scope": null, '
        '"created_at": "2026-01-01T00:00:00Z"}'
    )
    request = TutorTeachingRequest.model_validate_json(old_shaped_json)
    coordinator, local, _ = _coordinator(FakeLocalProvider(output_text="A general answer."))

    response = asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert response.citations == []
    assert len(local.requests) == 1
    assert local.requests[0].privacy_classification is PrivacyClassification.INTERNAL


# --- determinism / no hidden state ----------------------------------------------


def test_repeated_equivalent_inputs_produce_equivalent_response_structure() -> None:
    request = _teaching_request(retrieve_grounding=False, general_knowledge_acknowledged=True)

    first_coordinator, _, _ = _coordinator(FakeLocalProvider(output_text="A general answer."))
    first = asyncio.run(first_coordinator.teach(request, budget_policy=_budget_policy()))

    second_coordinator, _, _ = _coordinator(FakeLocalProvider(output_text="A general answer."))
    second = asyncio.run(second_coordinator.teach(request, budget_policy=_budget_policy()))

    assert first.model_dump(exclude={"response_id", "created_at"}) == second.model_dump(
        exclude={"response_id", "created_at"}
    )


def test_coordinator_has_no_filesystem_effect(tmp_path: Path) -> None:
    coordinator, _, _ = _coordinator(FakeLocalProvider(output_text="A general answer."))
    request = _teaching_request(retrieve_grounding=False, general_knowledge_acknowledged=True)

    asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))

    assert list(tmp_path.iterdir()) == []


def test_coordinator_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    coordinator, _, _ = _coordinator(FakeLocalProvider(output_text="A general answer."))
    request = _teaching_request(retrieve_grounding=False, general_knowledge_acknowledged=True)

    # The event loop itself opens a self-pipe socket on construction, so
    # the loop must exist before socket.socket is blocked.
    loop = asyncio.new_event_loop()
    try:

        def _blocked(*args: object, **kwargs: object) -> None:
            raise AssertionError("no network access is permitted in the coordinator")

        monkeypatch.setattr(socket, "socket", _blocked)

        response = loop.run_until_complete(
            coordinator.teach(request, budget_policy=_budget_policy())
        )
        assert response.explanation == "A general answer."
    finally:
        monkeypatch.undo()
        loop.close()


# --- defensive fallback (not a re-validation of the schema validator) ---------


def test_coordinator_defensively_rejects_an_instance_selecting_no_mode() -> None:
    """model_construct() bypasses TutorTeachingRequest's own validator
    entirely — the only way to produce an otherwise-impossible instance
    that selects none of the three modes (all defaults: no bundle, no
    acknowledgement, no retrieve_grounding)."""
    request = TutorTeachingRequest.model_construct(agent_request_id=uuid4(), learning_objective="x")
    assert request.grounding_bundle is None
    assert request.general_knowledge_acknowledged is False
    assert request.retrieve_grounding is False

    coordinator, _, _ = _coordinator(FakeLocalProvider())

    with pytest.raises(ValueError, match="selects none of"):
        asyncio.run(coordinator.teach(request, budget_policy=_budget_policy()))
