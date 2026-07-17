from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from personal_lms.domain.enums import SourceType
from personal_lms.domain.extraction import (
    ExtractedArtifact,
    ExtractionArtifactProvenance,
    ExtractionCapability,
    ExtractionJob,
    ExtractionJobStatus,
)
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.promotion import PromotionBlocker, PromotionEligibility
from personal_lms.domain.source_inventory import (
    SourceApprovalStatus,
    SourceInventoryRecord,
    SourceLocatorKind,
    SourceMediaType,
    SourceRightsStatus,
    SourceVersion,
)
from personal_lms.promotion.eligibility import evaluate_promotion_eligibility

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _inventory_source(**overrides: object) -> SourceInventoryRecord:
    defaults: dict[str, object] = {
        "source_id": uuid4(),
        "locator_kind": SourceLocatorKind.WEB_URL,
        "locator": "https://example.com/a",
        "media_type": SourceMediaType.HTML,
        "approval_status": SourceApprovalStatus.APPROVED,
        "rights_status": SourceRightsStatus.PUBLIC_REFERENCE,
        "privacy_classification": PrivacyClassification.INTERNAL,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return SourceInventoryRecord.model_validate(defaults)


def _version(inventory_source_id: object, **overrides: object) -> SourceVersion:
    defaults: dict[str, object] = {
        "source_id": inventory_source_id,
        "content_hash_sha256": "a" * 64,
        "observed_at": _NOW,
    }
    defaults.update(overrides)
    return SourceVersion.model_validate(defaults)


def _job(**overrides: object) -> ExtractionJob:
    defaults: dict[str, object] = {
        "inventory_source_id": uuid4(),
        "source_version_id": uuid4(),
        "requested_capability": ExtractionCapability.PLAIN_TEXT,
        "media_kind": SourceMediaType.HTML,
        "status": ExtractionJobStatus.SUCCEEDED,
        "created_at": _NOW,
        "updated_at": _NOW,
        "idempotency_key": "k",
    }
    defaults.update(overrides)
    return ExtractionJob.model_validate(defaults)


def _artifact(
    *, job_id: object, inventory_source_id: object, source_version_id: object
) -> ExtractedArtifact:
    return ExtractedArtifact(
        artifact_id=uuid4(),
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_locator="candidate://x",
        created_at=_NOW,
        provenance=ExtractionArtifactProvenance(
            job_id=job_id,
            inventory_source_id=inventory_source_id,
            source_version_id=source_version_id,
            extractor_name="fake-extractor",
            extractor_version="0.0.0-test",
            extracted_at=_NOW,
        ),
    )


def _eligible_inputs(**source_overrides: object) -> dict[str, object]:
    source = _inventory_source(**source_overrides)
    version = _version(source.source_id)
    job = _job(inventory_source_id=source.source_id, source_version_id=version.version_id)
    artifact = _artifact(
        job_id=job.job_id,
        inventory_source_id=source.source_id,
        source_version_id=version.version_id,
    )
    return {
        "inventory_source": source,
        "source_version": version,
        "extraction_job": job,
        "artifact": artifact,
        "proposed_privacy_classification": source.privacy_classification,
        "proposed_title": "A Title",
        "already_promoted": False,
    }


def test_fully_eligible_case() -> None:
    eligibility, blockers = evaluate_promotion_eligibility(**_eligible_inputs())
    assert eligibility is PromotionEligibility.ELIGIBLE
    assert blockers == ()


def test_unapproved_source_blocked() -> None:
    inputs = _eligible_inputs(approval_status=SourceApprovalStatus.UNREVIEWED)
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.BLOCKED
    assert PromotionBlocker.SOURCE_NOT_APPROVED in blockers


@pytest.mark.parametrize(
    "rights_status", [SourceRightsStatus.UNKNOWN, SourceRightsStatus.RESTRICTED]
)
def test_unclear_or_prohibited_rights_blocked(rights_status: SourceRightsStatus) -> None:
    inputs = _eligible_inputs(rights_status=rights_status)
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.BLOCKED
    assert PromotionBlocker.RIGHTS_NOT_CLEARED in blockers


def test_failed_extraction_job_blocked() -> None:
    inputs = _eligible_inputs()
    inputs["extraction_job"] = inputs["extraction_job"].model_copy(
        update={"status": ExtractionJobStatus.FAILED_TERMINAL}
    )
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.BLOCKED
    assert PromotionBlocker.EXTRACTION_NOT_SUCCESSFUL in blockers


def test_artifact_from_another_source_blocked() -> None:
    inputs = _eligible_inputs()
    inputs["artifact"] = _artifact(
        job_id=inputs["extraction_job"].job_id,
        inventory_source_id=uuid4(),  # different source
        source_version_id=inputs["source_version"].version_id,
    )
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.BLOCKED
    assert PromotionBlocker.ARTIFACT_SOURCE_MISMATCH in blockers


def test_artifact_from_another_version_blocked() -> None:
    inputs = _eligible_inputs()
    inputs["artifact"] = _artifact(
        job_id=inputs["extraction_job"].job_id,
        inventory_source_id=inputs["inventory_source"].source_id,
        source_version_id=uuid4(),  # different version
    )
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.BLOCKED
    assert PromotionBlocker.ARTIFACT_VERSION_MISMATCH in blockers


def test_privacy_downgrade_blocked() -> None:
    inputs = _eligible_inputs(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY)
    inputs["proposed_privacy_classification"] = PrivacyClassification.PUBLIC
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.BLOCKED
    assert PromotionBlocker.PRIVACY_DOWNGRADE_FORBIDDEN in blockers


def test_privacy_same_or_stricter_allowed() -> None:
    inputs = _eligible_inputs(privacy_classification=PrivacyClassification.SENSITIVE)
    inputs["proposed_privacy_classification"] = PrivacyClassification.RESTRICTED_LOCAL_ONLY
    eligibility, _ = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.ELIGIBLE


def test_restricted_local_only_can_promote_to_restricted_local_only() -> None:
    inputs = _eligible_inputs(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY)
    inputs["proposed_privacy_classification"] = PrivacyClassification.RESTRICTED_LOCAL_ONLY
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.ELIGIBLE
    assert blockers == ()


def test_already_promoted_blocked() -> None:
    inputs = _eligible_inputs()
    inputs["already_promoted"] = True
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.BLOCKED
    assert PromotionBlocker.ALREADY_PROMOTED in blockers


def test_missing_title_blocked() -> None:
    inputs = _eligible_inputs()
    inputs["proposed_title"] = "   "
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.BLOCKED
    assert PromotionBlocker.MISSING_REQUIRED_METADATA in blockers


def test_multiple_blockers_all_reported() -> None:
    inputs = _eligible_inputs(
        approval_status=SourceApprovalStatus.REJECTED,
        rights_status=SourceRightsStatus.RESTRICTED,
    )
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.BLOCKED
    assert PromotionBlocker.SOURCE_NOT_APPROVED in blockers
    assert PromotionBlocker.RIGHTS_NOT_CLEARED in blockers


# --- domain-neutral coverage: no certification/course/CCNA required -------------


@pytest.mark.parametrize(
    ("proposed_source_type", "locator", "media_type"),
    [
        (SourceType.URL, "https://example.com/networking-guide", SourceMediaType.HTML),
        (SourceType.URL, "https://example.com/linux-admin-notes", SourceMediaType.HTML),
        (SourceType.PDF, "/archive/kubernetes-operators.pdf", SourceMediaType.PDF),
        (SourceType.URL, "https://example.com/aws-well-architected", SourceMediaType.HTML),
        (SourceType.DOCUMENT, "/archive/career-interview-prep.docx", SourceMediaType.TEXT),
        (SourceType.DOCUMENT, "/archive/project-runbook.md", SourceMediaType.MARKDOWN),
    ],
)
def test_domain_neutral_eligibility_across_topics(
    proposed_source_type: SourceType, locator: str, media_type: SourceMediaType
) -> None:
    locator_kind = (
        SourceLocatorKind.WEB_URL if locator.startswith("http") else SourceLocatorKind.FILE_PATH
    )
    inputs = _eligible_inputs(locator=locator, locator_kind=locator_kind, media_type=media_type)
    eligibility, blockers = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.ELIGIBLE
    assert blockers == ()


def test_eligibility_never_references_certification_fields() -> None:
    """No certification/course/exam-objective mapping is required anywhere
    in eligibility evaluation — knowledge_domains/certifications/courses/
    topics default to empty tuples and eligibility is unaffected."""
    inputs = _eligible_inputs()
    assert inputs["inventory_source"].certifications == ()
    assert inputs["inventory_source"].courses == ()
    eligibility, _ = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.ELIGIBLE


def test_optional_cross_domain_metadata_preserved_when_present() -> None:
    inputs = _eligible_inputs(
        knowledge_domains=("kubernetes",), certifications=("KCNA",), topics=("pods",)
    )
    eligibility, _ = evaluate_promotion_eligibility(**inputs)
    assert eligibility is PromotionEligibility.ELIGIBLE
    assert inputs["inventory_source"].certifications == ("KCNA",)
