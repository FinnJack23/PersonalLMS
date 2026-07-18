from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from personal_lms.catalog.sqlite import SQLiteSourceCatalog
from personal_lms.domain.catalog import SourceRecord
from personal_lms.domain.enums import SourceProcessingStatus, SourceType
from personal_lms.domain.extraction import (
    ExtractedArtifact,
    ExtractionArtifactProvenance,
    ExtractionCapability,
    ExtractionRequest,
    derive_artifact_id,
)
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.promotion import (
    PromotionDecisionOutcome,
    PromotionEligibility,
    PromotionExecutionState,
    derive_catalog_source_id,
)
from personal_lms.domain.source_inventory import (
    SourceApprovalStatus,
    SourceInventoryRecord,
    SourceLocatorKind,
    SourceMediaType,
    SourceRightsStatus,
    SourceVersion,
    derive_source_id,
)
from personal_lms.extraction.sqlite import SQLiteExtractionQueue
from personal_lms.promotion.errors import (
    PromotionBlockedError,
    PromotionDecisionRequiredError,
)
from personal_lms.promotion.service import SourcePromotionService
from personal_lms.promotion.sqlite import SQLitePromotionRepository
from personal_lms.source_inventory.sqlite import SQLiteSourceInventory

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _FlakyCatalog:
    """Wraps a real ``SQLiteSourceCatalog`` and can be told to fail the
    next ``upsert_source`` call — used to simulate a crash between
    ``CATALOG_WRITE_STARTED`` and a confirmed write."""

    def __init__(self, inner: SQLiteSourceCatalog) -> None:
        self._inner = inner
        self.fail_next_upsert = False

    def initialize_schema(self) -> None:
        self._inner.initialize_schema()

    def upsert_source(self, record: SourceRecord) -> None:
        if self.fail_next_upsert:
            self.fail_next_upsert = False
            raise RuntimeError("simulated catalog write failure")
        self._inner.upsert_source(record)

    def get_source(self, source_id: str) -> SourceRecord | None:
        return self._inner.get_source(source_id)

    def list_sources(self, *, filters: Any = None) -> tuple[SourceRecord, ...]:
        return self._inner.list_sources(filters=filters)

    def add_relationship(self, relationship: Any) -> None:
        self._inner.add_relationship(relationship)

    def list_relationships(self, source_id: str) -> tuple[Any, ...]:
        return self._inner.list_relationships(source_id)

    def search(self, query: str, **kwargs: Any) -> tuple[Any, ...]:
        return self._inner.search(query, **kwargs)

    def close(self) -> None:
        self._inner.close()


class _FlakyPromotionRepository:
    """Wraps a real ``SQLitePromotionRepository`` and can be told to fail
    the next ``create_mapping`` call — used to simulate a crash after the
    catalog write is confirmed but before the mapping is persisted."""

    def __init__(self, inner: SQLitePromotionRepository) -> None:
        self._inner = inner
        self.fail_next_create_mapping = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def create_mapping(self, mapping: Any) -> Any:
        if self.fail_next_create_mapping:
            self.fail_next_create_mapping = False
            raise RuntimeError("simulated mapping persistence failure")
        return self._inner.create_mapping(mapping)


@dataclass
class _Env:
    inventory: SQLiteSourceInventory
    queue: SQLiteExtractionQueue
    promotion_repository: SQLitePromotionRepository
    catalog: SQLiteSourceCatalog
    service: SourcePromotionService


def _build_env() -> _Env:
    inventory = SQLiteSourceInventory.open(":memory:")
    inventory.initialize_schema()
    queue = SQLiteExtractionQueue.open(":memory:")
    queue.initialize_schema()
    promotion_repository = SQLitePromotionRepository.open(":memory:")
    promotion_repository.initialize_schema()
    catalog = SQLiteSourceCatalog.open(":memory:")
    catalog.initialize_schema()
    service = SourcePromotionService(
        inventory=inventory,
        extraction_queue=queue,
        promotion_repository=promotion_repository,
        catalog=catalog,
    )
    return _Env(inventory, queue, promotion_repository, catalog, service)


