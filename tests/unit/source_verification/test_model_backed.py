from __future__ import annotations

import asyncio
import inspect
import json
import os
import socket
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from personal_lms.domain import (
    BudgetPolicy,
    GroundingBundle,
    PrivacyClassification,
    RetrievedEvidence,
    SourceCitation,
    SourceVerificationRequest,
)
from personal_lms.domain.enums import CostClass, LatencyClass, RoutingOutcome
from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest, ModelResult
from personal_lms.domain.routing import RoutingDecision
from personal_lms.policies.errors import NoCompatibleProviderError, RoutingError
from personal_lms.policies.router import DeterministicRouter, RoutingResult
from personal_lms.providers import FakeHostedProvider, FakeLocalProvider, ProviderRegistry
from personal_lms.providers.errors import (
    ProviderContractError,
    ProviderError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from personal_lms.source_verification import (
    ModelBackedSourceVerifier,
    SourceVerificationContractError,
    SourceVerificationExecutionError,
    SourceVerificationPrivacyError,
    SourceVerificationRoutingPolicy,
    SourceVerificationUnavailableError,
    SourceVerifier,
)
from personal_lms.source_verification.model_backed import (
    _build_prompt,
    _resolve_used_evidence,
    _to_model_request_id,
)


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


def _request(**overrides: object) -> SourceVerificationRequest:
    defaults: dict[str, object] = {
        "request_id": str(uuid4()),
        "generated_text": "The DR is elected by priority. [E1]",
        "grounding_bundle": _bundle(),
        "used_citation_labels": ("E1",),
        "privacy_classification": PrivacyClassification.INTERNAL,
    }
    defaults.update(overrides)
    return SourceVerificationRequest.model_validate(defaults)


def _budget_policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "policy_id": "default",
        "daily_limit_usd": Decimal("3.00"),
        "monthly_limit_usd": Decimal("40.00"),
    }
    defaults.update(overrides)
    return BudgetPolicy.model_validate(defaults)


def _verified_payload(request_id: str, **overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "request_id": request_id,
        "status": "verified",
        "claims": [
            {"claim_id": "C1", "status": "supported", "evidence_labels": ["E1"], "reason_codes": []}
        ],
        "verified_citation_labels": ["E1"],
        "unsupported_claim_count": 0,
        "conflict_count": 0,
        "semantic_confidence": None,
        "reason_codes": [],
    }
    defaults.update(overrides)
    return defaults


def _verified_json(request_id: str, **overrides: object) -> str:
    return json.dumps(_verified_payload(request_id, **overrides))


class _CapturingProvider:
    """Wraps a real fake provider, recording every ModelRequest it receives."""

    def __init__(self, inner: FakeLocalProvider | FakeHostedProvider) -> None:
        self._inner = inner
        self.requests: list[ModelRequest] = []

    @property
    def provider_id(self) -> str:
        return self._inner.provider_id

    @property
    def capability_profiles(self) -> tuple[ModelCapabilityProfile, ...]:
        return self._inner.capability_profiles

    @property
    def is_local(self) -> bool:
        return self._inner.is_local

    async def generate(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        return await self._inner.generate(request)


class _CountingRouter:
    """Wraps a real DeterministicRouter, counting route() calls."""

    def __init__(self, inner: DeterministicRouter) -> None:
        self._inner = inner
        self.route_calls: list[ModelRequest] = []

    def route(self, request: ModelRequest, **kwargs: object) -> RoutingResult:
        self.route_calls.append(request)
        return self._inner.route(request, **kwargs)  # type: ignore[arg-type]


class _AlwaysTier0Router:
    """A duck-typed router that always reports TIER_0_DETERMINISTIC with no
    provider, regardless of what deterministic_capable was passed — used only
    to exercise ModelBackedSourceVerifier's defensive contract-violation
    branch, which is otherwise structurally unreachable through the real
    router (deterministic_capable is always False for semantic verification)."""

    def route(self, request: ModelRequest, **kwargs: object) -> RoutingResult:
        return RoutingResult(
            decision=RoutingDecision(
                outcome=RoutingOutcome.TIER_0_DETERMINISTIC, reasons=["forced"]
            ),
            provider=None,
        )


def _local_profile(**overrides: object) -> ModelCapabilityProfile:
    defaults: dict[str, object] = {
        "profile_id": "local-verify",
        "supports_reasoning": True,
        "max_context_tokens": 8192,
        "is_local": True,
        "max_privacy_classification": PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        "latency_class": LatencyClass.STANDARD,
        "cost_class": CostClass.FREE,
    }
    defaults.update(overrides)
    return ModelCapabilityProfile.model_validate(defaults)


def _hosted_profile(**overrides: object) -> ModelCapabilityProfile:
    defaults: dict[str, object] = {
        "profile_id": "hosted-verify",
        "supports_reasoning": True,
        "max_context_tokens": 128_000,
        "is_local": False,
        "max_privacy_classification": PrivacyClassification.PUBLIC,
        "latency_class": LatencyClass.STANDARD,
        "cost_class": CostClass.MEDIUM,
    }
    defaults.update(overrides)
    return ModelCapabilityProfile.model_validate(defaults)


def _router_with(*providers: object) -> tuple[DeterministicRouter, ProviderRegistry]:
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)  # type: ignore[arg-type]
    return DeterministicRouter(registry), registry


