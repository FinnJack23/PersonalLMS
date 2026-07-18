"""Pure, deterministic promotion-eligibility evaluation.

No I/O, no database access, no clock, no randomness — every input is
already-loaded domain data, and the same inputs always produce the same
output. Used both when a ``PromotionCandidate`` is first created and
again, independently, by ``SourcePromotionService.promote`` immediately
before it writes to the curated catalog (see Part 9's "re-evaluate
eligibility at execution time" requirement) — never trusting a stale
snapshot.

Reuses the enums already defined in ``domain.source_inventory`` rather
than duplicating an equivalent set — see the module docstring in that
file for the layering this respects.
"""

from __future__ import annotations

from personal_lms.domain.extraction import ExtractedArtifact, ExtractionJob, ExtractionJobStatus
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.promotion import (
    PRIVACY_STRICTNESS_ORDER,
    PromotionBlocker,
    PromotionEligibility,
)
from personal_lms.domain.source_inventory import (
    SourceApprovalStatus,
    SourceInventoryRecord,
    SourceRightsStatus,
    SourceVersion,
)

#: Rights states permitting promotion. ``UNKNOWN`` and ``RESTRICTED`` are
#: deliberately excluded — this module never infers a legal conclusion
#: from silence; an unset rights status blocks promotion just as an
#: explicitly restricted one does.
_RIGHTS_ELIGIBLE_FOR_PROMOTION = frozenset(
    {
        SourceRightsStatus.OWNED,
        SourceRightsStatus.LICENSED,
        SourceRightsStatus.PUBLIC_REFERENCE,
    }
)


def evaluate_promotion_eligibility(
    *,
    inventory_source: SourceInventoryRecord,
    source_version: SourceVersion,
    extraction_job: ExtractionJob,
    artifact: ExtractedArtifact,
    proposed_privacy_classification: PrivacyClassification,
    proposed_title: str,
    already_promoted: bool,
) -> tuple[PromotionEligibility, tuple[PromotionBlocker, ...]]:
    """Evaluate every configured promotion condition and return every
    blocker that applies — never just the first one encountered."""
    blockers: list[PromotionBlocker] = []

    if extraction_job.status is not ExtractionJobStatus.SUCCEEDED:
        blockers.append(PromotionBlocker.EXTRACTION_NOT_SUCCESSFUL)

    if (
        artifact.provenance.inventory_source_id != inventory_source.source_id
        or source_version.source_id != inventory_source.source_id
    ):
        blockers.append(PromotionBlocker.ARTIFACT_SOURCE_MISMATCH)

    if artifact.provenance.source_version_id != source_version.version_id:
        blockers.append(PromotionBlocker.ARTIFACT_VERSION_MISMATCH)

    if inventory_source.approval_status is not SourceApprovalStatus.APPROVED:
        blockers.append(PromotionBlocker.SOURCE_NOT_APPROVED)

    if inventory_source.rights_status not in _RIGHTS_ELIGIBLE_FOR_PROMOTION:
        blockers.append(PromotionBlocker.RIGHTS_NOT_CLEARED)

    if (
        PRIVACY_STRICTNESS_ORDER[proposed_privacy_classification]
        < PRIVACY_STRICTNESS_ORDER[inventory_source.privacy_classification]
    ):
        blockers.append(PromotionBlocker.PRIVACY_DOWNGRADE_FORBIDDEN)

    if already_promoted:
        blockers.append(PromotionBlocker.ALREADY_PROMOTED)

    if not proposed_title.strip():
        blockers.append(PromotionBlocker.MISSING_REQUIRED_METADATA)

    if blockers:
        return PromotionEligibility.BLOCKED, tuple(blockers)
    return PromotionEligibility.ELIGIBLE, ()


__all__ = ["evaluate_promotion_eligibility"]