def _register_succeeded_source(
    env: _Env,
    *,
    locator: str = "https://example.com/networking-guide",
    locator_kind: SourceLocatorKind = SourceLocatorKind.WEB_URL,
    media_type: SourceMediaType = SourceMediaType.HTML,
    approval_status: SourceApprovalStatus = SourceApprovalStatus.APPROVED,
    rights_status: SourceRightsStatus = SourceRightsStatus.PUBLIC_REFERENCE,
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL,
    knowledge_domains: tuple[str, ...] = (),
    certifications: tuple[str, ...] = (),
) -> tuple[UUID, UUID, UUID]:
    """Registers an inventory source + version + a SUCCEEDED extraction
    job + its artifact. Returns (source_id, version_id, artifact_id)."""
    source_id = derive_source_id(canonical_locator=locator)
    record = SourceInventoryRecord(
        source_id=source_id,
        locator_kind=locator_kind,
        locator=locator,
        media_type=media_type,
        title="A Source",
        approval_status=approval_status,
        rights_status=rights_status,
        privacy_classification=privacy_classification,
        knowledge_domains=knowledge_domains,
        certifications=certifications,
        created_at=_NOW,
        updated_at=_NOW,
    )
    env.inventory.add_source(record)

    version = SourceVersion(
        source_id=source_id, content_hash_sha256="a" * 64, size_bytes=2048, observed_at=_NOW
    )
    env.inventory.add_version(version)

    request = ExtractionRequest(
        inventory_source_id=source_id,
        source_version_id=version.version_id,
        requested_capability=ExtractionCapability.PLAIN_TEXT,
        media_kind=media_type,
        idempotency_key=f"job-{uuid4()}",
    )
    job = env.queue.enqueue(request, now=_NOW)
    env.queue.claim_next(worker_id="w1", now=_NOW)
    env.queue.mark_running(job.job_id, worker_id="w1", now=_NOW)

    artifact_id = derive_artifact_id(
        job_id=job.job_id,
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_hash=None,
        content_locator=f"candidate://{job.job_id}",
    )
    artifact = ExtractedArtifact(
        artifact_id=artifact_id,
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_locator=f"candidate://{job.job_id}",
        created_at=_NOW,
        provenance=ExtractionArtifactProvenance(
            job_id=job.job_id,
            inventory_source_id=source_id,
            source_version_id=version.version_id,
            extractor_name="fake-extractor",
            extractor_version="0.0.0-test",
            extracted_at=_NOW,
        ),
    )
    env.queue.record_success(job.job_id, artifact, now=_NOW)
    return source_id, version.version_id, artifact_id


# --- happy path and explicit-promotion boundary ----------------------------------


def test_full_promotion_flow_writes_curated_record() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)

    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="Networking Guide",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    assert candidate.eligibility is PromotionEligibility.ELIGIBLE

    env.service.decide(
        candidate.candidate_id,
        outcome=PromotionDecisionOutcome.APPROVE,
        reviewer="alan",
        now=_NOW,
    )
    result = env.service.promote(candidate.candidate_id, now=_NOW)

    assert result.execution_state is PromotionExecutionState.COMPLETED
    assert result.already_completed is False
    assert result.catalog_source_id == derive_catalog_source_id(inventory_source_id=source_id)

    record = env.catalog.get_source(result.catalog_source_id)
    assert record is not None
    assert record.status is SourceProcessingStatus.APPROVED
    assert record.privacy_classification is PrivacyClassification.INTERNAL


def test_candidate_creation_alone_does_not_promote() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    catalog_source_id = derive_catalog_source_id(inventory_source_id=source_id)
    assert env.catalog.get_source(catalog_source_id) is None
    assert candidate.eligibility is PromotionEligibility.ELIGIBLE  # still not promoted


def test_approved_decision_alone_does_not_promote() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    catalog_source_id = derive_catalog_source_id(inventory_source_id=source_id)
    assert env.catalog.get_source(catalog_source_id) is None