def _verifier(
    router: object, *, budget_policy: BudgetPolicy | None = None, **kwargs: object
) -> ModelBackedSourceVerifier:
    return ModelBackedSourceVerifier(
        verifier_id="model-backed-verifier",
        router=router,  # type: ignore[arg-type]
        budget_policy=budget_policy or _budget_policy(),
        **kwargs,  # type: ignore[arg-type]
    )


# --- construction and protocol --------------------------------------------------


def test_satisfies_source_verifier_protocol() -> None:
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    assert isinstance(verifier, SourceVerifier)


def test_empty_verifier_id_is_rejected() -> None:
    router, _ = _router_with(FakeLocalProvider())
    with pytest.raises(ValueError, match="verifier_id"):
        ModelBackedSourceVerifier(verifier_id="", router=router, budget_policy=_budget_policy())


def test_invalid_routing_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="minimum_context_tokens"):
        SourceVerificationRoutingPolicy(minimum_context_tokens=-1)


def test_constructor_performs_no_network_or_filesystem_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    router, _ = _router_with(FakeLocalProvider())

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network access is permitted in the constructor")

    monkeypatch.setattr(socket, "socket", _blocked)
    _verifier(router)
    monkeypatch.undo()

    assert list(tmp_path.iterdir()) == []


# --- evidence minimization -------------------------------------------------------


def test_only_used_citation_evidence_is_included() -> None:
    bundle = _bundle(
        evidence=[
            _evidence(chunk_id="chunk-1", text="Used evidence text."),
            _evidence(chunk_id="chunk-2", text="Unused evidence text."),
        ]
    )
    request = _request(grounding_bundle=bundle, used_citation_labels=("E1",))

    items = _resolve_used_evidence(request, verifier_id="v")

    assert [item.label for item in items] == ["E1"]
    assert items[0].evidence.text == "Used evidence text."


def test_unused_evidence_is_excluded_from_the_prompt() -> None:
    bundle = _bundle(
        evidence=[
            _evidence(chunk_id="chunk-1", text="Used evidence text."),
            _evidence(chunk_id="chunk-2", text="Unused evidence text."),
        ]
    )
    request = _request(grounding_bundle=bundle, used_citation_labels=("E1",))
    items = _resolve_used_evidence(request, verifier_id="v")

    prompt = _build_prompt(request, items)

    assert "Used evidence text." in prompt
    assert "Unused evidence text." not in prompt


def test_duplicate_used_labels_are_collapsed() -> None:
    request = _request(used_citation_labels=("E1", "E1"))
    items = _resolve_used_evidence(request, verifier_id="v")
    assert [item.label for item in items] == ["E1"]


def test_first_use_label_order_is_preserved() -> None:
    bundle = _bundle(
        evidence=[
            _evidence(chunk_id="chunk-1", text="First trusted item."),
            _evidence(chunk_id="chunk-2", text="Second trusted item."),
        ]
    )
    request = _request(grounding_bundle=bundle, used_citation_labels=("E2", "E1"))
    items = _resolve_used_evidence(request, verifier_id="v")
    assert [item.label for item in items] == ["E2", "E1"]


def test_untrusted_evidence_is_rejected_when_referenced() -> None:
    bundle = _bundle(evidence=[_evidence(trusted_for_rag=False)])
    request = _request(grounding_bundle=bundle, used_citation_labels=("E1",))
    with pytest.raises(SourceVerificationContractError):
        _resolve_used_evidence(request, verifier_id="v")


