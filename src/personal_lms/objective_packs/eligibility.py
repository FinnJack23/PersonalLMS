"""Evidence eligibility policy: the single definition of "may this evidence be used?".

One policy, applied in two places. This module holds the *definition* —
a pure, deterministic function from (policy, source artifact, evidence
region) to an ``EligibilityState`` plus a stable reason. The retrieval
layer applies the same dimensions in SQL before ``LIMIT`` (see
``content.protocol.ChunkEligibilityFilter``) so a chunk that this policy
would block is never even a ranking candidate.

Keeping both in step is a deliberate design choice rather than an
accident: the dimensions are enumerated once here, in
``ELIGIBILITY_DIMENSIONS``, and the content-layer filter names the same
set. A test asserts the two agree.

Evaluation order is fixed and total. Every input produces exactly one
outcome, and the *first* failing dimension is the reported reason, so a
region that is both quarantined and unreviewed always reports
``EVIDENCE_QUARANTINED`` — stable across runs and across refactors of the
underlying data.

Order rationale: hard exclusions that describe the material itself
(quarantine, rights) come before decisions about a *use* of it (permitted
use, privacy ceiling), which come before judgements about its *state*
(review, trust), which come last because they are the ones a reviewer can
change. The strictest applicable rule always wins, and a region can never
be more eligible than the artifact it came from.

Nothing here grants approval. The policy can only *recognize* an approval
that a reviewer already persisted; see ``domain.evidence_review``.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from personal_lms.domain.objective_packs import (
    EligibilityState,
    EvidenceRegion,
    PermittedUse,
    QuarantineStatus,
    SourceArtifactRef,
    TrustStatus,
    ValidationReasonCode,
)
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import SourceRightsStatus
from personal_lms.objective_packs.hashing import hash_records

__all__ = [
    "ELIGIBILITY_DIMENSIONS",
    "EligibilityDecision",
    "EvidenceEligibility",
    "EvidenceIndexSnapshot",
    "EvidencePolicy",
    "ReviewAuthority",
    "allowed_privacy_classifications",
]

#: The structural dimensions this policy evaluates, in evaluation order.
#: The content-retrieval filter applies exactly this set before ``LIMIT``
#: — see ``content.protocol.ChunkEligibilityFilter.DIMENSIONS``.
ELIGIBILITY_DIMENSIONS: tuple[str, ...] = (
    "quarantine",
    "rights",
    "permitted_use",
    "privacy",
    "objective_version",
    "review_state",
    "trust",
)

# Explicit privacy ordering, deliberately not derived from the enum's
# declaration order so a future reordering for display purposes can never
# silently change a security decision. Mirrors
# ``librarian.content_grounding._PRIVACY_RANK``, which makes the same
# choice for the same reason.
_PRIVACY_RANK: MappingProxyType[PrivacyClassification, int] = MappingProxyType(
    {
        PrivacyClassification.PUBLIC: 0,
        PrivacyClassification.INTERNAL: 1,
        PrivacyClassification.SENSITIVE: 2,
        PrivacyClassification.RESTRICTED_LOCAL_ONLY: 3,
    }
)

# Rights states that permit no local use at all, regardless of what a
# pack's permitted_uses claims. RESTRICTED is a hard denial; UNKNOWN is
# treated as a denial too — absence of a recorded right is not a grant.
_RIGHTS_DENYING = frozenset({SourceRightsStatus.RESTRICTED, SourceRightsStatus.UNKNOWN})


def allowed_privacy_classifications(
    ceiling: PrivacyClassification,
) -> frozenset[PrivacyClassification]:
    """Every classification a consumer at ``ceiling`` may see.

    A classification is permitted iff its explicit rank is less than or
    equal to the ceiling's: ``INTERNAL`` permits ``{PUBLIC, INTERNAL}``,
    never ``SENSITIVE``.
    """
    ceiling_rank = _PRIVACY_RANK[ceiling]
    return frozenset(
        classification for classification, rank in _PRIVACY_RANK.items() if rank <= ceiling_rank
    )


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    """What a caller is asking to do with evidence.

    ``policy_version`` is recorded in every snapshot and gate report so a
    later policy change produces a visibly different result rather than
    silently reinterpreting a frozen one.

    ``require_trusted`` defaults to ``True``: answer-bearing and teaching
    use both demand trusted evidence. A caller doing exploratory
    retrieval may relax it, but nothing in the gate path does.
    """

    policy_version: str
    objective_ref: str
    requested_use: PermittedUse = PermittedUse.LOCAL_TEACH
    privacy_ceiling: PrivacyClassification = PrivacyClassification.INTERNAL
    require_trusted: bool = True
    require_approved_review: bool = True
    require_objective_match: bool = True


@runtime_checkable
class ReviewAuthority(Protocol):
    """What a persisted review record currently authorizes.

    Structural, so this package needs no import from ``evidence_review``
    and stays a pure policy layer. The implementation is
    ``evidence_review.authority.EvidenceAuthoritySnapshot``.
    """

    def authorizes(self, evidence_id: str) -> bool:
        """Whether a current, subject-matching approval covers this region."""
        ...


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    """One evidence region's outcome under one policy."""

    evidence_id: str
    state: EligibilityState
    reason_code: ValidationReasonCode | None = None
    dimension: str | None = None

    @property
    def is_eligible(self) -> bool:
        return self.state is EligibilityState.ELIGIBLE