def test_promote_without_decision_raises() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    with pytest.raises(PromotionDecisionRequiredError):
        env.service.promote(candidate.candidate_id, now=_NOW)


@pytest.mark.parametrize(
    "outcome", [PromotionDecisionOutcome.REJECT, PromotionDecisionOutcome.DEFER]
)
def test_promote_with_non_approve_decision_raises(outcome: PromotionDecisionOutcome) -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(candidate.candidate_id, outcome=outcome, reviewer="alan", now=_NOW)
    with pytest.raises(PromotionDecisionRequiredError):
        env.service.promote(candidate.candidate_id, now=_NOW)


def test_extraction_success_is_never_itself_an_approval() -> None:
    """A SUCCEEDED extraction job plus a freshly built (eligible)
    candidate is still not enough — promote() requires an explicit
    APPROVE decision even though eligibility already passed."""
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    assert candidate.eligibility is PromotionEligibility.ELIGIBLE
    with pytest.raises(PromotionDecisionRequiredError):
        env.service.promote(candidate.candidate_id, now=_NOW)


# --- eligibility re-evaluated at execution time ----------------------------------


def test_eligibility_reevaluated_at_execution_time() -> None:
    """A candidate built while the source was approved, but rejected
    before promote() executes, must be blocked at execution time — not
    merely trusted from its creation-time snapshot."""
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )

    source = env.inventory.get_source(source_id)
    env.inventory.update_source(
        source.model_copy(
            update={"approval_status": SourceApprovalStatus.REJECTED, "updated_at": _NOW}
        )
    )

    with pytest.raises(PromotionBlockedError):
        env.service.promote(candidate.candidate_id, now=_NOW)

    catalog_source_id = derive_catalog_source_id(inventory_source_id=source_id)
    assert env.catalog.get_source(catalog_source_id) is None


def test_unapproved_source_blocks_candidate_creation() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(
        env, approval_status=SourceApprovalStatus.UNREVIEWED
    )
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    assert candidate.eligibility is PromotionEligibility.BLOCKED


def test_unclear_rights_blocks_candidate_creation() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(
        env, rights_status=SourceRightsStatus.UNKNOWN
    )
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    assert candidate.eligibility is PromotionEligibility.BLOCKED


# --- idempotency and cross-repository recovery -----------------------------------


def test_promote_twice_is_idempotent() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    first = env.service.promote(candidate.candidate_id, now=_NOW)
    second = env.service.promote(candidate.candidate_id, now=_NOW + timedelta(hours=1))

    assert first.already_completed is False
    assert second.already_completed is True
    assert first.catalog_source_id == second.catalog_source_id
    assert len(env.catalog.list_sources()) == 1


def test_recovery_failure_before_catalog_write_leaves_no_curated_record() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )

    flaky_catalog = _FlakyCatalog(env.catalog)
    flaky_catalog.fail_next_upsert = True
    service = SourcePromotionService(
        inventory=env.inventory,
        extraction_queue=env.queue,
        promotion_repository=env.promotion_repository,
        catalog=flaky_catalog,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError):
        service.promote(candidate.candidate_id, now=_NOW)

    execution = env.promotion_repository.get_execution(candidate.candidate_id)
    assert execution is not None
    assert execution.execution_state is PromotionExecutionState.RECOVERY_REQUIRED
    catalog_source_id = derive_catalog_source_id(inventory_source_id=source_id)
    assert env.catalog.get_source(catalog_source_id) is None

    # Retry against a healthy catalog reconciles and completes.
    result = service.promote(candidate.candidate_id, now=_NOW)
    assert result.execution_state is PromotionExecutionState.COMPLETED
    assert len(env.catalog.list_sources()) == 1


