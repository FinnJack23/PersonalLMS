"""Domain-neutral citation-checked Tutor v1: ``EvidenceCheckedTutorService``.

Combines a ``LibrarianContentGroundingService`` retrieval with a routed
``ModelProvider`` generation and a structural post-generation citation
check into one bounded service. It performs no source ingestion, no
OCR/PDF handling, no question-bank/drill generation, no SQLite schema
change, and no direct SQLite/Obsidian/filesystem access — evidence only
ever arrives as an already-assembled ``GroundingBundle`` obtained through
the injected grounding service.

The generation, prompt-construction, and structural citation-verification
logic below the retrieval step lives in ``personal_lms.tutor._generation``
(package-private) and is shared with ``TutorTeachingCoordinator``'s
supplied-grounding-bundle mode — see that module's docstring. This class
owns only the one behavior specific to it: performing the retrieval that
produces the ``GroundingBundle`` in the first place.

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

from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.librarian import LibrarianRetrievalRequest
from personal_lms.domain.tutor import TeachingResponse, TutorTeachingRequest
from personal_lms.librarian.content_grounding import LibrarianContentGroundingService
from personal_lms.policies.router import DeterministicRouter
from personal_lms.source_verification.protocol import SourceVerifier
from personal_lms.tutor._generation import answer_from_bundle


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
        source_verifier: SourceVerifier | None = None,
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
        merely to satisfy validation. ``TutorTeachingCoordinator`` is the
        public entry point that dispatches all three modes, delegating to
        this service unchanged for ``retrieve_grounding`` requests.

        ``request.learning_objective`` doubles as the retrieval query,
        ``request.knowledge_scope`` as the retrieval scope filter, and
        ``request.privacy_classification`` as both the retrieval ceiling
        and the ``ModelRequest`` privacy classification passed to routing
        and to any configured Source Verifier — there is no separate
        privacy parameter here, so nothing can conflict with the
        classification already recorded on the request.

        ``source_verifier`` is optional and, like ``budget_policy``, a
        per-call dependency — omitted (``None``) by default, preserving
        every existing caller unchanged. When supplied, it runs exactly
        once, strictly after structural citation-label validation passes
        (see ``personal_lms.tutor._generation.answer_from_bundle`` for the
        full execution order and gating rules).
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

        return await answer_from_bundle(
            request=request,
            bundle=bundle,
            router=self._router,
            budget_policy=budget_policy,
            source_verifier=source_verifier,
        )
