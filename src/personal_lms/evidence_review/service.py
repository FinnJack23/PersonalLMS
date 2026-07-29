"""Evidence review service: the only path by which evidence becomes approved.

This service records decisions a human reviewer made. It does not make
them, infer them, or derive them from any other state. There is no method
here that approves anything on a caller's behalf, and no default that
produces an approval — a caller must supply an explicit outcome, a
reviewer identity, and a reason.

Three guarantees are enforced here rather than left to callers:

- **Subject binding.** ``record_decision`` recomputes the review-subject
  digest from the pack, region, and artifact actually supplied and refuses
  a decision whose digest disagrees. A reviewer's approval is bound to the
  exact thing they saw — pack version, objective version, source bytes,
  selector, and content — so it cannot be re-scoped afterwards.
- **Kind and description agreement.** A text decision cannot approve an
  image region, and an approved visual decision must carry exactly the
  accessible description the region holds. Otherwise a reviewer could
  approve one description while the pack ships another.
- **Linear history.** There is exactly one root decision per region and
  exactly one child per decision. Forks and second roots are rejected, so
  "the current decision" is never ambiguous.

The service holds no clock. Callers supply ``decided_at`` on the decision,
matching the convention used by every other repository-backed service in
this codebase.
"""

from __future__ import annotations

from personal_lms.domain.evidence_review import (
    EvidenceReviewDecision,
    EvidenceReviewKind,
    EvidenceReviewOutcome,
    compute_review_subject_digest,
)
from personal_lms.domain.objective_packs import (
    EvidenceRegion,
    ImageRegionSelector,
    ObjectivePack,
    SourceArtifactRef,
)
from personal_lms.evidence_review.errors import (
    EvidenceReviewContractError,
    StaleEvidenceReviewError,
)
from personal_lms.evidence_review.protocol import EvidenceReviewRepository

__all__ = ["EvidenceReviewService"]


