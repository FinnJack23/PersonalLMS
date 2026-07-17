"""Shared, package-private generation and structural citation-verification helpers.

Used by both ``EvidenceCheckedTutorService`` (retrieval-driven mode) and
``TutorTeachingCoordinator`` (supplied-bundle and general-knowledge modes)
so that evidence-block construction, prompt formatting, routing/generation
error handling, and citation parsing/mapping are each implemented exactly
once — never duplicated across the two public services. Nothing here is
part of this package's public API; only ``EvidenceCheckedTutorService`` and
``TutorTeachingCoordinator`` (see ``evidence_checked.py``/``coordinator.py``)
are.

Explicitly out of scope, same as both public services: semantic claim
verification. Citation verification here is structural only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.librarian import GroundingBundle, RetrievedEvidence
from personal_lms.domain.models import ModelRequest, ModelResult
from personal_lms.domain.tutor import (
    CitationIntegrityStatus,
    TeachingResponse,
    TutorTeachingRequest,
)
from personal_lms.policies.errors import RoutingError
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers.errors import ProviderError

CAPABILITY_PROFILE_EVIDENCE_CHECKED = "tutor_evidence_checked"
CAPABILITY_PROFILE_GENERAL_KNOWLEDGE = "tutor_general_knowledge"

REFUSAL_EXPLANATION = "This question cannot be answered from approved, trusted sources."
INSUFFICIENT_GROUNDING_REASON = (
    "insufficient approved, trusted evidence was retrieved to answer this question"
)
NO_ELIGIBLE_PROVIDER_REASON = "no eligible model provider was available to answer this question"
PROVIDER_FAILURE_REASON = "the model provider failed while answering this question"
CITATION_INTEGRITY_FAILURE_REASON = (
    "the generated answer failed structural citation-integrity verification"
)

# confidence is a required TeachingResponse field, but no Tutor service in
# this package performs semantic/factual verification — passing structural
# citation-integrity checks is not semantic confidence. Every response,
# verified or refused, therefore reports the most conservative value the
# schema permits (its lower bound, 0.0) rather than asserting any earned
# confidence: semantic confidence is simply not assessed here.
# citation_integrity_status (not this field) is the authoritative
# structural-verification signal.
CONFIDENCE_NOT_ASSESSED = 0.0

_CITATION_LABEL_PATTERN = re.compile(r"\[(E\d+)\]")


class NoEligibleProviderError(Exception):
    """Routing found (or was allowed) no eligible provider for this request.

    Deliberately distinct from ``ProviderFailedError`` — a routing-policy
    outcome (no compatible/eligible provider, or approval required) must
    never be reported or handled as a provider *runtime* failure. Carries
    no message, prompt text, or other request content.
    """


class ProviderFailedError(Exception):
    """The selected provider's ``generate()`` call raised a ``ProviderError``.

    Carries no message, prompt text, or other request content — the
    original ``ProviderError`` is intentionally not chained into a
    user-facing message.
    """


@dataclass(frozen=True, slots=True)
class EvidenceBlock:
    label: str
    evidence: RetrievedEvidence


def trusted_blocks(bundle: GroundingBundle) -> tuple[EvidenceBlock, ...]:
    """Trusted, text-bearing evidence in retrieval order, labeled E1, E2, ...

    Approved-but-untrusted and unapproved evidence (``trusted_for_rag`` is
    ``False`` or ``None``) is excluded entirely — it may still be present
    in ``bundle.evidence`` for transparency, but it must never reach the
    model prompt. Evidence with no ``text`` (source-metadata-only retrieval
    paths never set it) is likewise excluded rather than rendered with a
    fabricated or blank body.
    """
    trusted = [
        item for item in bundle.evidence if item.trusted_for_rag is True and item.text is not None
    ]
    return tuple(
        EvidenceBlock(label=f"E{index}", evidence=item) for index, item in enumerate(trusted, 1)
    )


def _render_evidence_block(block: EvidenceBlock) -> str:
    """Render one evidence block using only fields actually present.

    ``document_id``, ``chunk_id``, and ``citation.location`` are omitted
    entirely when unset — never rendered as a literal "None".
    """
    evidence = block.evidence
    text = evidence.text
    assert text is not None  # guaranteed by trusted_blocks' filter

    header_parts = [f"[{block.label}]", f"source_id={evidence.citation.source_id}"]
    if evidence.document_id:
        header_parts.append(f"document_id={evidence.document_id}")
    if evidence.chunk_id:
        header_parts.append(f"chunk_id={evidence.chunk_id}")
    header_parts.append(f'title="{evidence.citation.title}"')
    if evidence.citation.location:
        header_parts.append(f'location="{evidence.citation.location}"')
    return " ".join(header_parts) + "\n" + text


def build_evidence_prompt(learning_objective: str, blocks: tuple[EvidenceBlock, ...]) -> str:
    """Deterministic prompt construction: no randomness, clock, or history.

    Instructs the model to answer only from the supplied evidence, to cite
    inline using ``[E1]``/``[E2]``/etc., and to state plainly when the
    evidence does not answer the objective — never to guess. No untrusted
    evidence, hidden metadata, or unrelated conversation history is ever
    included, since ``blocks`` already excludes everything but trusted,
    text-bearing evidence for this one request.
    """
    evidence_text = "\n\n".join(_render_evidence_block(block) for block in blocks)
    return (
        "You are a tutor. Answer the learning objective below using only the "
        "evidence supplied beneath it. Cite every claim inline using the "
        "matching evidence label in square brackets, e.g. [E1] or [E2]. Do "
        "not use any information that is not present in the supplied "
        "evidence, and do not reference any hidden metadata or prior "
        "conversation history. If the supplied evidence does not fully "
        "answer the learning objective, say so explicitly rather than "
        "guessing.\n\n"
        f"Learning objective: {learning_objective}\n\n"
        "Evidence:\n"
        f"{evidence_text}\n\n"
        "Answer the learning objective above using only the evidence, with "
        "inline citations."
    )


def build_general_knowledge_prompt(learning_objective: str) -> str:
    """Deterministic prompt construction for the no-evidence general-knowledge mode.

    No evidence is supplied at all, so the model is explicitly told not to
    fabricate source labels — any ``[E<n>]``-shaped text it produces anyway
    is never trusted or surfaced (see ``TutorTeachingCoordinator``, which
    never runs citation verification against an empty trusted set for this
    mode and always reports ``citations=[]``).
    """
    return (
        "You are a tutor answering from general knowledge only — no "
        "retrieved source evidence is supplied for this request. Answer "
        "the learning objective below directly and clearly. Do not "
        "fabricate citations or reference source labels such as [E1]; "
        "none are provided.\n\n"
        f"Learning objective: {learning_objective}\n\n"
        "Answer the learning objective above."
    )


def citation_labels_in_first_use_order(output_text: str) -> list[str]:
    """Every ``[E<n>]`` label used, deduplicated, in first-appearance order."""
    seen: dict[str, None] = {}
    for match in _CITATION_LABEL_PATTERN.finditer(output_text):
        seen.setdefault(match.group(1), None)
    return list(seen)


def verify_citations(output_text: str, trusted_labels: frozenset[str]) -> tuple[bool, list[str]]:
    """Structural citation-integrity check only — no semantic entailment claim.

    Valid iff at least one citation label is used (a substantive generated
    answer must cite something) and every used label names evidence that
    was actually supplied to the model (an unknown label such as ``[E99]``
    invalidates the whole draft).
    """
    used_labels = citation_labels_in_first_use_order(output_text)
    if not used_labels:
        return False, used_labels
    if any(label not in trusted_labels for label in used_labels):
        return False, used_labels
    return True, used_labels


def citations_for_labels(
    blocks: tuple[EvidenceBlock, ...], used_labels: list[str]
) -> list[SourceCitation]:
    by_label = {block.label: block.evidence.citation for block in blocks}
    return [by_label[label] for label in used_labels]


def refusal_response(
    request: TutorTeachingRequest,
    *,
    grounding_is_sufficient: bool | None,
    retrieval_gaps: list[str],
    reason: str,
    citation_integrity_status: CitationIntegrityStatus = CitationIntegrityStatus.NOT_APPLICABLE,
) -> TeachingResponse:
    """A deterministic cannot-answer result. Never fabricates an explanation or citation.

    ``grounding_is_sufficient``/``retrieval_gaps`` are passed explicitly
    (rather than a ``GroundingBundle``) so this same helper covers the
    general-knowledge mode too, which has no bundle at all —
    ``grounding_is_sufficient=None`` there, never a fabricated ``False``.
    """
    return TeachingResponse(
        request_id=request.request_id,
        learning_objective=request.learning_objective,
        explanation=REFUSAL_EXPLANATION,
        citations=[],
        grounded_in_general_knowledge=True,
        confidence=CONFIDENCE_NOT_ASSESSED,
        grounding_is_sufficient=grounding_is_sufficient,
        citation_integrity_status=citation_integrity_status,
        retrieval_gaps=list(retrieval_gaps),
        refusal_reason=reason,
    )


async def route_and_generate(
    router: DeterministicRouter, budget_policy: BudgetPolicy, model_request: ModelRequest
) -> ModelResult:
    """Route and generate exactly once, distinguishing routing-policy
    outcomes from provider runtime failures.

    Raises ``NoEligibleProviderError`` for a ``RoutingError`` or a
    ``RoutingResult`` with no selected provider (``tier_0``/
    ``approval_required``) — a routing-policy outcome, never conflated
    with ``ProviderFailedError``, which is raised only when the already-
    selected provider's ``generate()`` call itself raises a
    ``ProviderError``. These are two separate ``try`` blocks specifically
    so a routing-policy exception can never be caught by the
    provider-failure handler even though ``RoutingError`` is itself a
    ``ProviderError`` subclass.
    """
    try:
        routing_result = router.route(model_request, budget_policy=budget_policy)
    except RoutingError:
        raise NoEligibleProviderError from None

    if routing_result.provider is None:
        # tier_0_deterministic (never triggered by Tutor generation
        # requests) or approval_required: no provider is eligible right now.
        raise NoEligibleProviderError

    try:
        return await routing_result.provider.generate(model_request)
    except ProviderError:
        raise ProviderFailedError from None


async def answer_from_bundle(
    *,
    request: TutorTeachingRequest,
    bundle: GroundingBundle,
    router: DeterministicRouter,
    budget_policy: BudgetPolicy,
) -> TeachingResponse:
    """Generate and structurally verify one ``TeachingResponse`` from an
    already-obtained ``GroundingBundle`` — fresh retrieval or caller-supplied.

    Shared by ``EvidenceCheckedTutorService`` (after its own single
    retrieval call) and ``TutorTeachingCoordinator``'s supplied-bundle mode
    (using the caller-supplied bundle directly, performing no retrieval of
    its own) — the only difference between the two modes is where
    ``bundle`` came from; everything downstream (trust filtering, prompt
    construction, routing, generation, citation verification) is identical
    and implemented exactly once here.
    """
    if not bundle.is_sufficient:
        return refusal_response(
            request,
            grounding_is_sufficient=bundle.is_sufficient,
            retrieval_gaps=bundle.gaps,
            reason=INSUFFICIENT_GROUNDING_REASON,
        )

    blocks = trusted_blocks(bundle)
    if not blocks:
        # Defensive: bundle.is_sufficient=True already implies at least one
        # trusted, text-bearing hit for a freshly retrieved bundle, but a
        # caller-supplied bundle carries no such guarantee — this never
        # assumes it instead of checking.
        return refusal_response(
            request,
            grounding_is_sufficient=bundle.is_sufficient,
            retrieval_gaps=bundle.gaps,
            reason=INSUFFICIENT_GROUNDING_REASON,
        )

    prompt = build_evidence_prompt(request.learning_objective, blocks)
    model_request = ModelRequest(
        capability_profile=CAPABILITY_PROFILE_EVIDENCE_CHECKED,
        prompt=prompt,
        privacy_classification=request.privacy_classification,
    )

    try:
        result = await route_and_generate(router, budget_policy, model_request)
    except NoEligibleProviderError:
        return refusal_response(
            request,
            grounding_is_sufficient=bundle.is_sufficient,
            retrieval_gaps=bundle.gaps,
            reason=NO_ELIGIBLE_PROVIDER_REASON,
        )
    except ProviderFailedError:
        return refusal_response(
            request,
            grounding_is_sufficient=bundle.is_sufficient,
            retrieval_gaps=bundle.gaps,
            reason=PROVIDER_FAILURE_REASON,
        )

    trusted_labels = frozenset(block.label for block in blocks)
    is_valid, used_labels = verify_citations(result.output_text, trusted_labels)

    if not is_valid:
        return refusal_response(
            request,
            grounding_is_sufficient=bundle.is_sufficient,
            retrieval_gaps=bundle.gaps,
            reason=CITATION_INTEGRITY_FAILURE_REASON,
            citation_integrity_status=CitationIntegrityStatus.FAILED,
        )

    return TeachingResponse(
        request_id=request.request_id,
        learning_objective=request.learning_objective,
        explanation=result.output_text,
        citations=citations_for_labels(blocks, used_labels),
        grounded_in_general_knowledge=False,
        confidence=CONFIDENCE_NOT_ASSESSED,
        grounding_is_sufficient=True,
        citation_integrity_status=CitationIntegrityStatus.VERIFIED,
        retrieval_gaps=list(bundle.gaps),
        refusal_reason=None,
    )


async def answer_from_general_knowledge(
    request: TutorTeachingRequest,
    *,
    router: DeterministicRouter,
    budget_policy: BudgetPolicy,
) -> TeachingResponse:
    """Route and generate exactly once with no grounding bundle at all.

    No retrieval, no evidence blocks, no citation verification (there is
    no trusted-label set to verify against) — ``citations`` is always
    ``[]`` and ``citation_integrity_status`` is always ``NOT_APPLICABLE``
    (verification was never performed, by design, for this mode).
    ``grounding_is_sufficient`` is ``None`` rather than a fabricated
    ``False``: there is no bundle whose sufficiency could even be judged.
    """
    prompt = build_general_knowledge_prompt(request.learning_objective)
    model_request = ModelRequest(
        capability_profile=CAPABILITY_PROFILE_GENERAL_KNOWLEDGE,
        prompt=prompt,
        privacy_classification=request.privacy_classification,
    )

    try:
        result = await route_and_generate(router, budget_policy, model_request)
    except NoEligibleProviderError:
        return refusal_response(
            request,
            grounding_is_sufficient=None,
            retrieval_gaps=[],
            reason=NO_ELIGIBLE_PROVIDER_REASON,
        )
    except ProviderFailedError:
        return refusal_response(
            request,
            grounding_is_sufficient=None,
            retrieval_gaps=[],
            reason=PROVIDER_FAILURE_REASON,
        )

    return TeachingResponse(
        request_id=request.request_id,
        learning_objective=request.learning_objective,
        explanation=result.output_text,
        citations=[],
        grounded_in_general_knowledge=True,
        confidence=CONFIDENCE_NOT_ASSESSED,
        grounding_is_sufficient=None,
        citation_integrity_status=CitationIntegrityStatus.NOT_APPLICABLE,
        retrieval_gaps=[],
        refusal_reason=None,
    )