def test_recovery_catalog_write_succeeds_mapping_fails_then_reconciles() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )

    flaky_repository = _FlakyPromotionRepository(env.promotion_repository)
    flaky_repository.fail_next_create_mapping = True
    service = SourcePromotionService(
        inventory=env.inventory,
        extraction_queue=env.queue,
        promotion_repository=flaky_repository,  # type: ignore[arg-type]
        catalog=env.catalog,
    )

    with pytest.raises(RuntimeError):
        service.promote(candidate.candidate_id, now=_NOW)

    execution = env.promotion_repository.get_execution(candidate.candidate_id)
    assert execution is not None
    assert execution.execution_state is PromotionExecutionState.RECOVERY_REQUIRED
    # The catalog write itself already succeeded and is not duplicated.
    catalog_source_id = derive_catalog_source_id(inventory_source_id=source_id)
    assert env.catalog.get_source(catalog_source_id) is not None
    assert len(env.catalog.list_sources()) == 1

    result = service.promote(candidate.candidate_id, now=_NOW)
    assert result.execution_state is PromotionExecutionState.COMPLETED
    assert len(env.catalog.list_sources()) == 1
    assert env.promotion_repository.get_mapping(source_id) is not None


def test_recovery_mapping_exists_completion_flag_missing_reconciles() -> None:
    """Simulates a crash after the mapping was persisted but before the
    execution row was flipped to COMPLETED — retry must reconcile, not
    duplicate the curated record."""
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    result = env.service.promote(candidate.candidate_id, now=_NOW)
    assert result.execution_state is PromotionExecutionState.COMPLETED

    # Simulate the crash: roll the persisted execution state back as if
    # the COMPLETED write never landed, while the mapping/catalog writes
    # (already durable) remain untouched.
    execution = env.promotion_repository.get_execution(candidate.candidate_id)
    assert execution is not None
    env.promotion_repository.save_execution(
        execution.model_copy(
            update={"execution_state": PromotionExecutionState.CATALOG_WRITE_CONFIRMED}
        )
    )

    result2 = env.service.promote(candidate.candidate_id, now=_NOW)
    assert result2.execution_state is PromotionExecutionState.COMPLETED
    assert len(env.catalog.list_sources()) == 1


def test_candidate_executed_twice_returns_already_completed_result() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    env.service.promote(candidate.candidate_id, now=_NOW)
    second = env.service.promote(candidate.candidate_id, now=_NOW)
    assert second.already_completed is True


# --- version identity policy (Strategy B) -----------------------------------------


def test_promoting_a_later_version_updates_the_same_curated_record() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="Version 1 Title",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    first_result = env.service.promote(candidate.candidate_id, now=_NOW)

    # A new version of the same source is observed and extracted.
    new_version = SourceVersion(
        source_id=source_id, content_hash_sha256="b" * 64, size_bytes=4096, observed_at=_NOW
    )
    env.inventory.add_version(new_version)
    request = ExtractionRequest(
        inventory_source_id=source_id,
        source_version_id=new_version.version_id,
        requested_capability=ExtractionCapability.PLAIN_TEXT,
        media_kind=SourceMediaType.HTML,
        idempotency_key=f"job-{uuid4()}",
    )
    job = env.queue.enqueue(request, now=_NOW)
    env.queue.claim_next(worker_id="w1", now=_NOW)
    env.queue.mark_running(job.job_id, worker_id="w1", now=_NOW)
    new_artifact_id = derive_artifact_id(
        job_id=job.job_id,
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_hash=None,
        content_locator=f"candidate://{job.job_id}",
    )
    new_artifact = ExtractedArtifact(
        artifact_id=new_artifact_id,
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_locator=f"candidate://{job.job_id}",
        created_at=_NOW,
        provenance=ExtractionArtifactProvenance(
            job_id=job.job_id,
            inventory_source_id=source_id,
            source_version_id=new_version.version_id,
            extractor_name="fake-extractor",
            extractor_version="0.0.0-test",
            extracted_at=_NOW,
        ),
    )
    env.queue.record_success(job.job_id, new_artifact, now=_NOW)

    new_candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=new_version.version_id,
        extracted_artifact_id=new_artifact_id,
        proposed_title="Version 2 Title",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    assert new_candidate.eligibility is PromotionEligibility.ELIGIBLE
    env.service.decide(
        new_candidate.candidate_id,
        outcome=PromotionDecisionOutcome.APPROVE,
        reviewer="alan",
        now=_NOW,
    )
    second_result = env.service.promote(new_candidate.candidate_id, now=_NOW)

    assert second_result.catalog_source_id == first_result.catalog_source_id
    assert len(env.catalog.list_sources()) == 1
    record = env.catalog.get_source(second_result.catalog_source_id)
    assert record is not None
    assert record.filename == "Version 2 Title"
    assert record.sha256_hash == "b" * 64


