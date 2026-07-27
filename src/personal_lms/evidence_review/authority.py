"""Evidence authority snapshot: what persisted decisions currently authorize.

This module is the *only* bridge from stored reviewer decisions to
retrieval and gate eligibility. Nothing downstream reads a pack's authored
``review_state`` or ``trust_status`` to decide whether evidence may be
used, because those are authoring fields — a fixture writing
``review_state: approved`` about itself is making a claim, not granting
permission. Treating that claim as authority made the entire review
boundary decorative in the first implementation.

A snapshot is computed once against a specific pack, recomputes each
region's subject digest from the pack's *current* content, and looks up
the current persisted decision for that region. An approval only counts
when its digest still matches, so any change to the pack version,
objective version, source bytes, selector, or content silently revokes it
— silently in the sense that no code has to remember to check, not in the
sense that it goes unreported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
from personal_lms.evidence_review.service import EvidenceReviewService

__all__ = [
    "AuthorityVerdict",
    "EvidenceAuthoritySnapshot",
    "review_kind_for",
    "subject_digest_for",
    "verify_decision",
]


def review_kind_for(region: EvidenceRegion) -> EvidenceReviewKind:
    """The review kind a region requires, decided by its selector."""
    return (
        EvidenceReviewKind.VISUAL
        if isinstance(region.selector, ImageRegionSelector)
        else EvidenceReviewKind.TEXT
    )


def subject_digest_for(
    pack: ObjectivePack, region: EvidenceRegion, artifact: SourceArtifactRef
) -> str:
    """The review-subject digest for one region, recomputed from the pack.

    Content is taken from the region's own ``review_content_for`` rather
    than its authored ``content_sha256``, so a pack cannot bind a
    reviewer's approval to text the reviewer never saw.
    """
    kind = review_kind_for(region)
    return compute_review_subject_digest(
        pack_id=pack.manifest.pack_id,
        pack_version=pack.manifest.pack_version,
        objective_ref=pack.objective_ref,
        evidence_id=region.evidence_id,
        kind=kind,
        source_id=artifact.source_id,
        source_sha256=artifact.sha256,
        selector=region.selector,
        resolved_content=region.review_content_for(kind),
    )


@dataclass(frozen=True, slots=True)
class AuthorityVerdict:
    """Why one persisted decision does or does not authorize a region."""

    evidence_id: str
    authorized: bool
    reason: str

    @property
    def is_stale(self) -> bool:
        return self.reason == "subject_digest_mismatch"


def verify_decision(
    decision: EvidenceReviewDecision,
    *,
    pack: ObjectivePack,
    region: EvidenceRegion,
    artifact: SourceArtifactRef,
) -> AuthorityVerdict:
    """The single read-time authority check, used everywhere decisions are read.

    Every identity component is verified here rather than trusted from
    write time. A decision could have reached the store by any route —
    a direct repository call, a restored backup, a hand-edited row — so
    the read path re-establishes the whole subject rather than assuming
    the write path did.

    Order runs cheapest-and-most-specific first so the reported reason is
    stable and says the most useful thing.
    """

    def deny(reason: str) -> AuthorityVerdict:
        return AuthorityVerdict(evidence_id=region.evidence_id, authorized=False, reason=reason)

    if decision.evidence_id != region.evidence_id:
        return deny("evidence_id_mismatch")
    if decision.pack_id != pack.manifest.pack_id:
        return deny("pack_id_mismatch")
    if decision.pack_version != pack.manifest.pack_version:
        return deny("pack_version_mismatch")
    if decision.objective_ref != pack.objective_ref:
        return deny("objective_ref_mismatch")
    if decision.source_id != artifact.source_id:
        return deny("source_id_mismatch")
    if decision.source_sha256 != artifact.sha256:
        return deny("source_bytes_mismatch")
    if decision.kind is not review_kind_for(region):
        return deny("review_kind_mismatch")
    if decision.outcome is not EvidenceReviewOutcome.APPROVED:
        return deny(f"outcome_{decision.outcome.value}")
    if (
        decision.kind is EvidenceReviewKind.VISUAL
        and decision.accessible_description != region.accessible_description
    ):
        # The reviewer approved a description; a different one ships.
        return deny("accessible_description_mismatch")
    if decision.subject_digest != subject_digest_for(pack, region, artifact):
        return deny("subject_digest_mismatch")

    return AuthorityVerdict(evidence_id=region.evidence_id, authorized=True, reason="approved")


@dataclass(frozen=True, slots=True)
class EvidenceAuthoritySnapshot:
    """What the persisted review record currently authorizes for one pack.

    Immutable and computed once per run, so every consumer in a gate
    evaluation sees the same authority state and two consumers cannot
    disagree about whether a region is approved.
    """

    pack_id: str
    pack_version: str
    objective_ref: str
    approved_evidence_ids: tuple[str, ...] = ()
    approved_visual_evidence_ids: tuple[str, ...] = ()
    required_visual_evidence_ids: tuple[str, ...] = ()
    pending_evidence_ids: tuple[str, ...] = ()
    stale_evidence_ids: tuple[str, ...] = ()
    decisions_by_evidence_id: dict[str, EvidenceReviewDecision] = field(default_factory=dict)

    def authorizes(self, evidence_id: str) -> bool:
        """Whether a current, matching approval covers ``evidence_id``."""
        return evidence_id in self.approved_evidence_ids

    @property
    def visual_review_satisfied(self) -> bool:
        """Whether every image region carries a current approved visual review.

        A pack with **no** image regions returns ``False``, deliberately.
        The Gate 1 requirement is that one infographic claim has a
        recorded human visual review; a pack containing nothing to review
        has not met that requirement, it has merely avoided it. Vacuous
        truth here would let a pack pass the visual check by deleting its
        only image.
        """
        if not self.required_visual_evidence_ids:
            return False
        return set(self.required_visual_evidence_ids) <= set(self.approved_visual_evidence_ids)

    @classmethod
    def build(
        cls, *, pack: ObjectivePack, review_service: EvidenceReviewService
    ) -> EvidenceAuthoritySnapshot:
        """Compute the snapshot for ``pack`` against currently stored decisions.

        Every decision goes through ``verify_decision``, so identity,
        outcome, description, and digest are all re-established at read
        time. Nothing here consults the pack's authored review state.
        """
        artifacts = pack.sources_by_id
        approved: list[str] = []
        approved_visual: list[str] = []
        required_visual: list[str] = []
        pending: list[str] = []
        stale: list[str] = []
        decisions: dict[str, EvidenceReviewDecision] = {}

        for region in sorted(pack.evidence_regions, key=lambda item: item.evidence_id):
            if isinstance(region.selector, ImageRegionSelector):
                required_visual.append(region.evidence_id)

            artifact = artifacts.get(region.source_id)
            if artifact is None:
                # Unresolvable provenance can never be authorized, and it
                # is a pending review rather than a silent omission.
                pending.append(region.evidence_id)
                continue

            current = review_service.current_decision_for_subject(pack=pack, region=region)
            if current is None:
                pending.append(region.evidence_id)
                continue

            decisions[region.evidence_id] = current
            verdict = verify_decision(current, pack=pack, region=region, artifact=artifact)
            if verdict.authorized:
                approved.append(region.evidence_id)
                if current.kind is EvidenceReviewKind.VISUAL:
                    approved_visual.append(region.evidence_id)
            elif verdict.is_stale:
                # A decision exists but was made against a different
                # subject — the region needs a fresh look, and reporting
                # only "not approved" would hide why.
                stale.append(region.evidence_id)
            else:
                pending.append(region.evidence_id)

        return cls(
            pack_id=pack.manifest.pack_id,
            pack_version=pack.manifest.pack_version,
            objective_ref=pack.objective_ref,
            approved_evidence_ids=tuple(sorted(approved)),
            approved_visual_evidence_ids=tuple(sorted(approved_visual)),
            required_visual_evidence_ids=tuple(sorted(required_visual)),
            pending_evidence_ids=tuple(sorted(pending)),
            stale_evidence_ids=tuple(sorted(stale)),
            decisions_by_evidence_id=decisions,
        )