def test_missing_used_label_fails_before_routing() -> None:
    request = _request(grounding_bundle=_bundle(evidence=[]), used_citation_labels=("E1",))
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)

    with pytest.raises(SourceVerificationContractError):
        asyncio.run(verifier.verify(request))

    assert provider.requests == []


def test_unknown_label_fails_before_routing() -> None:
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    request = _request(used_citation_labels=("E1", "E2"))

    with pytest.raises(SourceVerificationContractError):
        asyncio.run(verifier.verify(request))

    assert provider.requests == []


def test_no_retrieval_related_dependency_exists() -> None:
    params = set(inspect.signature(ModelBackedSourceVerifier.__init__).parameters)
    assert not any("retriev" in p or "grounding_service" in p or "catalog" in p for p in params)


# --- prompt construction ----------------------------------------------------------


def test_prompt_order_is_deterministic() -> None:
    request = _request(generated_text="Priority wins. [E1]")
    items = _resolve_used_evidence(request, verifier_id="v")
    prompt = _build_prompt(request, items)

    task_pos = prompt.index("You are a source verifier")
    answer_pos = prompt.index("Generated answer:")
    evidence_pos = prompt.index("Evidence:")
    schema_pos = prompt.index('"request_id"')
    request_id_pos = prompt.index(f"request_id: {request.request_id}")

    assert task_pos < answer_pos < evidence_pos < schema_pos < request_id_pos


def test_equivalent_requests_produce_identical_prompt_text() -> None:
    request = _request()
    items = _resolve_used_evidence(request, verifier_id="v")

    first = _build_prompt(request, items)
    second = _build_prompt(request, items)

    assert first == second


def test_prompt_contains_generated_answer_and_selected_evidence() -> None:
    request = _request(generated_text="Unique generated answer marker XYZ. [E1]")
    items = _resolve_used_evidence(request, verifier_id="v")
    prompt = _build_prompt(request, items)

    assert "Unique generated answer marker XYZ." in prompt
    assert "The OSPF DR election is decided by priority" in prompt


def test_prompt_excludes_provider_ids_and_routing_details() -> None:
    request = _request()
    items = _resolve_used_evidence(request, verifier_id="v")
    prompt = _build_prompt(request, items)

    assert "fake-local" not in prompt
    assert "tier_1_local" not in prompt
    assert "capability_profile" not in prompt
    assert "budget_policy" not in prompt


def test_prompt_requests_strict_json() -> None:
    request = _request()
    items = _resolve_used_evidence(request, verifier_id="v")
    prompt = _build_prompt(request, items)

    assert "strict JSON" in prompt
    assert "Markdown" in prompt


def test_prompt_prohibits_general_knowledge_substitution() -> None:
    request = _request()
    items = _resolve_used_evidence(request, verifier_id="v")
    prompt = _build_prompt(request, items)

    assert "pretrained knowledge" in prompt


# --- routing ------------------------------------------------------------------------


def test_router_is_called_exactly_once() -> None:
    request = _request(generated_text="Priority wins. [E1]")
    provider = _CapturingProvider(
        FakeLocalProvider(
            output_text=_verified_json(request.request_id),
            capability_profiles=(_local_profile(),),
        )
    )
    inner_router, _ = _router_with(provider)
    counting_router = _CountingRouter(inner_router)
    verifier = _verifier(counting_router)

    asyncio.run(verifier.verify(request))

    assert len(counting_router.route_calls) == 1


def test_local_tier1_selects_one_provider_and_calls_it_once() -> None:
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    request = _request()
    provider._inner.output_text = _verified_json(request.request_id)  # type: ignore[attr-defined]

    asyncio.run(verifier.verify(request))

    assert len(provider.requests) == 1


def test_hosted_tier2_selects_one_provider_and_calls_it_once() -> None:
    provider = _CapturingProvider(FakeHostedProvider(capability_profiles=(_hosted_profile(),)))
    router, _ = _router_with(provider)
    budget_policy = _budget_policy(automatic_single_call_limit_usd=Decimal("0.50"))
    verifier = _verifier(router, budget_policy=budget_policy)
    request = _request(privacy_classification=PrivacyClassification.PUBLIC)
    provider._inner.output_text = _verified_json(request.request_id)  # type: ignore[attr-defined]

    asyncio.run(verifier.verify(request))

    assert len(provider.requests) == 1