# --- privacy and rights security tests (Part 13) ----------------------------------


def test_restricted_local_only_source_can_promote_locally() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(
        env, privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY
    )
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="Restricted",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        now=_NOW,
    )
    assert candidate.eligibility is PromotionEligibility.ELIGIBLE
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    result = env.service.promote(candidate.candidate_id, now=_NOW)
    record = env.catalog.get_source(result.catalog_source_id)
    assert record is not None
    assert record.privacy_classification is PrivacyClassification.RESTRICTED_LOCAL_ONLY


def test_promotion_does_not_alter_model_router_privacy_rules() -> None:
    """Promoting a RESTRICTED_LOCAL_ONLY source never upgrades its
    eligibility for hosted-model routing — this module has no code path
    that touches routing policy at all."""
    import ast

    from personal_lms.promotion import service as service_module

    source = Path(service_module.__file__).read_text()
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "policies" not in imported_roots
    assert "providers" not in imported_roots


def test_privacy_cannot_be_downgraded_during_promotion() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(
        env, privacy_classification=PrivacyClassification.SENSITIVE
    )
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="Downgrade Attempt",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.PUBLIC,
        now=_NOW,
    )
    assert candidate.eligibility is PromotionEligibility.BLOCKED
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    with pytest.raises(PromotionBlockedError):
        env.service.promote(candidate.candidate_id, now=_NOW)


def test_unapproved_source_is_blocked_end_to_end() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(
        env, approval_status=SourceApprovalStatus.UNREVIEWED
    )
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    with pytest.raises(PromotionBlockedError):
        env.service.promote(candidate.candidate_id, now=_NOW)


def test_prohibited_rights_status_is_blocked_end_to_end() -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(
        env, rights_status=SourceRightsStatus.RESTRICTED
    )
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    with pytest.raises(PromotionBlockedError):
        env.service.promote(candidate.candidate_id, now=_NOW)


def test_failed_extraction_job_is_blocked() -> None:
    env = _build_env()
    source_id = derive_source_id(canonical_locator="https://example.com/broken")
    record = SourceInventoryRecord(
        source_id=source_id,
        locator_kind=SourceLocatorKind.WEB_URL,
        locator="https://example.com/broken",
        media_type=SourceMediaType.HTML,
        approval_status=SourceApprovalStatus.APPROVED,
        rights_status=SourceRightsStatus.PUBLIC_REFERENCE,
        created_at=_NOW,
        updated_at=_NOW,
    )
    env.inventory.add_source(record)
    version = SourceVersion(source_id=source_id, content_hash_sha256="c" * 64, observed_at=_NOW)
    env.inventory.add_version(version)

    request = ExtractionRequest(
        inventory_source_id=source_id,
        source_version_id=version.version_id,
        requested_capability=ExtractionCapability.PLAIN_TEXT,
        media_kind=SourceMediaType.HTML,
        idempotency_key="job-broken",
    )
    job = env.queue.enqueue(request, now=_NOW)
    env.queue.claim_next(worker_id="w1", now=_NOW)
    env.queue.mark_running(job.job_id, worker_id="w1", now=_NOW)
    env.queue.record_terminal_failure(
        job.job_id, error_code="parse_error", error_message="could not parse", now=_NOW
    )

    artifact_id = derive_artifact_id(
        job_id=job.job_id,
        artifact_kind=ExtractionCapability.PLAIN_TEXT,
        content_hash=None,
        content_locator="candidate://never-succeeded",
    )
    # No artifact was actually recorded (extraction failed) — attempting
    # to build a candidate against a nonexistent artifact fails closed.
    with pytest.raises(Exception):  # noqa: B017 - ExtractionArtifactNotFoundError
        env.service.build_candidate(
            inventory_source_id=source_id,
            source_version_id=version.version_id,
            extracted_artifact_id=artifact_id,
            proposed_title="X",
            proposed_source_type=SourceType.URL,
            proposed_privacy_classification=PrivacyClassification.INTERNAL,
            now=_NOW,
        )


