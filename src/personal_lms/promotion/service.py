"""``SourcePromotionService``: the explicit, human-gated bridge from a
raw-archive ``SourceInventoryRecord`` into the existing, unmodified
curated ``personal_lms.catalog.SourceCatalog``.

No extraction completion, candidate creation, or approved decision ever
promotes anything by itself — ``promote()`` is always a separate,
explicit call (see Rule 3 in
``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md``).

Recovery model: every step ``promote()`` takes toward writing the curated
catalog is independently idempotent (``SourceCatalog.upsert_source`` is
insert-or-replace; ``derive_catalog_source_id`` is a pure deterministic
function of ``inventory_source_id`` alone; ``PromotionRepository.create_mapping``
is idempotent-or-reconciling). This means recovering from a partial
failure never requires bespoke reconciliation branches — a retried
``promote()`` call simply re-derives the same identity and re-runs the
same idempotent steps starting over, converging on
``PromotionExecutionState.COMPLETED`` without ever creating a second
curated record for the same inventory source. This is an **idempotent,
recoverable workflow across independently committed SQLite connections**
— never a distributed transaction, and never claimed as one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from personal_lms.catalog.protocol import SourceCatalog
from personal_lms.domain.catalog import ProvenanceMetadata, SourceRecord
from personal_lms.domain.enums import SourceProcessingStatus, SourceType
from personal_lms.domain.extraction import ExtractedArtifact
from personal_lms.domain.knowledge_scope import KnowledgeScope
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.promotion import (
    CURRENT_MAPPING_VERSION,
    PromotionCandidate,
    PromotionDecision,
    PromotionDecisionOutcome,
    PromotionEligibility,
    PromotionEvent,
    PromotionExecution,
    PromotionExecutionState,
    PromotionMapping,
    PromotionResult,
    derive_catalog_source_id,
    derive_promotion_candidate_id,
)
from personal_lms.domain.source_inventory import SourceInventoryRecord, SourceVersion
from personal_lms.extraction.protocol import ExtractionQueue
from personal_lms.promotion.eligibility import evaluate_promotion_eligibility
from personal_lms.promotion.errors import (
    PromotionBlockedError,
    PromotionDecisionRequiredError,
    PromotionSourceVersionNotFoundError,
)
from personal_lms.promotion.protocol import PromotionRepository
from personal_lms.source_inventory.protocol import SourceInventoryCatalog


class SourcePromotionService:
    """Application service coordinating four independently persisted
    stores (inventory, extraction queue, promotion repository, curated
    catalog) into one explicit, auditable promotion workflow.

    No ``Clock``/time-provider abstraction exists yet in this codebase
    (see ``personal_lms.extraction.protocol``'s identical note) — every
    method here accepts an explicit ``now`` instead.
    """

    def __init__(
        self,
        *,
        inventory: SourceInventoryCatalog,
        extraction_queue: ExtractionQueue,
        promotion_repository: PromotionRepository,
        catalog: SourceCatalog,
    ) -> None:
        self._inventory = inventory
        self._extraction_queue = extraction_queue
        self._promotion_repository = promotion_repository
        self._catalog = catalog

    def build_candidate(
        self,
        *,
        inventory_source_id: UUID,
        source_version_id: UUID,
        extracted_artifact_id: UUID,
        proposed_title: str,
        proposed_source_type: SourceType,
        proposed_privacy_classification: PrivacyClassification,
        proposed_metadata: dict[str, Any] | None = None,
        now: datetime,
    ) -> PromotionCandidate:
        """Load the referenced inventory/version/artifact/job records,
        evaluate eligibility, and persist a new (or return the existing,
        idempotently-identical) ``PromotionCandidate``.

        Never itself promotes anything — see the module docstring.
        """
        inventory_source = self._inventory.get_source(inventory_source_id)
        source_version = self._get_source_version(inventory_source_id, source_version_id)
        artifact = self._extraction_queue.get_artifact(extracted_artifact_id)
        job = self._extraction_queue.get(artifact.provenance.job_id)

        already_promoted = self._promotion_repository.has_completed_promotion_for_source_version(
            inventory_source_id, source_version_id
        )
        eligibility, blockers = evaluate_promotion_eligibility(
            inventory_source=inventory_source,
            source_version=source_version,
            extraction_job=job,
            artifact=artifact,
            proposed_privacy_classification=proposed_privacy_classification,
            proposed_title=proposed_title,
            already_promoted=already_promoted,
        )

        candidate = PromotionCandidate(
            candidate_id=derive_promotion_candidate_id(
                inventory_source_id=inventory_source_id,
                source_version_id=source_version_id,
                extracted_artifact_id=extracted_artifact_id,
            ),
            inventory_source_id=inventory_source_id,
            source_version_id=source_version_id,
            extracted_artifact_id=extracted_artifact_id,
            proposed_catalog_source_id=derive_catalog_source_id(
                inventory_source_id=inventory_source_id
            ),
            proposed_title=proposed_title,
            proposed_source_type=proposed_source_type,
            proposed_privacy_classification=proposed_privacy_classification,
            proposed_metadata=proposed_metadata or {},
            eligibility=eligibility,
            blockers=blockers,
            created_at=now,
        )
        stored = self._promotion_repository.create_candidate(candidate)
        self._record_event(
            stored.candidate_id, "candidate_created", now, {"eligibility": eligibility.value}
        )
        return stored

    def decide(
        self,
        candidate_id: UUID,
        *,
        outcome: PromotionDecisionOutcome,
        reviewer: str,
        reason: str | None = None,
        now: datetime,
    ) -> PromotionDecision:
        """Record an explicit, immutable governance decision.

        Recording ``APPROVE`` never itself executes promotion — the
        caller must separately call ``promote()`` (see the module
        docstring's Rule 3 reference).
        """
        self._promotion_repository.get_candidate(candidate_id)  # raises if unknown
        decision = PromotionDecision(
            candidate_id=candidate_id,
            outcome=outcome,
            reviewer=reviewer,
            reason=reason,
            created_at=now,
        )
        stored = self._promotion_repository.record_decision(decision)
        self._record_event(candidate_id, "decision_recorded", now, {"outcome": outcome.value})
        return stored

    def promote(self, candidate_id: UUID, *, now: datetime) -> PromotionResult:
        """Execute the full promotion workflow described in the module
        docstring's recovery model. Raises ``PromotionDecisionRequiredError``
        if no approved decision exists, or ``PromotionBlockedError`` if
        eligibility, re-evaluated at execution time, fails."""
        candidate = self._promotion_repository.get_candidate(candidate_id)

        existing_execution = self._promotion_repository.get_execution(candidate_id)
        if (
            existing_execution is not None
            and existing_execution.execution_state is PromotionExecutionState.COMPLETED
        ):
            return PromotionResult(
                candidate_id=candidate_id,
                decision_id=existing_execution.decision_id,
                catalog_source_id=existing_execution.catalog_source_id,
                execution_state=PromotionExecutionState.COMPLETED,
                already_completed=True,
                created_at=now,
            )

        decision = self._promotion_repository.get_latest_decision(candidate_id)
        if decision is None or decision.outcome is not PromotionDecisionOutcome.APPROVE:
            raise PromotionDecisionRequiredError(candidate_id)

        inventory_source = self._inventory.get_source(candidate.inventory_source_id)
        source_version = self._get_source_version(
            candidate.inventory_source_id, candidate.source_version_id
        )
        artifact = self._extraction_queue.get_artifact(candidate.extracted_artifact_id)
        job = self._extraction_queue.get(artifact.provenance.job_id)

        already_promoted = self._promotion_repository.has_completed_promotion_for_source_version(
            candidate.inventory_source_id,
            candidate.source_version_id,
            exclude_candidate_id=candidate_id,
        )
        eligibility, blockers = evaluate_promotion_eligibility(
            inventory_source=inventory_source,
            source_version=source_version,
            extraction_job=job,
            artifact=artifact,
            proposed_privacy_classification=candidate.proposed_privacy_classification,
            proposed_title=candidate.proposed_title,
            already_promoted=already_promoted,
        )
        if eligibility is not PromotionEligibility.ELIGIBLE:
            raise PromotionBlockedError(candidate_id, blockers)

        catalog_source_id = derive_catalog_source_id(
            inventory_source_id=candidate.inventory_source_id
        )
        execution = PromotionExecution(
            candidate_id=candidate_id,
            decision_id=decision.decision_id,
            catalog_source_id=catalog_source_id,
            execution_state=PromotionExecutionState.CATALOG_WRITE_STARTED,
            created_at=existing_execution.created_at if existing_execution else now,
            updated_at=now,
        )
        execution = self._promotion_repository.save_execution(execution)
        self._record_event(candidate_id, "catalog_write_started", now, {})

        record = self._build_source_record(
            catalog_source_id=catalog_source_id,
            inventory_source=inventory_source,
            source_version=source_version,
            artifact=artifact,
            candidate=candidate,
            now=now,
        )
        try:
            self._catalog.upsert_source(record)
        except Exception:
            self._enter_recovery(execution, now=now, stage="catalog_write")
            raise

        execution = execution.model_copy(
            update={
                "execution_state": PromotionExecutionState.CATALOG_WRITE_CONFIRMED,
                "updated_at": now,
            }
        )
        execution = self._promotion_repository.save_execution(execution)
        self._record_event(
            candidate_id, "catalog_write_confirmed", now, {"catalog_source_id": catalog_source_id}
        )

        mapping = PromotionMapping(
            inventory_source_id=candidate.inventory_source_id,
            source_version_id=candidate.source_version_id,
            catalog_source_id=catalog_source_id,
            mapping_version=CURRENT_MAPPING_VERSION,
            created_at=now,
        )
        try:
            self._promotion_repository.create_mapping(mapping)
        except Exception:
            self._enter_recovery(execution, now=now, stage="mapping")
            raise

        execution = execution.model_copy(
            update={"execution_state": PromotionExecutionState.COMPLETED, "updated_at": now}
        )
        self._promotion_repository.save_execution(execution)
        self._record_event(candidate_id, "completed", now, {"catalog_source_id": catalog_source_id})

        return PromotionResult(
            candidate_id=candidate_id,
            decision_id=decision.decision_id,
            catalog_source_id=catalog_source_id,
            execution_state=PromotionExecutionState.COMPLETED,
            already_completed=False,
            created_at=now,
        )

    # --- internal helpers ----------------------------------------------------

    def _enter_recovery(self, execution: PromotionExecution, *, now: datetime, stage: str) -> None:
        recovering = execution.model_copy(
            update={"execution_state": PromotionExecutionState.RECOVERY_REQUIRED, "updated_at": now}
        )
        self._promotion_repository.save_execution(recovering)
        self._record_event(execution.candidate_id, "recovery_required", now, {"stage": stage})

    def _record_event(
        self, candidate_id: UUID, event_type: str, occurred_at: datetime, detail: dict[str, Any]
    ) -> None:
        self._promotion_repository.record_event(
            PromotionEvent(
                candidate_id=candidate_id,
                event_type=event_type,
                occurred_at=occurred_at,
                detail=detail,
            )
        )

    def _get_source_version(
        self, inventory_source_id: UUID, source_version_id: UUID
    ) -> SourceVersion:
        for version in self._inventory.list_versions(inventory_source_id):
            if version.version_id == source_version_id:
                return version
        raise PromotionSourceVersionNotFoundError(inventory_source_id, source_version_id)

    def _build_source_record(
        self,
        *,
        catalog_source_id: str,
        inventory_source: SourceInventoryRecord,
        source_version: SourceVersion,
        artifact: ExtractedArtifact,
        candidate: PromotionCandidate,
        now: datetime,
    ) -> SourceRecord:
        """Build a curated ``SourceRecord`` from only reviewed and
        approved metadata — never extraction warnings, raw internal
        locators beyond the source's own already-approved
        ``original_location``, or credentials. See the module docstring
        and Part 12 of
        ``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md``."""
        knowledge_scopes: list[KnowledgeScope] = [
            *(KnowledgeScope(knowledge_domain=d) for d in inventory_source.knowledge_domains),
            *(KnowledgeScope(certification=c) for c in inventory_source.certifications),
            *(KnowledgeScope(course=c) for c in inventory_source.courses),
            *(KnowledgeScope(topic=t) for t in inventory_source.topics),
        ]

        mime_type = artifact.mime_type or inventory_source.mime_type or "application/octet-stream"
        byte_size = source_version.size_bytes
        if byte_size is None:
            byte_size = artifact.content_size_bytes
        if byte_size is None:
            byte_size = inventory_source.size_bytes
        if byte_size is None:
            byte_size = 0

        return SourceRecord(
            source_id=catalog_source_id,
            source_type=candidate.proposed_source_type,
            original_location=inventory_source.canonical_locator,
            filename=candidate.proposed_title,
            mime_type=mime_type,
            sha256_hash=source_version.content_hash_sha256,
            byte_size=byte_size,
            privacy_classification=candidate.proposed_privacy_classification,
            status=SourceProcessingStatus.APPROVED,
            is_generated_artifact=False,
            knowledge_scopes=knowledge_scopes,
            provenance=ProvenanceMetadata(
                imported_at=now,
                imported_by="source_promotion_service",
                acquisition_note=(
                    f"Promoted from inventory source {inventory_source.source_id} "
                    f"(version {source_version.version_id}) via extraction job "
                    f"{artifact.provenance.job_id}, artifact {artifact.artifact_id}."
                ),
            ),
        )


__all__ = ["SourcePromotionService"]