def test_approval_required_calls_zero_providers() -> None:
    provider = _CapturingProvider(FakeHostedProvider(capability_profiles=(_hosted_profile(),)))
    router, _ = _router_with(provider)
    budget_policy = _budget_policy(automatic_single_call_limit_usd=Decimal("0"))
    verifier = _verifier(router, budget_policy=budget_policy)
    request = _request(privacy_classification=PrivacyClassification.PUBLIC)

    with pytest.raises(SourceVerificationUnavailableError):
        asyncio.run(verifier.verify(request))

    assert provider.requests == []


def test_approval_required_is_not_classified_as_routing_error() -> None:
    provider = _CapturingProvider(FakeHostedProvider(capability_profiles=(_hosted_profile(),)))
    router, _ = _router_with(provider)
    budget_policy = _budget_policy(automatic_single_call_limit_usd=Decimal("0"))
    verifier = _verifier(router, budget_policy=budget_policy)
    request = _request(privacy_classification=PrivacyClassification.PUBLIC)

    with pytest.raises(SourceVerificationUnavailableError) as exc_info:
        asyncio.run(verifier.verify(request))

    assert not isinstance(exc_info.value, RoutingError)


def test_approval_required_is_not_classified_as_provider_error() -> None:
    provider = _CapturingProvider(FakeHostedProvider(capability_profiles=(_hosted_profile(),)))
    router, _ = _router_with(provider)
    budget_policy = _budget_policy(automatic_single_call_limit_usd=Decimal("0"))
    verifier = _verifier(router, budget_policy=budget_policy)
    request = _request(privacy_classification=PrivacyClassification.PUBLIC)

    with pytest.raises(SourceVerificationUnavailableError) as exc_info:
        asyncio.run(verifier.verify(request))

    assert not isinstance(exc_info.value, ProviderError)


def test_routing_rejection_calls_zero_providers() -> None:
    router, _ = _router_with()  # empty registry
    verifier = _verifier(router)

    with pytest.raises(SourceVerificationUnavailableError):
        asyncio.run(verifier.verify(_request()))


def test_privacy_denial_maps_to_source_verification_privacy_error() -> None:
    restricted_hosted_profile = _hosted_profile(
        profile_id="hosted-restricted",
        max_privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
    )
    provider = _CapturingProvider(
        FakeHostedProvider(capability_profiles=(restricted_hosted_profile,))
    )
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    request = _request(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY)

    with pytest.raises(SourceVerificationPrivacyError):
        asyncio.run(verifier.verify(request))

    assert provider.requests == []


def test_tier_0_is_treated_as_an_internal_contract_violation() -> None:
    verifier = _verifier(_AlwaysTier0Router())

    with pytest.raises(SourceVerificationContractError):
        asyncio.run(verifier.verify(_request()))


def test_no_fallback_to_a_second_qualifying_provider() -> None:
    provider_a = _CapturingProvider(
        FakeLocalProvider("provider-a", capability_profiles=(_local_profile(profile_id="a"),))
    )
    provider_b = _CapturingProvider(
        FakeLocalProvider("provider-b", capability_profiles=(_local_profile(profile_id="b"),))
    )
    router, _ = _router_with(provider_a, provider_b)
    verifier = _verifier(router)
    request = _request()
    provider_a._inner.output_text = _verified_json(request.request_id)  # type: ignore[attr-defined]
    provider_b._inner.output_text = _verified_json(request.request_id)  # type: ignore[attr-defined]

    asyncio.run(verifier.verify(request))

    called = [p for p in (provider_a, provider_b) if p.requests]
    assert len(called) == 1


# --- provider execution -------------------------------------------------------------


def test_provider_receives_exactly_one_model_request() -> None:
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    request = _request()
    provider._inner.output_text = _verified_json(request.request_id)  # type: ignore[attr-defined]

    asyncio.run(verifier.verify(request))

    assert len(provider.requests) == 1


def test_model_request_id_matches_the_verification_request() -> None:
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    request_id = str(uuid4())
    request = _request(request_id=request_id)
    provider._inner.output_text = _verified_json(request_id)  # type: ignore[attr-defined]

    asyncio.run(verifier.verify(request))

    assert provider.requests[0].request_id == UUID(request_id)


# --- deterministic (non-UUID) request-ID correlation ---------------------------


def test_valid_uuid_string_maps_to_that_exact_uuid() -> None:
    request_id = str(uuid4())
    assert _to_model_request_id(request_id) == UUID(request_id)