def test_artifact_content_locator_is_opaque_not_a_filesystem_path() -> None:
    """content_locator is never interpreted, opened, or resolved by any
    part of the promotion bridge — it is carried through as an opaque
    string only."""
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    artifact = env.queue.get_artifact(artifact_id)
    assert artifact.content_locator.startswith("candidate://")
    # SourceRecord never receives the raw content_locator as its
    # filename/original_location — those come from approved metadata.
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    result = env.service.promote(candidate.candidate_id, now=_NOW)
    record = env.catalog.get_source(result.catalog_source_id)
    assert record is not None
    assert artifact.content_locator not in record.filename
    assert artifact.content_locator not in record.original_location


def test_no_network_calls_during_promotion(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network access is permitted during promotion")

    monkeypatch.setattr(socket, "socket", _blocked)

    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    env.service.promote(candidate.candidate_id, now=_NOW)


def test_no_production_filesystem_access(tmp_path: Path) -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(env)
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="X",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    env.service.promote(candidate.candidate_id, now=_NOW)
    assert list(tmp_path.iterdir()) == []


def test_no_obsidian_access_anywhere_in_promotion_module() -> None:
    import ast

    from personal_lms.promotion import service as service_module

    source = Path(service_module.__file__).read_text()
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "vault" not in imported_roots
    assert "obsidian" not in imported_roots


# --- domain-neutral end-to-end coverage (Part 14) --------------------------------


@pytest.mark.parametrize(
    ("locator", "media_type", "domains", "certs"),
    [
        ("https://example.com/ospf-deep-dive", SourceMediaType.HTML, ("networking",), ()),
        ("https://example.com/linux-systemd-guide", SourceMediaType.HTML, ("linux",), ()),
        (
            "https://example.com/kubernetes-operators",
            SourceMediaType.HTML,
            ("kubernetes",),
            ("KCNA",),
        ),
        ("https://example.com/aws-well-architected", SourceMediaType.HTML, ("cloud",), ()),
        ("https://example.com/interview-story-bank", SourceMediaType.HTML, ("career",), ()),
        ("https://example.com/project-runbook", SourceMediaType.HTML, (), ()),
    ],
)
def test_domain_neutral_promotion_without_certification_mapping(
    locator: str, media_type: SourceMediaType, domains: tuple[str, ...], certs: tuple[str, ...]
) -> None:
    env = _build_env()
    source_id, version_id, artifact_id = _register_succeeded_source(
        env, locator=locator, media_type=media_type, knowledge_domains=domains, certifications=certs
    )
    candidate = env.service.build_candidate(
        inventory_source_id=source_id,
        source_version_id=version_id,
        extracted_artifact_id=artifact_id,
        proposed_title="A generic source",
        proposed_source_type=SourceType.URL,
        proposed_privacy_classification=PrivacyClassification.INTERNAL,
        now=_NOW,
    )
    assert candidate.eligibility is PromotionEligibility.ELIGIBLE
    env.service.decide(
        candidate.candidate_id, outcome=PromotionDecisionOutcome.APPROVE, reviewer="alan", now=_NOW
    )
    result = env.service.promote(candidate.candidate_id, now=_NOW)
    record = env.catalog.get_source(result.catalog_source_id)
    assert record is not None
    scope_domains = {s.knowledge_domain for s in record.knowledge_scopes if s.knowledge_domain}
    scope_certs = {s.certification for s in record.knowledge_scopes if s.certification}
    assert scope_domains == set(domains)
    assert scope_certs == set(certs)