class EvidenceEligibility:
    """Evaluates evidence against one policy. Pure and deterministic.

    Holds no state beyond the policy and performs no I/O, so the same
    inputs always produce the same decision — a requirement for the
    reproducible-index-hash gate check.
    """

    def __init__(self, policy: EvidencePolicy, *, authority: ReviewAuthority | None = None) -> None:
        self._policy = policy
        self._permitted_privacy = allowed_privacy_classifications(policy.privacy_ceiling)
        self._authority = authority

    @property
    def policy(self) -> EvidencePolicy:
        return self._policy

    @property
    def authority(self) -> ReviewAuthority | None:
        return self._authority

    def permits_use(self, artifact: SourceArtifactRef) -> bool:
        """Whether ``artifact`` is cleared for the policy's requested use.

        Absence of a use in ``permitted_uses`` is denial. There is no
        implied grant and no hierarchy between uses — clearing something
        for ``local_index`` says nothing about ``local_teach``.
        """
        return self._policy.requested_use in artifact.permitted_uses

    def evaluate(self, region: EvidenceRegion, artifact: SourceArtifactRef) -> EligibilityDecision:
        """The single, total eligibility decision for one region.

        ``artifact`` must be the region's own source; passing an unrelated
        artifact is a caller error that this method cannot detect, which
        is why ``EvidenceIndexSnapshot.build`` resolves the pairing itself
        rather than trusting callers to.
        """
        policy = self._policy

        # 1. Quarantine — a hard exclusion on the material itself.
        if (
            region.quarantine_status is QuarantineStatus.QUARANTINED
            or artifact.quarantine_status is QuarantineStatus.QUARANTINED
        ):
            return self._blocked(region, ValidationReasonCode.EVIDENCE_QUARANTINED, "quarantine")

        # 2. Rights — no recorded right is a denial, not a grant.
        if artifact.rights_status in _RIGHTS_DENYING:
            return self._blocked(region, ValidationReasonCode.RIGHTS_DENIED, "rights")

        # 3. Permitted use — enumerated, never implied.
        if not self.permits_use(artifact):
            return self._blocked(region, ValidationReasonCode.USE_NOT_PERMITTED, "permitted_use")

        # 4. Privacy ceiling — the stricter of region and artifact wins.
        if (
            region.privacy_classification not in self._permitted_privacy
            or artifact.privacy_classification not in self._permitted_privacy
        ):
            return self._blocked(region, ValidationReasonCode.PRIVACY_RESTRICTED, "privacy")

        # 5. Objective/version scope — a wrong-blueprint region is out of
        #    scope rather than blocked: it may be perfectly good evidence
        #    for a different objective version.
        if policy.require_objective_match and policy.objective_ref not in region.objective_refs:
            return EligibilityDecision(
                evidence_id=region.evidence_id,
                state=EligibilityState.INELIGIBLE,
                reason_code=ValidationReasonCode.OBJECTIVE_REF_MISMATCH,
                dimension="objective_version",
            )

        # 6. Review authority — the reviewer boundary.
        #
        # This consults the *persisted decision snapshot*, never the pack's
        # authored review_state. An authoring file saying "approved" about
        # itself is a claim; only a stored reviewer decision bound to the
        # current subject is authority. With no snapshot supplied, nothing
        # is approved — fail closed, so forgetting to wire the authority in
        # denies rather than silently permits.
        if policy.require_approved_review and (
            self._authority is None or not self._authority.authorizes(region.evidence_id)
        ):
            return self._blocked(region, ValidationReasonCode.EVIDENCE_NOT_REVIEWED, "review_state")

        # 7. Trust — the outcome of review, checked last because it is
        #    the dimension a reviewer decision can move. The authored
        #    trust_status may only *further restrict*: a pack marking its
        #    own evidence trusted grants nothing on its own, which is why
        #    the approval check above already had to pass.
        if policy.require_trusted and (
            region.trust_status is not TrustStatus.TRUSTED
            or artifact.trust_status is not TrustStatus.TRUSTED
        ):
            return self._blocked(region, ValidationReasonCode.UNTRUSTED_EVIDENCE, "trust")

        return EligibilityDecision(evidence_id=region.evidence_id, state=EligibilityState.ELIGIBLE)

    @staticmethod
    def _blocked(
        region: EvidenceRegion, reason_code: ValidationReasonCode, dimension: str
    ) -> EligibilityDecision:
        return EligibilityDecision(
            evidence_id=region.evidence_id,
            state=EligibilityState.BLOCKED,
            reason_code=reason_code,
            dimension=dimension,
        )