def test_same_non_uuid_id_always_maps_to_the_same_uuid() -> None:
    first = _to_model_request_id("req-1")
    second = _to_model_request_id("req-1")
    assert first == second


def test_different_non_uuid_ids_map_to_different_uuids() -> None:
    assert _to_model_request_id("req-1") != _to_model_request_id("req-2")


def test_repeated_equivalent_verification_calls_send_the_same_model_request_id() -> None:
    request = _request(request_id="req-non-uuid")
    payload = _verified_json(request.request_id)

    provider_1 = _CapturingProvider(
        FakeLocalProvider(output_text=payload, capability_profiles=(_local_profile(),))
    )
    router_1, _ = _router_with(provider_1)
    asyncio.run(_verifier(router_1).verify(request))

    provider_2 = _CapturingProvider(
        FakeLocalProvider(output_text=payload, capability_profiles=(_local_profile(),))
    )
    router_2, _ = _router_with(provider_2)
    asyncio.run(_verifier(router_2).verify(request))

    assert provider_1.requests[0].request_id == provider_2.requests[0].request_id


def test_model_result_correlation_still_succeeds_for_a_non_uuid_request_id() -> None:
    request = _request(request_id="req-non-uuid")
    provider = _CapturingProvider(
        FakeLocalProvider(
            output_text=_verified_json(request.request_id),
            capability_profiles=(_local_profile(),),
        )
    )
    router, _ = _router_with(provider)

    result = asyncio.run(_verifier(router).verify(request))

    assert result.status.value == "verified"


def test_source_verification_result_request_id_remains_the_original_string() -> None:
    request = _request(request_id="req-non-uuid")
    provider = _CapturingProvider(
        FakeLocalProvider(
            output_text=_verified_json(request.request_id),
            capability_profiles=(_local_profile(),),
        )
    )
    router, _ = _router_with(provider)

    result = asyncio.run(_verifier(router).verify(request))

    assert result.request_id == "req-non-uuid"


def test_to_model_request_id_uses_no_uuid4_or_random_source() -> None:
    """Deterministic proof, not just repeated-call inference: patching
    uuid4 in the module under test must never be invoked by
    _to_model_request_id for a non-UUID id."""
    import personal_lms.source_verification.model_backed as module

    assert not hasattr(module, "uuid4")

    # Reflectively confirm uuid5 (not uuid4/random) is what the function
    # actually calls, by checking the derivation is a pure function of its
    # input (a random source would make two calls in the same process
    # diverge with overwhelming probability across many invocations).
    results = {_to_model_request_id("stable-id") for _ in range(50)}
    assert results == {_to_model_request_id("stable-id")}


def test_no_schema_or_public_api_changes() -> None:
    """SourceVerificationRequest/Result schemas and the SourceVerifier
    protocol are untouched by this correction — only the private
    ModelRequest.request_id derivation changed."""
    from personal_lms.domain.source_verification import (
        SourceVerificationRequest,
        SourceVerificationResult,
    )

    assert set(SourceVerificationRequest.model_fields) == {
        "request_id",
        "generated_text",
        "grounding_bundle",
        "used_citation_labels",
        "privacy_classification",
    }
    assert set(SourceVerificationResult.model_fields) == {
        "request_id",
        "status",
        "claims",
        "verified_citation_labels",
        "unsupported_claim_count",
        "conflict_count",
        "semantic_confidence",
        "reason_codes",
    }


def test_model_request_privacy_classification_matches_the_verification_request() -> None:
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    request = _request(privacy_classification=PrivacyClassification.SENSITIVE)
    provider._inner.output_text = _verified_json(request.request_id)  # type: ignore[attr-defined]

    asyncio.run(verifier.verify(request))

    assert provider.requests[0].privacy_classification is PrivacyClassification.SENSITIVE


def test_semantic_verification_is_not_marked_deterministic_capable() -> None:
    """An empty registry proves deterministic_capable was False: a True
    value would short-circuit to TIER_0_DETERMINISTIC before the registry
    is ever consulted, never raising NoCompatibleProviderError."""
    router = DeterministicRouter(ProviderRegistry())
    verifier = _verifier(router)

    with pytest.raises(SourceVerificationUnavailableError) as exc_info:
        asyncio.run(verifier.verify(_request()))

    assert isinstance(exc_info.value.__cause__, NoCompatibleProviderError)


