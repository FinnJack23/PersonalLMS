"""``TutorTeachingCoordinator``: the public entry point for Tutor teaching requests.

Dispatches every valid ``TutorTeachingRequest`` deterministically to
exactly one of its three mutually exclusive grounding modes (see
``personal_lms.domain.tutor.TutorTeachingRequest``):

- ``retrieve_grounding=True`` — delegated unchanged to
  ``EvidenceCheckedTutorService.teach()``. Nothing here re-implements or
  re-triggers retrieval, routing, or generation for this mode; the
  resulting ``TeachingResponse`` is returned exactly as produced.
- ``grounding_bundle`` supplied — answered directly using the supplied
  bundle as the complete evidence input. No retrieval call is ever made;
  the bundle is never supplemented with fresh retrieval. Routes and
  generates exactly once, applying the identical trusted-evidence
  labeling and structural citation verification
  ``EvidenceCheckedTutorService`` uses (shared via
  ``personal_lms.tutor._generation``, not duplicated).
- ``general_knowledge_acknowledged=True`` — answered directly with no
  grounding bundle at all (no retrieval, no evidence blocks, no citation
  verification, and no Source Verifier call). Always reports
  ``citations=[]`` and never claims verified citation or source
  integrity.

``teach()``'s optional ``source_verifier`` parameter (default ``None``,
per-call, mirroring ``budget_policy``) is forwarded identically to both
the ``retrieve_grounding`` delegation and the supplied-bundle mode — both
ultimately call the same shared ``personal_lms.tutor._generation``
verification gate, so a semantic Source Verifier is never integrated
twice. It is never forwarded to the general-knowledge mode, which accepts
no such parameter at all.

Composition only: this class does not inherit from
``EvidenceCheckedTutorService``, and neither service inherits from the
other. Dependencies are injected and provider-neutral. Per call, this
coordinator dispatches exactly one mode, performs retrieval zero or one
time, routes zero or one time, and calls a model provider zero or one
time — never retries, never falls back to another provider, and never
executes more than one request mode.

``TutorTeachingRequest``'s own Pydantic validator already guarantees
exactly one of the three modes is set; this coordinator does not
duplicate that validator. Its only defensive check is a fallback
``ValueError`` for an otherwise-impossible instance (e.g. one constructed
via ``model_construct``, bypassing validation) that selects none of the
three modes — never re-checking mutual exclusivity itself.
"""

from __future__ import annotations

from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.tutor import TeachingResponse, TutorTeachingRequest
from personal_lms.policies.router import DeterministicRouter
from personal_lms.source_verification.protocol import SourceVerifier
from personal_lms.tutor._generation import answer_from_bundle, answer_from_general_knowledge
from personal_lms.tutor.evidence_checked import EvidenceCheckedTutorService


class TutorTeachingCoordinator:
    """Dispatches every valid ``TutorTeachingRequest`` mode deterministically.

    ``evidence_checked_service`` and ``router`` are injected and
    provider-neutral. ``budget_policy`` is a per-call parameter, matching
    ``EvidenceCheckedTutorService.teach()``'s own precedent.
    """

    def __init__(
        self,
        evidence_checked_service: EvidenceCheckedTutorService,
        router: DeterministicRouter,
    ) -> None:
        self._evidence_checked_service = evidence_checked_service
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
        """Dispatch ``request`` to exactly one of its three grounding modes.

        ``raw_query``/``max_results`` are forwarded only to the
        ``retrieve_grounding`` mode (the only mode that performs
        retrieval) — they have no meaning for the other two modes, which
        never call the grounding service. ``source_verifier`` is optional
        (default ``None``, preserving every existing caller unchanged) and
        is forwarded identically to the ``retrieve_grounding`` and
        supplied-bundle modes only — never to general-knowledge mode.
        """
        if request.retrieve_grounding:
            return await self._evidence_checked_service.teach(
                request,
                budget_policy=budget_policy,
                raw_query=raw_query,
                max_results=max_results,
                source_verifier=source_verifier,
            )

        if request.grounding_bundle is not None:
            return await answer_from_bundle(
                request=request,
                bundle=request.grounding_bundle,
                router=self._router,
                budget_policy=budget_policy,
                source_verifier=source_verifier,
            )

        if request.general_knowledge_acknowledged:
            return await answer_from_general_knowledge(
                request, router=self._router, budget_policy=budget_policy
            )

        raise ValueError(
            "TutorTeachingRequest selects none of retrieve_grounding, "
            "grounding_bundle, or general_knowledge_acknowledged=True"
        )
