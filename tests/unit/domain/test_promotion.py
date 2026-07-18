from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import PrivacyClassification, SourceType
from personal_lms.domain.promotion import (
    PRIVACY_STRICTNESS_ORDER,
    PromotionBlocker,
    PromotionCandidate,
    PromotionDecision,
    PromotionDecisionOutcome,
    PromotionEligibility,
    PromotionEvent,
    derive_catalog_source_id,
    derive_promotion_candidate_id,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _candidate(**overrides: object) -> PromotionCandidate:
    defaults: dict[str, object] = {
        "candidate_id": uuid4(),
        "inventory_source_id": uuid4(),
        "source_version_id": uuid4(),
        "extracted_artifact_id": uuid4(),
        "proposed_catalog_source_id": "cat-1",
        "proposed_title": "Some Source",
        "proposed_source_type": SourceType.DOCUMENT,
        "proposed_privacy_classification": PrivacyClassification.INTERNAL,
        "eligibility": PromotionEligibility.ELIGIBLE,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return PromotionCandidate.model_validate(defaults)


# --- candidate validation --------------------------------------------------------


def test_eligible_candidate_cannot_carry_blockers() -> None:
    with pytest.raises(ValidationError):
        _candidate(
            eligibility=PromotionEligibility.ELIGIBLE,
            blockers=(PromotionBlocker.SOURCE_NOT_APPROVED,),
        )


def test_blocked_candidate_must_carry_a_blocker() -> None:
    with pytest.raises(ValidationError):
        _candidate(eligibility=PromotionEligibility.BLOCKED, blockers=())


def test_blocked_candidate_with_blocker_is_valid() -> None:
    candidate = _candidate(
        eligibility=PromotionEligibility.BLOCKED,
        blockers=(PromotionBlocker.SOURCE_NOT_APPROVED,),
    )
    assert candidate.blockers == (PromotionBlocker.SOURCE_NOT_APPROVED,)


def test_candidate_metadata_must_be_json_safe() -> None:
    with pytest.raises(ValidationError):
        _candidate(proposed_metadata={"bad": {1, 2, 3}})


def test_candidate_empty_title_rejected() -> None:
    with pytest.raises(ValidationError):
        _candidate(proposed_title="")


# --- decisions -----------------------------------------------------------------


def test_decision_requires_nonempty_reviewer() -> None:
    with pytest.raises(ValidationError):
        PromotionDecision(
            candidate_id=uuid4(),
            outcome=PromotionDecisionOutcome.APPROVE,
            reviewer="",
            created_at=_NOW,
        )


def test_decision_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        PromotionDecision.model_validate(
            {
                "candidate_id": uuid4(),
                "outcome": "approve",
                "reviewer": "alan",
                "created_at": _NOW,
                "unexpected": True,
            }
        )


def test_decision_outcomes_are_explicit_never_inferred_from_extraction() -> None:
    """Extraction success is never itself an approval outcome — the only
    valid outcomes are the three explicit governance decisions."""
    assert {o.value for o in PromotionDecisionOutcome} == {"approve", "reject", "defer"}


# --- events ------------------------------------------------------------------------


def test_event_detail_must_be_json_safe() -> None:
    with pytest.raises(ValidationError):
        PromotionEvent(
            candidate_id=uuid4(),
            event_type="candidate_created",
            occurred_at=_NOW,
            detail={"bad": {1, 2, 3}},
        )


# --- privacy strictness ordering -------------------------------------------------


def test_privacy_strictness_order_covers_every_classification() -> None:
    assert set(PRIVACY_STRICTNESS_ORDER) == set(PrivacyClassification)


def test_privacy_strictness_is_monotonic_public_to_restricted() -> None:
    ordered = [
        PrivacyClassification.PUBLIC,
        PrivacyClassification.INTERNAL,
        PrivacyClassification.SENSITIVE,
        PrivacyClassification.RESTRICTED_LOCAL_ONLY,
    ]
    values = [PRIVACY_STRICTNESS_ORDER[c] for c in ordered]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


# --- deterministic identity -------------------------------------------------------


def test_same_triple_produces_same_candidate_id() -> None:
    inventory_source_id, source_version_id, artifact_id = uuid4(), uuid4(), uuid4()
    first = derive_promotion_candidate_id(
        inventory_source_id=inventory_source_id,
        source_version_id=source_version_id,
        extracted_artifact_id=artifact_id,
    )
    second = derive_promotion_candidate_id(
        inventory_source_id=inventory_source_id,
        source_version_id=source_version_id,
        extracted_artifact_id=artifact_id,
    )
    assert first == second


def test_different_artifact_produces_different_candidate_id() -> None:
    inventory_source_id, source_version_id = uuid4(), uuid4()
    first = derive_promotion_candidate_id(
        inventory_source_id=inventory_source_id,
        source_version_id=source_version_id,
        extracted_artifact_id=uuid4(),
    )
    second = derive_promotion_candidate_id(
        inventory_source_id=inventory_source_id,
        source_version_id=source_version_id,
        extracted_artifact_id=uuid4(),
    )
    assert first != second


def test_same_inventory_source_produces_same_catalog_source_id() -> None:
    inventory_source_id = uuid4()
    first = derive_catalog_source_id(inventory_source_id=inventory_source_id)
    second = derive_catalog_source_id(inventory_source_id=inventory_source_id)
    assert first == second


def test_catalog_source_id_independent_of_source_version() -> None:
    """Strategy B: catalog identity depends only on the inventory source,
    never on which version is being promoted."""
    inventory_source_id = uuid4()
    catalog_id = derive_catalog_source_id(inventory_source_id=inventory_source_id)
    # Deliberately no source_version_id parameter exists at all — this
    # test documents that as the contract, not merely an implementation
    # detail.
    assert isinstance(catalog_id, str)
    assert catalog_id == derive_catalog_source_id(inventory_source_id=inventory_source_id)


def test_different_inventory_sources_produce_different_catalog_ids() -> None:
    first = derive_catalog_source_id(inventory_source_id=uuid4())
    second = derive_catalog_source_id(inventory_source_id=uuid4())
    assert first != second


def test_catalog_source_id_no_random_source() -> None:
    inventory_source_id = uuid4()
    results = {derive_catalog_source_id(inventory_source_id=inventory_source_id) for _ in range(25)}
    assert len(results) == 1