def test_reasoning_requirement_follows_the_configured_routing_policy() -> None:
    non_reasoning_profile = _local_profile(profile_id="non-reasoning", supports_reasoning=False)
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(non_reasoning_profile,)))

    router, _ = _router_with(provider)
    strict_verifier = _verifier(router)  # default requires_reasoning=True
    with pytest.raises(SourceVerificationUnavailableError):
        asyncio.run(strict_verifier.verify(_request()))
    assert provider.requests == []

    router2, _ = _router_with(provider)
    lenient_verifier = _verifier(
        router2, routing_policy=SourceVerificationRoutingPolicy(requires_reasoning=False)
    )
    request = _request()
    provider._inner.output_text = _verified_json(request.request_id)  # type: ignore[attr-defined]
    asyncio.run(lenient_verifier.verify(request))
    assert len(provider.requests) == 1


def test_provider_result_request_id_mismatch_fails_safely() -> None:
    class _MismatchedProvider:
        provider_id = "mismatched"
        capability_profiles = (_local_profile(),)
        is_local = True

        async def generate(self, request: ModelRequest) -> ModelResult:
            return ModelResult(
                request_id=uuid4(),  # deliberately different from request.request_id
                capability_profile=request.capability_profile,
                is_local=True,
                output_text=_verified_json(str(request.request_id)),
                input_tokens=1,
                output_tokens=1,
                latency_ms=1.0,
                finish_reason="stop",
            )

    router, _ = _router_with(_MismatchedProvider())
    verifier = _verifier(router)

    with pytest.raises(SourceVerificationContractError):
        asyncio.run(verifier.verify(_request()))


# --- output parsing --------------------------------------------------------------


def _verify_with_output(output_text: str, request: SourceVerificationRequest | None = None):
    provider = _CapturingProvider(
        FakeLocalProvider(output_text=output_text, capability_profiles=(_local_profile(),))
    )
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    return asyncio.run(verifier.verify(request or _request()))


def test_valid_json_parses_successfully() -> None:
    request = _request()
    result = _verify_with_output(_verified_json(request.request_id), request)
    assert result.status.value == "verified"


def test_markdown_fenced_json_is_rejected() -> None:
    request = _request()
    fenced = "```json\n" + _verified_json(request.request_id) + "\n```"
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(fenced, request)


def test_leading_explanatory_prose_is_rejected() -> None:
    request = _request()
    prefixed = "Here is my analysis:\n" + _verified_json(request.request_id)
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(prefixed, request)


def test_trailing_explanatory_prose_is_rejected() -> None:
    request = _request()
    suffixed = _verified_json(request.request_id) + "\nLet me know if you need anything else."
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(suffixed, request)


def test_malformed_json_fails_safely() -> None:
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output("{not valid json")


def test_invalid_enum_values_fail_safely() -> None:
    request = _request()
    payload = _verified_payload(request.request_id, status="maybe")
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(json.dumps(payload), request)


def test_invalid_confidence_fails_safely() -> None:
    request = _request()
    payload = _verified_payload(request.request_id, semantic_confidence=1.5)
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(json.dumps(payload), request)


def test_invalid_counts_fail_safely() -> None:
    request = _request()
    payload = _verified_payload(request.request_id, unsupported_claim_count=-1)
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(json.dumps(payload), request)


def test_verified_with_unsupported_claims_fails_schema_validation() -> None:
    request = _request()
    payload = _verified_payload(
        request.request_id,
        claims=[
            {
                "claim_id": "C1",
                "status": "unsupported",
                "evidence_labels": ["E1"],
                "reason_codes": [],
            }
        ],
    )
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(json.dumps(payload), request)


def test_unknown_verified_labels_fail_cross_validation() -> None:
    request = _request(used_citation_labels=("E1",))
    payload = _verified_payload(request.request_id, verified_citation_labels=["E1", "E99"])
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(json.dumps(payload), request)


def test_unknown_claim_labels_fail_cross_validation() -> None:
    request = _request(used_citation_labels=("E1",))
    payload = _verified_payload(
        request.request_id,
        claims=[
            {
                "claim_id": "C1",
                "status": "supported",
                "evidence_labels": ["E1", "E99"],
                "reason_codes": [],
            }
        ],
    )
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(json.dumps(payload), request)


def test_parsed_request_id_mismatch_fails_safely() -> None:
    request = _request()
    payload = _verified_payload("a-completely-different-request-id")
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(json.dumps(payload), request)