@dataclass(frozen=True, slots=True)
class EvidenceIndexSnapshot:
    """The deterministic eligible-evidence view of one pack under one policy.

    ``content_hash`` covers the *logical* eligible records — evidence IDs
    with their content hashes — never storage bytes. Two runs over the
    same pack therefore produce the same hash regardless of database
    layout, insertion order, or filesystem state.
    """

    policy_version: str
    objective_ref: str
    eligible_evidence_ids: tuple[str, ...]
    decisions: tuple[EligibilityDecision, ...]
    content_hash: str

    @property
    def excluded(self) -> dict[str, ValidationReasonCode]:
        """Evidence ID to the reason it was excluded, for every non-eligible region."""
        return {
            decision.evidence_id: decision.reason_code
            for decision in self.decisions
            if not decision.is_eligible and decision.reason_code is not None
        }

    @classmethod
    def build(
        cls,
        *,
        eligibility: EvidenceEligibility,
        regions: tuple[EvidenceRegion, ...],
        artifacts_by_id: dict[str, SourceArtifactRef],
    ) -> EvidenceIndexSnapshot:
        """Evaluate every region, resolving each one's own artifact.

        A region whose ``source_id`` has no artifact is ``BLOCKED`` with
        ``UNKNOWN_SOURCE_ID`` rather than skipped: unresolvable provenance
        is a failure to report, never an omission to overlook.
        """
        policy = eligibility.policy
        decisions: list[EligibilityDecision] = []

        for region in sorted(regions, key=lambda item: item.evidence_id):
            artifact = artifacts_by_id.get(region.source_id)
            if artifact is None:
                decisions.append(
                    EligibilityDecision(
                        evidence_id=region.evidence_id,
                        state=EligibilityState.BLOCKED,
                        reason_code=ValidationReasonCode.UNKNOWN_SOURCE_ID,
                        dimension="provenance",
                    )
                )
                continue
            decisions.append(eligibility.evaluate(region, artifact))

        eligible_ids = tuple(
            sorted(decision.evidence_id for decision in decisions if decision.is_eligible)
        )
        regions_by_id = {region.evidence_id: region for region in regions}
        content_hash = hash_records(
            (
                {
                    "evidence_id": evidence_id,
                    "content_sha256": regions_by_id[evidence_id].content_sha256,
                    "source_id": regions_by_id[evidence_id].source_id,
                }
                for evidence_id in eligible_ids
            ),
            sort=True,
        )

        return cls(
            policy_version=policy.policy_version,
            objective_ref=policy.objective_ref,
            eligible_evidence_ids=eligible_ids,
            decisions=tuple(decisions),
            content_hash=content_hash,
        )