class EvidenceReviewService:
    """Records and interprets reviewer decisions over evidence regions."""

    def __init__(self, repository: EvidenceReviewRepository) -> None:
        self._repository = repository

    def record_decision(
        self,
        decision: EvidenceReviewDecision,
        *,
        pack: ObjectivePack,
        region: EvidenceRegion,
        artifact: SourceArtifactRef,
    ) -> EvidenceReviewDecision:
        """Append one decision after proving it matches the current subject.

        ``pack``, ``region``, and ``artifact`` are all required rather than
        optional so the binding check cannot be skipped by omission — a
        caller that does not have the current records cannot record a
        decision at all.
        """
        self._assert_identity_agrees(decision, pack=pack, region=region, artifact=artifact)
        self._assert_kind_agrees(decision, region=region)
        self._assert_description_agrees(decision, region=region)

        expected_digest = compute_review_subject_digest(
            pack_id=pack.manifest.pack_id,
            pack_version=pack.manifest.pack_version,
            objective_ref=pack.objective_ref,
            evidence_id=region.evidence_id,
            kind=decision.kind,
            source_id=artifact.source_id,
            source_sha256=artifact.sha256,
            selector=region.selector,
            resolved_content=region.review_content_for(decision.kind),
        )
        if decision.subject_digest != expected_digest:
            raise StaleEvidenceReviewError(
                "the decision was made against a different review subject than the one "
                "supplied; a stale or re-scoped subject can never be approved"
            )
        if decision.source_sha256 != artifact.sha256:
            raise StaleEvidenceReviewError(
                "the decision names different source bytes than the artifact currently holds"
            )

        self._assert_supersession_is_linear(decision)
        return self._repository.append(decision)

    @staticmethod
    def _assert_identity_agrees(
        decision: EvidenceReviewDecision,
        *,
        pack: ObjectivePack,
        region: EvidenceRegion,
        artifact: SourceArtifactRef,
    ) -> None:
        if decision.evidence_id != region.evidence_id:
            raise EvidenceReviewContractError(
                "the decision names a different evidence region than the one supplied"
            )
        if decision.source_id != artifact.source_id:
            raise EvidenceReviewContractError(
                "the decision names a different source artifact than the one supplied"
            )
        if region.source_id != artifact.source_id:
            raise EvidenceReviewContractError(
                "the supplied region does not belong to the supplied source artifact"
            )
        if decision.pack_id != pack.manifest.pack_id:
            raise EvidenceReviewContractError(
                "the decision names a different pack than the one supplied"
            )
        if decision.pack_version != pack.manifest.pack_version:
            raise EvidenceReviewContractError(
                "the decision names a different pack version than the one supplied"
            )
        if decision.objective_ref != pack.objective_ref:
            raise EvidenceReviewContractError(
                "the decision names a different objective version than the pack's"
            )

    @staticmethod
    def _assert_kind_agrees(decision: EvidenceReviewDecision, *, region: EvidenceRegion) -> None:
        """A text decision cannot approve an image region, or vice versa."""
        expected = (
            EvidenceReviewKind.VISUAL
            if isinstance(region.selector, ImageRegionSelector)
            else EvidenceReviewKind.TEXT
        )
        if decision.kind is not expected:
            raise EvidenceReviewContractError(
                f"the decision has kind={decision.kind.value} but the region requires "
                f"kind={expected.value}; a visual region needs a visual review"
            )

    @staticmethod
    def _assert_description_agrees(
        decision: EvidenceReviewDecision, *, region: EvidenceRegion
    ) -> None:
        """An approved visual decision must describe exactly what ships.

        Approving one description while the pack carries another would let
        the learner-visible text differ from the reviewed text.
        """
        if decision.kind is not EvidenceReviewKind.VISUAL:
            return
        if decision.outcome is not EvidenceReviewOutcome.APPROVED:
            return
        if decision.accessible_description != region.accessible_description:
            raise EvidenceReviewContractError(
                "the approved accessible description does not match the region's own; "
                "a reviewer may only approve the description that actually ships"
            )

    def _assert_supersession_is_linear(self, decision: EvidenceReviewDecision) -> None:
        """One root per region, one child per decision.

        Checked here for a clear error message; the repository enforces the
        same invariant with a database constraint, so a caller bypassing
        this service still cannot create a fork.
        """
        existing = self._repository.history_for_subject(
            evidence_id=decision.evidence_id,
            pack_id=decision.pack_id,
            pack_version=decision.pack_version,
            objective_ref=decision.objective_ref,
        )
        if not existing:
            if decision.supersedes_decision_id is not None:
                raise EvidenceReviewContractError(
                    "the first decision about a region must not supersede anything"
                )
            return

        if decision.supersedes_decision_id is None:
            if any(entry.decision_id == decision.decision_id for entry in existing):
                return  # An idempotent repeat; the repository will confirm it.
            raise EvidenceReviewContractError(
                "this region already has a root decision; a correction must supersede "
                "the current leaf rather than start a second root"
            )

        leaf = self._repository.current_for_subject(
            evidence_id=decision.evidence_id,
            pack_id=decision.pack_id,
            pack_version=decision.pack_version,
            objective_ref=decision.objective_ref,
        )
        if leaf is not None and decision.supersedes_decision_id != leaf.decision_id:
            raise EvidenceReviewContractError(
                "the decision supersedes a record that has itself already been "
                "superseded; review history is linear, so a correction must name "
                "the current leaf"
            )

    def current_decision(self, evidence_id: str) -> EvidenceReviewDecision | None:
        """The latest non-superseded decision for a bare evidence id.

        Kept for callers that genuinely have only an id. Anything holding
        a pack should use ``current_decision_for_subject``, which cannot
        confuse two packs that review the same evidence id.
        """
        return self._repository.current_for(evidence_id)

    def current_decision_for_subject(
        self, *, pack: ObjectivePack, region: EvidenceRegion
    ) -> EvidenceReviewDecision | None:
        """The current decision for one complete logical subject."""
        current: EvidenceReviewDecision | None = self._repository.current_for_subject(
            evidence_id=region.evidence_id,
            pack_id=pack.manifest.pack_id,
            pack_version=pack.manifest.pack_version,
            objective_ref=pack.objective_ref,
        )
        return current

    def history(self, evidence_id: str) -> tuple[EvidenceReviewDecision, ...]:
        """The full append-only decision history, oldest first."""
        return self._repository.history_for(evidence_id)

    def is_approved(
        self, pack: ObjectivePack, region: EvidenceRegion, artifact: SourceArtifactRef
    ) -> bool:
        """Whether a current approval authorizes exactly this subject.

        Fails closed on every uncertainty: no decision, a superseded one, a
        non-approval outcome, or a subject that no longer matches all
        return ``False``. There is no path through this method that returns
        ``True`` without a persisted reviewer approval bound to the current
        subject.
        """
        current = self._repository.current_for(region.evidence_id)
        if current is None:
            return False
        expected = compute_review_subject_digest(
            pack_id=pack.manifest.pack_id,
            pack_version=pack.manifest.pack_version,
            objective_ref=pack.objective_ref,
            evidence_id=region.evidence_id,
            kind=current.kind,
            source_id=artifact.source_id,
            source_sha256=artifact.sha256,
            selector=region.selector,
            resolved_content=region.review_content_for(current.kind),
        )
        return current.authorizes(subject_digest=expected)