def test_missing_required_fields_fail_safely() -> None:
    request = _request()
    payload = _verified_payload(request.request_id)
    del payload["status"]
    with pytest.raises(SourceVerificationContractError):
        _verify_with_output(json.dumps(payload), request)


def test_no_automatic_repair_occurs() -> None:
    """An invalid VERIFIED result (unsupported claim present) must fail
    outright — never silently downgraded to PARTIALLY_VERIFIED/REJECTED by
    the verifier itself, and never re-requested."""
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    request = _request()
    payload = _verified_payload(
        request.request_id,
        claims=[
            {
                "claim_id": "C1",
                "status": "unsupported",
                "evidence_labels": ["E1"],
                "reason_codes": [],
            }
        ],
    )
    provider._inner.output_text = json.dumps(payload)  # type: ignore[attr-defined]

    with pytest.raises(SourceVerificationContractError):
        asyncio.run(verifier.verify(request))

    assert len(provider.requests) == 1  # exactly one attempt, no retry


# --- failure mapping ---------------------------------------------------------------


class _FailingProvider:
    def __init__(self, exc: Exception) -> None:
        self.provider_id = "failing"
        self.capability_profiles = (_local_profile(),)
        self.is_local = True
        self._exc = exc
        self.calls = 0

    async def generate(self, request: ModelRequest) -> ModelResult:
        self.calls += 1
        raise self._exc


def test_provider_unavailable_maps_correctly() -> None:
    provider = _FailingProvider(ProviderUnavailableError("p", "down"))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    with pytest.raises(SourceVerificationUnavailableError):
        asyncio.run(verifier.verify(_request()))


def test_provider_timeout_maps_correctly() -> None:
    provider = _FailingProvider(ProviderTimeoutError("p", 5.0))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    with pytest.raises(SourceVerificationUnavailableError):
        asyncio.run(verifier.verify(_request()))


def test_provider_execution_failure_maps_correctly() -> None:
    provider = _FailingProvider(ProviderExecutionError("p", "boom"))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    with pytest.raises(SourceVerificationExecutionError):
        asyncio.run(verifier.verify(_request()))


def test_provider_contract_failure_maps_correctly() -> None:
    provider = _FailingProvider(ProviderContractError("p", "bad shape"))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    with pytest.raises(SourceVerificationContractError):
        asyncio.run(verifier.verify(_request()))


def test_routing_failures_remain_distinct_from_provider_failures() -> None:
    router, _ = _router_with()  # empty: routing failure
    verifier = _verifier(router)
    with pytest.raises(SourceVerificationUnavailableError) as exc_info:
        asyncio.run(verifier.verify(_request()))
    assert isinstance(exc_info.value.__cause__, NoCompatibleProviderError)

    provider = _FailingProvider(ProviderExecutionError("p", "boom"))
    router2, _ = _router_with(provider)
    verifier2 = _verifier(router2)
    with pytest.raises(SourceVerificationExecutionError):
        asyncio.run(verifier2.verify(_request()))


def test_raw_provider_output_does_not_appear_in_safe_errors() -> None:
    marker = "SECRET-RAW-PROVIDER-OUTPUT-MARKER"
    provider = _CapturingProvider(
        FakeLocalProvider(output_text=f"{marker} not json", capability_profiles=(_local_profile(),))
    )
    router, _ = _router_with(provider)
    verifier = _verifier(router)

    with pytest.raises(SourceVerificationContractError) as exc_info:
        asyncio.run(verifier.verify(_request()))

    assert marker not in str(exc_info.value)


def test_generated_answer_does_not_appear_in_safe_errors() -> None:
    marker = "SECRET-GENERATED-ANSWER-MARKER"
    request = _request(generated_text=f"{marker}. [E1]", used_citation_labels=("E1", "E2"))

    with pytest.raises(SourceVerificationContractError) as exc_info:
        _verify_with_output("irrelevant", request)

    assert marker not in str(exc_info.value)


def test_evidence_text_does_not_appear_in_safe_errors() -> None:
    marker = "SECRET-EVIDENCE-TEXT-MARKER"
    bundle = _bundle(evidence=[_evidence(text=f"Evidence containing {marker}.")])
    request = _request(grounding_bundle=bundle, used_citation_labels=("E1", "E2"))

    with pytest.raises(SourceVerificationContractError) as exc_info:
        _verify_with_output("irrelevant", request)

    assert marker not in str(exc_info.value)


