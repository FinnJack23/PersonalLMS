"""Domain-neutral citation-checked Tutor v1: ``EvidenceCheckedTutorService``.

Combines a ``LibrarianContentGroundingService`` retrieval with a routed
``ModelProvider`` generation and a structural post-generation citation
check into one bounded service. It performs no source ingestion, no
OCR/PDF handling, no question-bank/drill generation, no SQLite schema
change, and no direct SQLite/Obsidian/filesystem access — evidence only
ever arrives as an already-assembled ``GroundingBundle`` obtained through
the injected grounding service.

Explicitly out of scope: semantic claim verification. Citation
verification here is structural only — every inline ``[E<n>]`` label a
generated answer uses must name evidence that was actually supplied to
the model, but this service never checks whether the generated sentence
is actually *entailed* by that evidence's text. That remains a future
Source Verifier's job.

Call budget per ``teach()`` invocation:

- exactly one ``LibrarianContentGroundingService.retrieve()`` call;
- at most one ``DeterministicRouter.route()`` call (skipped entirely when
  grounding is insufficient);
- at most one ``ModelProvider.generate()`` call (skipped whenever routing
  did not select a provider, so an ineligible/unselected provider is
  never even constructed a prompt to see, let alone invoked).

Trust boundary: only ``RetrievedEvidence`` items with
``trusted_for_rag is True`` are ever rendered into the model prompt.
Approved-but-untrusted and unapproved evidence are excluded from the
prompt entirely, even though they may still appear in
``GroundingBundle.evidence`` — this mirrors
``LibrarianContentGroundingService``'s own approved-vs-trusted
distinction one layer up.

Privacy: ``request.privacy_classification`` (on ``TutorTeachingRequest``
itself — there is no separate privacy parameter, so a caller can never
pass a value that conflicts with the request it is attached to) is used
both as the retrieval ceiling (via ``LibrarianRetrievalRequest``, so no
chunk more restrictive than it is ever retrieved at all) and as the
``ModelRequest.privacy_classification`` passed to
``DeterministicRouter.route()``. Because retrieval never returns evidence
more restrictive than that ceiling, and the router already refuses to
route a ``restricted_local_only`` request to any hosted provider (see
``personal_lms.policies.router.DeterministicRouter``), restricted-local
evidence can never reach a hosted provider without this module
duplicating or weakening that router policy itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.librarian import (
    GroundingBundle,
    LibrarianRetrievalRequest,
    RetrievedEvidence,
)
from personal_lms.domain.models import ModelRequest
from personal_lms.domain.tutor import (
    CitationIntegrityStatus,
    TeachingResponse,
    TutorTeachingRequest,
)
from personal_lms.librarian.content_grounding import LibrarianContentGroundingService
from personal_lms.policies.errors import RoutingError
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers.errors import ProviderError

_CAPABILITY_PROFILE = "tutor_evidence_checked"

_REFUSAL_EXPLANATION = "This question cannot be answered from approved, trusted sources."
_INSUFFICIENT_GROUNDING_REASON = (
    "insufficient approved, trusted evidence was retrieved to answer this question"
)
_NO_ELIGIBLE_PROVIDER_REASON = "no eligible model provider was available to answer this question"
_PROVIDER_FAILURE_REASON = "the model provider failed while answering this question"
_CITATION_INTEGRITY_FAILURE_REASON = (
    "the generated answer failed structural citation-integrity verification"
)

# confidence is a required TeachingResponse field, but this service never
# performs semantic/factual verification (see the module docstring's
# "explicitly out of scope" note) — passing structural citation-integrity
# checks is not semantic confidence. Every response, verified or refused,
# therefore reports the most conservative value the schema permits (its
# lower bound, 0.0) rather than asserting any earned confidence: semantic
# confidence is simply not assessed here. citation_integrity_status (not
# this field) is the authoritative structural-verification signal.
_CONFIDENCE_NOT_ASSESSED = 0.0

_CITATION_LABEL_PATTERN = re.compile(r"\[(E\d+)\]")


@dataclass(frozen=True, slots=True)
class _EvidenceBlock:
    label: str
    evidence: RetrievedEvidence


def _trusted_blocks(bundle: GroundingBundle) -> tuple[_EvidenceBlock, ...]:
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
        _EvidenceBlock(label=f"E{index}", evidence=item) for index, item in enumerate(trusted, 1)
    )


def _render_evidence_block(block: _EvidenceBlock) -> str:
    """Render one evidence block using only fields actually present.

    ``document_id``, ``chunk_id``, and ``citation.location`` are omitted
    entirely when unset — never rendered as a literal "None".
    """
    evidence = block.evidence
    text = evidence.text
    assert text is not None  # guaranteed by _trusted_blocks' filter

    header_parts = [f"[{block.label}]", f"source_id={evidence.citation.source_id}"]
    if evidence.document_id:
        header_parts.append(f"document_id={evidence.document_id}")
    if evidence.chunk_id:
        header_parts.append(f"chunk_id={evidence.chunk_id}")
    header_parts.append(f'title="{evidence.citation.title}"')
    if evidence.citation.location:
        header_parts.append(f'location="{evidence.citation.location}"')
    return " ".join(header_parts) + "\n" + text


def _build_prompt(learning_objective: str, blocks: tuple[_EvidenceBlock, ...]) -> str:
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


def _citation_labels_in_first_use_order(output_text: str) -> list[str]:
    """Every ``[E<n>]`` label used, deduplicated, in first-appearance order."""
    seen: dict[str, None] = {}
    for match in _CITATION_LABEL_PATTERN.finditer(output_text):
        seen.setdefault(match.group(1), None)
    return list(seen)


def _verify_citations(output_text: str, trusted_labels: frozenset[str]) -> tuple[bool, list[str]]:
    """Structural citation-integrity check only — no semantic entailment claim.

    Valid iff at least one citation label is used (a substantive generated
    answer must cite something) and every used label names evidence that
    was actually supplied to the model (an unknown label such as ``[E99]``
    invalidates the whole draft).
    """
    used_labels = _citation_labels_in_first_use_order(output_text)
    if not used_labels:
        return False, used_labels
    if any(label not in trusted_labels for label in used_labels):
        return False, used_labels
    return True, used_labels


def _citations_for_labels(
    blocks: tuple[_EvidenceBlock, ...], used_labels: list[str]
) -> list[SourceCitation]:
    by_label = {block.label: block.evidence.citation for block in blocks}
    return [by_label[label] for label in used_labels]


def _refusal_response(
    request: TutorTeachingRequest,
    *,
    bundle: GroundingBundle,
    reason: str,
    citation_integrity_status: CitationIntegrityStatus = CitationIntegrityStatus.NOT_APPLICABLE,
) -> TeachingResponse:
    """A deterministic cannot-answer result. Never fabricates an explanation or citation."""
    return TeachingResponse(
        request_id=request.request_id,
        learning_objective=request.learning_objective,
        explanation=_REFUSAL_EXPLANATION,
        citations=[],
        grounded_in_general_knowledge=True,
        confidence=_CONFIDENCE_NOT_ASSESSED,
        grounding_is_sufficient=bundle.is_sufficient,
        citation_integrity_status=citation_integrity_status,
        retrieval_gaps=list(bundle.gaps),
        refusal_reason=reason,
    )


class EvidenceCheckedTutorService:
    """Retrieval, generation, and structural citation verification in one bounded flow.

    Dependencies (``grounding_service``, ``router``) are injected and
    provider-neutral — this class never imports a concrete provider class
    or vendor name (see ADR-0002). ``budget_policy`` is a per-call
    parameter, mirroring ``PersonalAssistantFlow.run()``'s existing
    precedent for routing-policy inputs.
    """

    def __init__(
        self, grounding_service: LibrarianContentGroundingService, router: DeterministicRouter
    ) -> None:
        self._grounding_service = grounding_service
        self._router = router

    async def teach(
        self,
        request: TutorTeachingRequest,
        *,
        budget_policy: BudgetPolicy,
        raw_query: str | None = None,
        max_results: int | None = None,
    ) -> TeachingResponse:
        """Retrieve, ground, generate, and structurally verify one teaching response.

        Requires ``request.retrieve_grounding is True`` — the explicit
        request mode reserved for services that perform their own fresh
        retrieval (see ``TutorTeachingRequest``'s three mutually exclusive
        grounding modes). A request built for one of the other two modes
        (an already-attached ``grounding_bundle``, or a genuine
        general-knowledge acknowledgement) is rejected outright rather
        than silently reinterpreted or forced through: this service never
        requires or ignores a false ``general_knowledge_acknowledged``
        merely to satisfy validation.

        ``request.learning_objective`` doubles as the retrieval query,
        ``request.knowledge_scope`` as the retrieval scope filter, and
        ``request.privacy_classification`` as both the retrieval ceiling
        and the ``ModelRequest`` privacy classification passed to routing
        — there is no separate privacy parameter here, so nothing can
        conflict with the classification already recorded on the request.
        """
        if not request.retrieve_grounding:
            raise ValueError(
                "EvidenceCheckedTutorService requires request.retrieve_grounding=True; "
                "a request carrying a pre-attached grounding_bundle or a "
                "general_knowledge_acknowledged=True request is not accepted here"
            )

        retrieval_request = LibrarianRetrievalRequest(
            interpreted_query=request.learning_objective,
            raw_query=raw_query,
            knowledge_scope=request.knowledge_scope,
            privacy_classification=request.privacy_classification,
            max_results=max_results,
        )
        bundle = self._grounding_service.retrieve(retrieval_request)

        if not bundle.is_sufficient:
            return _refusal_response(request, bundle=bundle, reason=_INSUFFICIENT_GROUNDING_REASON)

        blocks = _trusted_blocks(bundle)
        if not blocks:
            # Defensive: bundle.is_sufficient=True already implies at least
            # one trusted, text-bearing hit per LibrarianContentGroundingService's
            # own contract, but this never assumes that instead of checking.
            return _refusal_response(request, bundle=bundle, reason=_INSUFFICIENT_GROUNDING_REASON)

        prompt = _build_prompt(request.learning_objective, blocks)
        model_request = ModelRequest(
            capability_profile=_CAPABILITY_PROFILE,
            prompt=prompt,
            privacy_classification=request.privacy_classification,
        )

        try:
            routing_result = self._router.route(model_request, budget_policy=budget_policy)
        except RoutingError:
            return _refusal_response(request, bundle=bundle, reason=_NO_ELIGIBLE_PROVIDER_REASON)

        if routing_result.provider is None:
            # tier_0_deterministic (never triggered here) or
            # approval_required: no provider is eligible to call right now.
            return _refusal_response(request, bundle=bundle, reason=_NO_ELIGIBLE_PROVIDER_REASON)

        try:
            result = await routing_result.provider.generate(model_request)
        except ProviderError:
            return _refusal_response(request, bundle=bundle, reason=_PROVIDER_FAILURE_REASON)

        trusted_labels = frozenset(block.label for block in blocks)
        is_valid, used_labels = _verify_citations(result.output_text, trusted_labels)

        if not is_valid:
            return _refusal_response(
                request,
                bundle=bundle,
                reason=_CITATION_INTEGRITY_FAILURE_REASON,
                citation_integrity_status=CitationIntegrityStatus.FAILED,
            )

        return TeachingResponse(
            request_id=request.request_id,
            learning_objective=request.learning_objective,
            explanation=result.output_text,
            citations=_citations_for_labels(blocks, used_labels),
            grounded_in_general_knowledge=False,
            confidence=_CONFIDENCE_NOT_ASSESSED,
            grounding_is_sufficient=True,
            citation_integrity_status=CitationIntegrityStatus.VERIFIED,
            retrieval_gaps=list(bundle.gaps),
            refusal_reason=None,
        )
