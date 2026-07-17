"""``ModelBackedSourceVerifier``: a concrete, provider-neutral ``SourceVerifier``.

Converts one ``SourceVerificationRequest`` into one deterministic
``ModelRequest``, routes it exactly once through the existing
``DeterministicRouter``, calls the selected ``ModelProvider`` exactly once,
strictly parses the JSON it returns, validates it against the approved
``SourceVerificationResult`` schema, and cross-checks it against the
original request. No retry, no fallback, no automatic repair, no second
provider, no regeneration.

No Ollama HTTP access, hosted model API, vendor SDK, embeddings, vector
retrieval, or new dependency is introduced here — the existing
``FakeLocalProvider``/``FakeHostedProvider`` test doubles are sufficient
to exercise this entire execution path. A real local-model provider
adapter is a separate, later milestone.

Request/result correlation is preserved end to end and stays fully
deterministic: ``SourceVerificationRequest.request_id`` (a string)
becomes ``ModelRequest.request_id`` directly whenever it parses as a
``UUID`` (``ModelRequest.request_id`` is UUID-typed) — falling back to a
``uuid5`` derivation over a single fixed namespace only when it does not
(see ``_to_model_request_id``), never a random ``uuid4()``, so repeated
equivalent verification requests always correlate to the identical
``ModelRequest.request_id``. The original string identifier itself is
never rewritten — it is separately embedded in the prompt text and must
be echoed back inside the model's own JSON response body. Both
correlations are validated, never silently repaired:
``ModelResult.request_id`` must equal the ``ModelRequest.request_id`` we
sent, and the parsed ``SourceVerificationResult.request_id`` must equal
``request.request_id`` (checked by
``personal_lms.source_verification.protocol.validate_result_matches_request``).
A mismatch at either layer fails with a safe contract error.

Evidence minimization: only evidence associated with
``SourceVerificationRequest.used_citation_labels`` is ever rendered into
the prompt — never the full grounding bundle, never unused or untrusted
evidence, and no additional evidence is ever retrieved. This is both a
privacy control and a context-size control.

This module never imports from ``personal_lms.tutor`` — the dependency
direction is the other way (the Tutor package depends on this contract),
so the small E<n>-label-to-evidence derivation below is independently
re-implemented rather than imported from
``personal_lms.tutor._generation.trusted_blocks`` (see
``_trusted_evidence_by_label``'s docstring).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID, uuid5

from pydantic import ValidationError

from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.enums import CostClass, LatencyClass, RoutingOutcome
from personal_lms.domain.librarian import GroundingBundle, RetrievedEvidence
from personal_lms.domain.models import ModelRequest
from personal_lms.domain.source_verification import (
    SourceVerificationRequest,
    SourceVerificationResult,
)
from personal_lms.policies.errors import PrivacyPolicyDeniedError, RoutingError
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers.errors import (
    ProviderContractError,
    ProviderError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from personal_lms.source_verification.errors import (
    SourceVerificationContractError,
    SourceVerificationExecutionError,
    SourceVerificationPrivacyError,
    SourceVerificationUnavailableError,
)
from personal_lms.source_verification.protocol import validate_result_matches_request

_CAPABILITY_PROFILE = "source_verification"

# Fixed, module-level namespace for deterministically deriving a
# ModelRequest.request_id (UUID-typed) from a non-UUID-shaped
# SourceVerificationRequest.request_id (str-typed) via uuid5 — never
# uuid4(), never random, never clock- or environment-derived. Generated
# once (uuid4()) and hardcoded here; this literal value must never change,
# or previously-derived correlation IDs would silently shift.
_SOURCE_VERIFICATION_NAMESPACE = UUID("6f6b2f1e-3f0a-4a9e-9d6a-8a6f9f6c9b3a")

_RESULT_SHAPE_EXAMPLE = (
    '{"request_id": "<echo the request_id given below>", '
    '"status": "verified|partially_verified|rejected", '
    '"claims": [{"claim_id": "C1", '
    '"status": "supported|partially_supported|unsupported|conflicting|not_verifiable", '
    '"evidence_labels": ["E1"], "reason_codes": []}], '
    '"verified_citation_labels": ["E1"], "unsupported_claim_count": 0, '
    '"conflict_count": 0, "semantic_confidence": null, "reason_codes": []}'
)


@dataclass(frozen=True, slots=True)
class SourceVerificationRoutingPolicy:
    """Stable, provider-neutral routing configuration for semantic verification.

    Never derived from generated prose or evidence text — a fixed,
    constructor-injected policy only. Fields deliberately do not duplicate
    ``BudgetPolicy`` (cost limits stay there); this only carries capability
    filter preferences that ``DeterministicRouter.route()`` already
    accepts as plain parameters or ``ModelRequest`` fields (see
    ``ModelBackedSourceVerifier.verify`` for exactly where each field is
    used).
    """

    requires_reasoning: bool = True
    requires_vision: bool = False
    minimum_context_tokens: int = 0
    local_only: bool = False
    maximum_cost_class: CostClass | None = None
    maximum_latency_class: LatencyClass | None = None

    def __post_init__(self) -> None:
        if self.minimum_context_tokens < 0:
            raise ValueError("minimum_context_tokens must be >= 0")


_DEFAULT_ROUTING_POLICY = SourceVerificationRoutingPolicy()


@dataclass(frozen=True, slots=True)
class _EvidenceItem:
    label: str
    evidence: RetrievedEvidence


def _trusted_evidence_by_label(bundle: GroundingBundle) -> dict[str, RetrievedEvidence]:
    """Independently re-derive the E<n> label -> evidence mapping.

    Duplicated, in miniature, from
    ``personal_lms.tutor._generation.trusted_blocks`` rather than imported
    — this package must never depend on ``personal_lms.tutor`` internals
    (see the module docstring). Both implementations apply the identical,
    simple rule: E<n> is assigned, 1-indexed, to trusted
    (``trusted_for_rag is True``), text-bearing evidence, in
    ``bundle.evidence`` order — the same scheme originally used when the
    generated answer's citations were labeled, so labels resolve
    identically here.
    """
    trusted = [
        item for item in bundle.evidence if item.trusted_for_rag is True and item.text is not None
    ]
    return {f"E{index}": item for index, item in enumerate(trusted, 1)}


def _resolve_used_evidence(
    request: SourceVerificationRequest, *, verifier_id: str
) -> list[_EvidenceItem]:
    """Only used, trusted, text-bearing evidence — first-use order, deduplicated.

    Never broadens the grounding bundle and never retrieves additional
    evidence: every item returned here is already present in
    ``request.grounding_bundle.evidence``. A used label that cannot be
    resolved to trusted evidence in that bundle fails closed with a
    ``SourceVerificationContractError`` *before* any routing or provider
    call — never silently skipped.
    """
    by_label = _trusted_evidence_by_label(request.grounding_bundle)

    seen: dict[str, None] = {}
    for label in request.used_citation_labels:
        seen.setdefault(label, None)

    resolved: list[_EvidenceItem] = []
    for label in seen:
        evidence = by_label.get(label)
        if evidence is None:
            raise SourceVerificationContractError(
                verifier_id,
                f"used citation label {label!r} does not resolve to trusted, "
                "text-bearing evidence in the supplied grounding bundle",
            )
        resolved.append(_EvidenceItem(label=label, evidence=evidence))
    return resolved


def _render_evidence_item(item: _EvidenceItem) -> str:
    text = item.evidence.text
    assert text is not None  # guaranteed by _resolve_used_evidence's source mapping
    return f'[{item.label}] title="{item.evidence.citation.title}"\n{text}'


def _build_prompt(request: SourceVerificationRequest, evidence_items: list[_EvidenceItem]) -> str:
    """Deterministic prompt construction: no randomness, clock, or environment.

    Fixed section order, stable evidence order (first-use), stable
    labels. Never includes the original TutorTeachingRequest prompt (this
    schema does not even carry it), unused or untrusted evidence,
    unrelated grounding-bundle metadata, credentials, provider
    identifiers, routing details, filesystem paths, or environment
    variables.
    """
    evidence_text = "\n\n".join(_render_evidence_item(item) for item in evidence_items)
    used_labels_text = ", ".join(item.label for item in evidence_items)
    return (
        "You are a source verifier. Determine whether each factual claim in "
        "the generated answer below is supported by the evidence supplied "
        "beneath it. Identify claims yourself and assign them deterministic "
        "claim IDs C1, C2, C3, ... in the order they appear in the generated "
        "answer. For each claim, assign exactly one status: supported, "
        "partially_supported, unsupported, conflicting, or not_verifiable. A "
        "claim with no supporting evidence is unsupported (or "
        "not_verifiable if it is not a factual claim at all — an opinion, "
        "instruction, or rhetorical statement). A claim a supplied evidence "
        "passage directly contradicts is conflicting. Associate each claim "
        "only with evidence labels actually supplied below — never an "
        "evidence label that is not listed here, and return no unknown "
        "evidence labels anywhere in your response. Never use your own "
        "pretrained knowledge as evidence; judge support using only the "
        "evidence supplied here.\n\n"
        f"Generated answer:\n{request.generated_text}\n\n"
        f"Used evidence labels: {used_labels_text}\n\n"
        "Evidence:\n"
        f"{evidence_text}\n\n"
        "Respond with strict JSON only — no Markdown code fences, no "
        "explanatory text before or after the JSON object. The JSON object "
        "must have exactly this shape:\n"
        f"{_RESULT_SHAPE_EXAMPLE}\n\n"
        f"request_id: {request.request_id}\n\n"
        "Fail closed: if uncertain, prefer a lower status (unsupported or "
        'not_verifiable) rather than supported. Set "status" to "verified" '
        'only if every claim is fully supported; use "partially_verified" '
        'if some claims are only partially supported; use "rejected" if any '
        "claim is unsupported or conflicting."
    )


def _to_model_request_id(request_id: str) -> UUID:
    """Deterministically derive the ``ModelRequest.request_id`` used for
    execution correlation from ``request.request_id``.

    Preserves ``request.request_id`` as-is whenever it already parses as a
    ``UUID`` (``ModelRequest.request_id`` is UUID-typed). When it does
    not, deterministically derives a ``UUID`` via ``uuid5`` over a single
    fixed, module-level namespace — never a random ``uuid4()`` — so
    repeated equivalent verification requests always correlate to the
    identical ``ModelRequest.request_id``. This is purely an execution
    correlation identifier: it is never written back into
    ``SourceVerificationRequest.request_id`` or the parsed
    ``SourceVerificationResult.request_id``, both of which keep the
    original string exactly as given — the string itself is what is
    separately embedded in the prompt text and cross-checked by
    ``personal_lms.source_verification.protocol.validate_result_matches_request``.
    """
    try:
        return UUID(request_id)
    except ValueError:
        return uuid5(_SOURCE_VERIFICATION_NAMESPACE, request_id)


def _parse_json_object(output_text: str, *, verifier_id: str) -> dict[str, object]:
    """Strict JSON parsing only — no heuristic extraction, no fence stripping.

    ``json.loads`` requires the *entire* (whitespace-trimmed) string to be
    valid JSON, so Markdown code fences and any leading/trailing
    explanatory prose already fail to parse — no special-casing needed to
    reject them, and none is added.
    """
    try:
        parsed = json.loads(output_text.strip())
    except (ValueError, TypeError) as exc:
        raise SourceVerificationContractError(
            verifier_id, f"provider output was not valid JSON ({type(exc).__name__})"
        ) from None

    if not isinstance(parsed, dict):
        raise SourceVerificationContractError(
            verifier_id, "provider output JSON was not a JSON object"
        )
    return parsed


def _validate_result_schema(
    parsed: dict[str, object], *, verifier_id: str
) -> SourceVerificationResult:
    """Validate against the approved schema — never repaired or substituted.

    The safe error message deliberately never includes pydantic's own
    ``str(exc)``, which can embed the offending raw value(s) it rejected.
    """
    try:
        return SourceVerificationResult.model_validate(parsed)
    except ValidationError:
        raise SourceVerificationContractError(
            verifier_id, "provider output failed SourceVerificationResult schema validation"
        ) from None


class ModelBackedSourceVerifier:
    """Routed, provider-neutral ``SourceVerifier`` implementation.

    ``verify()`` continues to accept only ``SourceVerificationRequest`` —
    no separate per-call budget or privacy keyword argument exists.
    Routing/budget policy is fixed at construction time via constructor
    injection (``router``, ``budget_policy``, ``routing_policy``).
    """

    def __init__(
        self,
        *,
        verifier_id: str,
        router: DeterministicRouter,
        budget_policy: BudgetPolicy,
        routing_policy: SourceVerificationRoutingPolicy = _DEFAULT_ROUTING_POLICY,
    ) -> None:
        if not verifier_id:
            raise ValueError("verifier_id must not be empty")
        self._verifier_id = verifier_id
        self._router = router
        self._budget_policy = budget_policy
        self._routing_policy = routing_policy

    @property
    def verifier_id(self) -> str:
        return self._verifier_id

    async def verify(self, request: SourceVerificationRequest) -> SourceVerificationResult:
        """Execute the full routed verification path exactly once.

        Order: resolve used evidence, build the deterministic prompt,
        route exactly once, generate zero or one time depending on the
        routing outcome, strictly parse and validate the JSON result,
        then cross-check it against ``request``. No stage runs twice.
        """
        evidence_items = _resolve_used_evidence(request, verifier_id=self._verifier_id)
        prompt = _build_prompt(request, evidence_items)

        policy = self._routing_policy
        model_request = ModelRequest(
            request_id=_to_model_request_id(request.request_id),
            capability_profile=_CAPABILITY_PROFILE,
            prompt=prompt,
            requires_vision=policy.requires_vision,
            privacy_classification=request.privacy_classification,
            context_token_estimate=policy.minimum_context_tokens,
        )

        try:
            routing_result = self._router.route(
                model_request,
                budget_policy=self._budget_policy,
                deterministic_capable=False,
                requires_reasoning=policy.requires_reasoning,
                local_only=policy.local_only,
                max_cost_class=policy.maximum_cost_class or CostClass.HIGH,
                max_latency_class=policy.maximum_latency_class or LatencyClass.BATCH,
            )
        except PrivacyPolicyDeniedError as exc:
            raise SourceVerificationPrivacyError(
                self._verifier_id, "privacy_policy_denied_hosted_routing"
            ) from exc
        except RoutingError as exc:
            raise SourceVerificationUnavailableError(self._verifier_id, type(exc).__name__) from exc

        if routing_result.decision.outcome is RoutingOutcome.APPROVAL_REQUIRED:
            raise SourceVerificationUnavailableError(self._verifier_id, "approval_required")

        if routing_result.provider is None:
            # tier_0_deterministic is structurally unreachable — this call
            # always passes deterministic_capable=False — but a contract
            # violation (rather than a silent no-op) is the safe response
            # if it is ever somehow encountered.
            raise SourceVerificationContractError(
                self._verifier_id, "router returned no provider for a non-approval outcome"
            )

        try:
            model_result = await routing_result.provider.generate(model_request)
        except ProviderTimeoutError as exc:
            raise SourceVerificationUnavailableError(self._verifier_id, "provider_timeout") from exc
        except ProviderUnavailableError as exc:
            raise SourceVerificationUnavailableError(
                self._verifier_id, "provider_unavailable"
            ) from exc
        except ProviderExecutionError as exc:
            raise SourceVerificationExecutionError(
                self._verifier_id, "provider_execution_error"
            ) from exc
        except ProviderContractError as exc:
            raise SourceVerificationContractError(
                self._verifier_id, "provider_contract_error"
            ) from exc
        except ProviderError as exc:
            raise SourceVerificationExecutionError(self._verifier_id, type(exc).__name__) from exc

        if model_result.request_id != model_request.request_id:
            raise SourceVerificationContractError(
                self._verifier_id, "provider result request_id did not match the model request"
            )

        parsed = _parse_json_object(model_result.output_text, verifier_id=self._verifier_id)
        result = _validate_result_schema(parsed, verifier_id=self._verifier_id)
        validate_result_matches_request(request, result, verifier_id=self._verifier_id)
        return result