def test_original_prompt_text_does_not_appear_in_safe_errors() -> None:
    marker = "SECRET-LEARNING-OBJECTIVE-MARKER"
    # SourceVerificationRequest carries no learning_objective/original-prompt
    # field at all, so it structurally cannot leak here — this proves a
    # marker never legitimately reachable by this request shape stays absent.
    request = _request()
    with pytest.raises(SourceVerificationContractError) as exc_info:
        _verify_with_output("{not valid json", request)
    assert marker not in str(exc_info.value)


# --- isolation and determinism ------------------------------------------------------


def test_verify_makes_no_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    request = _request()
    provider._inner.output_text = _verified_json(request.request_id)  # type: ignore[attr-defined]

    loop = asyncio.new_event_loop()
    try:

        def _blocked(*args: object, **kwargs: object) -> None:
            raise AssertionError("no network access is permitted in ModelBackedSourceVerifier")

        monkeypatch.setattr(socket, "socket", _blocked)
        result = loop.run_until_complete(verifier.verify(request))
        assert result.status.value == "verified"
    finally:
        monkeypatch.undo()
        loop.close()


def test_verify_has_no_filesystem_effect(tmp_path: Path) -> None:
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    request = _request()
    provider._inner.output_text = _verified_json(request.request_id)  # type: ignore[attr-defined]

    asyncio.run(verifier.verify(request))

    assert list(tmp_path.iterdir()) == []


def test_verify_ignores_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _CapturingProvider(FakeLocalProvider(capability_profiles=(_local_profile(),)))
    router, _ = _router_with(provider)
    verifier = _verifier(router)
    request = _request()
    provider._inner.output_text = _verified_json(request.request_id)  # type: ignore[attr-defined]
    monkeypatch.setenv("OPENAI_API_KEY", "should-be-ignored")

    result = asyncio.run(verifier.verify(request))

    assert result.status.value == "verified"
    assert os.environ.get("OPENAI_API_KEY") == "should-be-ignored"


def test_registration_order_does_not_change_provider_selection() -> None:
    provider_a = _CapturingProvider(
        FakeLocalProvider(
            "provider-a",
            capability_profiles=(_local_profile(profile_id="a", cost_class=CostClass.FREE),),
        )
    )
    provider_b = _CapturingProvider(
        FakeLocalProvider(
            "provider-b",
            capability_profiles=(_local_profile(profile_id="b", cost_class=CostClass.FREE),),
        )
    )

    request = _request()
    payload = _verified_json(request.request_id)
    provider_a._inner.output_text = payload  # type: ignore[attr-defined]
    provider_b._inner.output_text = payload  # type: ignore[attr-defined]

    router_ab, _ = _router_with(provider_a, provider_b)
    asyncio.run(_verifier(router_ab).verify(request))
    selected_first_order = provider_a.provider_id if provider_a.requests else provider_b.provider_id

    provider_a2 = _CapturingProvider(
        FakeLocalProvider(
            "provider-a",
            capability_profiles=(_local_profile(profile_id="a", cost_class=CostClass.FREE),),
        )
    )
    provider_b2 = _CapturingProvider(
        FakeLocalProvider(
            "provider-b",
            capability_profiles=(_local_profile(profile_id="b", cost_class=CostClass.FREE),),
        )
    )
    provider_a2._inner.output_text = payload  # type: ignore[attr-defined]
    provider_b2._inner.output_text = payload  # type: ignore[attr-defined]
    router_ba, _ = _router_with(provider_b2, provider_a2)
    asyncio.run(_verifier(router_ba).verify(request))
    selected_second_order = (
        provider_a2.provider_id if provider_a2.requests else provider_b2.provider_id
    )

    assert selected_first_order == selected_second_order


def test_equivalent_inputs_and_provider_output_produce_equivalent_results() -> None:
    request = _request()
    payload = _verified_json(request.request_id, semantic_confidence=0.5)

    provider_1 = _CapturingProvider(
        FakeLocalProvider(output_text=payload, capability_profiles=(_local_profile(),))
    )
    router_1, _ = _router_with(provider_1)
    first = asyncio.run(_verifier(router_1).verify(request))

    provider_2 = _CapturingProvider(
        FakeLocalProvider(output_text=payload, capability_profiles=(_local_profile(),))
    )
    router_2, _ = _router_with(provider_2)
    second = asyncio.run(_verifier(router_2).verify(request))

    assert first == second
