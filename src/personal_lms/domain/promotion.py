"""Promotion domain contracts: the explicit bridge from raw-archive
inventory into the existing, approved curated source catalog.

Pure data shapes only — no filesystem access, extraction, network access,
or Obsidian access happens here. See
``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md`` for the
full design and ``personal_lms.promotion`` for the persistence-neutral
repository protocol, the pure eligibility evaluator, and the bridge
service that actually writes through ``personal_lms.catalog.SourceCatalog``.

Nothing in this module ever constructs a ``domain.catalog.SourceRecord``
directly — that is the promotion bridge service's job (see
``personal_lms.promotion.service.SourcePromotionService``), and it happens
only after an explicit ``PromotionDecisionOutcome.APPROVE`` decision. A
``PromotionCandidate`` existing, or even being marked ``ELIGIBLE``, never
by itself promotes anything.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4, uuid5

from pydantic import AwareDatetime, Field, field_validator, model_validator

from personal_lms.domain.base import StrictModel
from personal_lms.domain.enums import SourceType
from personal_lms.domain.privacy import PrivacyClassification

# Fixed, hardcoded namespace for this module's deterministic identity
# derivations (candidate identity, catalog source identity) — generated
# once (uuid4()) and never changed; see ``derive_promotion_candidate_id``
# and ``derive_catalog_source_id``. Mirrors
# ``domain.source_inventory._SOURCE_INVENTORY_NAMESPACE`` and
# ``domain.extraction._EXTRACTION_ARTIFACT_NAMESPACE``: uuid5 only, never
# uuid4, never a process hash, never the system clock.
_PROMOTION_NAMESPACE = UUID("686bbcf6-fb9d-4bf3-a461-cc72843161f5")

#: The deterministic catalog-identity derivation strategy implemented by
#: ``derive_catalog_source_id`` today. See that function's docstring, and
#: ``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md``'s
#: "Version identity decision" section, for the Strategy A vs. Strategy B
#: tradeoff this constant records.
CURRENT_MAPPING_VERSION = 1

#: Strictness ordering for ``PrivacyClassification`` — higher is more
#: restrictive. Used only to enforce "promotion may never *downgrade*
#: privacy" (a promoted record's classification must be >= the inventory
#: source's); never used to grant hosted-routing eligibility, which
#: remains entirely the model router's own decision
#: (``docs/product-specs/MODEL_ROUTER_AND_COST_CONTROLS.md``).
PRIVACY_STRICTNESS_ORDER: dict[PrivacyClassification, int] = {
    PrivacyClassification.PUBLIC: 0,
    PrivacyClassification.INTERNAL: 1,
    PrivacyClassification.SENSITIVE: 2,
    PrivacyClassification.RESTRICTED_LOCAL_ONLY: 3,
}


class PromotionBlocker(StrEnum):
    """Machine-readable reasons a candidate is not eligible for promotion.

    Structured, never a free-form string — see
    ``personal_lms.promotion.eligibility.evaluate_promotion_eligibility``.
    """

    SOURCE_NOT_APPROVED = "source_not_approved"
    RIGHTS_NOT_CLEARED = "rights_not_cleared"
    EXTRACTION_NOT_SUCCESSFUL = "extraction_not_successful"
    ARTIFACT_SOURCE_MISMATCH = "artifact_source_mismatch"
    ARTIFACT_VERSION_MISMATCH = "artifact_version_mismatch"
    PRIVACY_DOWNGRADE_FORBIDDEN = "privacy_downgrade_forbidden"
    ALREADY_PROMOTED = "already_promoted"
    MISSING_REQUIRED_METADATA = "missing_required_metadata"


class PromotionEligibility(StrEnum):
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"


class PromotionDecisionOutcome(StrEnum):
    """A human (or policy) governance decision. Extraction success is
    never itself an approval outcome — see the module docstring."""

    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"


class PromotionExecutionState(StrEnum):
    """Recoverable promotion-execution progress across the (separate)
    promotion-repository and curated-catalog stores.

    Never claims cross-database ACID atomicity — see
    ``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md``'s
    recovery section for exactly what each state means and how a retry
    reconciles it.
    """

    PENDING = "pending"
    CATALOG_WRITE_STARTED = "catalog_write_started"
    CATALOG_WRITE_CONFIRMED = "catalog_write_confirmed"
    COMPLETED = "completed"
    RECOVERY_REQUIRED = "recovery_required"


class PromotionCandidate(StrictModel):
    """One proposed promotion of an inventory source (at a specific
    version, backed by a specific extracted artifact) into the existing
    curated catalog.

    ``eligibility``/``blockers`` are a snapshot taken at candidate-creation
    time (see ``personal_lms.promotion.eligibility``) — the promotion
    bridge service always re-evaluates eligibility at execution time
    rather than trusting this snapshot (see
    ``personal_lms.promotion.service.SourcePromotionService.promote``).
    """

    candidate_id: UUID
    inventory_source_id: UUID
    source_version_id: UUID
    extracted_artifact_id: UUID
    proposed_catalog_source_id: str = Field(min_length=1)
    proposed_title: str = Field(min_length=1)
    proposed_source_type: SourceType
    proposed_privacy_classification: PrivacyClassification
    proposed_metadata: dict[str, Any] = Field(default_factory=dict)
    eligibility: PromotionEligibility
    blockers: tuple[PromotionBlocker, ...] = Field(default_factory=tuple)
    created_at: AwareDatetime

    @field_validator("proposed_metadata")
    @classmethod
    def _metadata_is_json_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("proposed_metadata must contain only JSON-safe values") from exc
        return value

    @model_validator(mode="after")
    def _blockers_consistent_with_eligibility(self) -> Self:
        if self.eligibility is PromotionEligibility.ELIGIBLE and self.blockers:
            raise ValueError("an eligible candidate must not carry blockers")
        if self.eligibility is PromotionEligibility.BLOCKED and not self.blockers:
            raise ValueError("a blocked candidate must carry at least one blocker")
        return self


class PromotionDecision(StrictModel):
    """One immutable governance decision for a ``PromotionCandidate``.

    Never mutated after creation — a changed decision is always a new
    ``PromotionDecision`` row with a later ``created_at`` (see
    ``personal_lms.promotion.protocol.PromotionRepository.record_decision``,
    which only ever appends).
    """

    decision_id: UUID = Field(default_factory=uuid4)
    candidate_id: UUID
    outcome: PromotionDecisionOutcome
    reviewer: str = Field(
        min_length=1, description="A stable actor identifier, not necessarily a human name."
    )
    reason: str | None = Field(default=None, min_length=1)
    created_at: AwareDatetime


class PromotionMapping(StrictModel):
    """The deterministic, recorded identity bridge from one inventory
    source to its curated ``SourceRecord.source_id``.

    ``mapping_version`` records which deterministic-derivation strategy
    (see ``CURRENT_MAPPING_VERSION``) produced ``catalog_source_id`` — not
    the promoted source's own content version. Under the Strategy B
    identity policy this module implements (see
    ``derive_catalog_source_id``), one inventory source has at most one
    ``PromotionMapping`` row regardless of how many versions have been
    promoted; ``source_version_id`` records the most recently promoted
    version for audit purposes only.
    """

    inventory_source_id: UUID
    source_version_id: UUID
    catalog_source_id: str = Field(min_length=1)
    mapping_version: int = Field(ge=1)
    created_at: AwareDatetime


class PromotionExecution(StrictModel):
    """Recoverable per-candidate execution progress. One row per
    candidate — a candidate is promoted at most once successfully; see
    ``PromotionExecutionState``."""

    candidate_id: UUID
    decision_id: UUID
    catalog_source_id: str = Field(min_length=1)
    execution_state: PromotionExecutionState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PromotionEvent(StrictModel):
    """One append-only audit-log entry for a candidate's promotion
    lifecycle. Never updated or deleted once recorded."""

    event_id: UUID = Field(default_factory=uuid4)
    candidate_id: UUID
    event_type: str = Field(min_length=1)
    occurred_at: AwareDatetime
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("detail")
    @classmethod
    def _detail_is_json_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("detail must contain only JSON-safe values") from exc
        return value


class PromotionResult(StrictModel):
    """The outcome of one ``SourcePromotionService.promote()`` call.

    ``already_completed=True`` signals that this call did not perform a
    new catalog write — a prior call had already completed the same
    candidate's promotion, and this result reconciles/echoes that outcome
    rather than duplicating it (see
    ``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md``'s
    recovery section, and never described as a distributed transaction).
    """

    candidate_id: UUID
    decision_id: UUID
    catalog_source_id: str = Field(min_length=1)
    execution_state: PromotionExecutionState
    already_completed: bool = False
    created_at: AwareDatetime


def derive_promotion_candidate_id(
    *, inventory_source_id: UUID, source_version_id: UUID, extracted_artifact_id: UUID
) -> UUID:
    """Deterministic candidate identity — never ``uuid4()``, never random.

    Creating a candidate again for the same
    ``(inventory_source_id, source_version_id, extracted_artifact_id)``
    triple must produce the same ``candidate_id``, so repository-level
    candidate creation can be idempotent (see
    ``personal_lms.promotion.protocol.PromotionRepository.create_candidate``).
    """
    return uuid5(
        _PROMOTION_NAMESPACE,
        f"candidate:{inventory_source_id}:{source_version_id}:{extracted_artifact_id}",
    )


def derive_catalog_source_id(*, inventory_source_id: UUID) -> str:
    """Deterministic curated ``SourceRecord.source_id`` for an inventory
    source — never ``uuid4()``, never random, never based on
    ``source_version_id``.

    Implements **Strategy B** (see
    ``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md``'s
    "Version identity decision"): one inventory source maps to one stable
    curated source identity across all of its versions. Promoting a later
    version of the same inventory source updates the *same*
    ``SourceRecord`` row via ``SourceCatalog.upsert_source``'s existing
    insert-or-replace semantics, rather than creating a second curated
    record that would need an explicit ``supersedes`` relationship. This
    is the least-disruptive strategy compatible with the existing
    ``SourceCatalog`` contract, which this module never modifies.
    """
    return str(uuid5(_PROMOTION_NAMESPACE, f"catalog_source:{inventory_source_id}"))


__all__ = [
    "CURRENT_MAPPING_VERSION",
    "PRIVACY_STRICTNESS_ORDER",
    "PromotionBlocker",
    "PromotionCandidate",
    "PromotionDecision",
    "PromotionDecisionOutcome",
    "PromotionEligibility",
    "PromotionEvent",
    "PromotionExecution",
    "PromotionExecutionState",
    "PromotionMapping",
    "PromotionResult",
    "derive_catalog_source_id",
    "derive_promotion_candidate_id",
]
