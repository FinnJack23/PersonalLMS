"""Validator tests: recompute-never-trust, reference integrity, and grounding.

The central property under test is that a pack cannot pass by asserting
its own quality. Declared coverage and declared grounding scores are both
recomputed, and a disagreement is itself a finding.
"""

from __future__ import annotations

import pytest

from personal_lms.domain.objective_packs import (
    ExposureClass,
    ValidationReasonCode,
    ValidationSeverity,
)
from personal_lms.objective_packs.validation import ObjectivePackValidator

from ._helpers import (
    OTHER_OBJECTIVE_REF,
    make_claim,
    make_item,
    make_pack,
    make_support,
    make_text_region,
)


@pytest.fixture
def validator() -> ObjectivePackValidator:
    return ObjectivePackValidator()


def reason_codes(pack, validator: ObjectivePackValidator) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    return validator.validate(pack).reason_codes


class TestValidPack:
    def test_a_clean_pack_validates(self, validator: ObjectivePackValidator) -> None:
        report = validator.validate(make_pack())

        assert report.is_valid
        assert report.errors == ()

    def test_validation_is_deterministic(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack()

        first = validator.validate(pack)
        second = validator.validate(pack)

        assert first.canonical_pack_hash == second.canonical_pack_hash
        assert first.findings == second.findings

    def test_findings_are_sorted_stably(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(baseline_item_ids=("item-1", "unknown-a", "unknown-b"))

        findings = validator.validate(pack).findings

        assert list(findings) == sorted(findings, key=lambda finding: finding.sort_key)


class TestReferenceIntegrity:
    def test_unknown_item_id_fails_fast(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(baseline_item_ids=("item-1", "item-2", "ghost-item"))

        report = validator.validate(pack)

        assert not report.is_valid
        assert ValidationReasonCode.UNKNOWN_ITEM_ID.value in report.reason_codes

    def test_duplicate_baseline_reference_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(baseline_item_ids=("item-1", "item-1", "item-2"))

        assert ValidationReasonCode.DUPLICATE_ITEM_ID.value in reason_codes(pack, validator)

    def test_an_invented_citation_cannot_resolve(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(
            claims=(make_claim(support=(make_support(evidence_id="ev-does-not-exist"),)),)
        )

        assert ValidationReasonCode.UNRESOLVED_CITATION.value in reason_codes(pack, validator)

    def test_evidence_citing_an_unknown_source_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(regions=(make_text_region(source_id="src-missing"),))

        assert ValidationReasonCode.UNKNOWN_SOURCE_ID.value in reason_codes(pack, validator)

    def test_item_citing_an_unknown_claim_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(
            items=(
                make_item(item_id="item-1", claim_ids=("claim-ghost",)),
                make_item(item_id="item-2"),
                make_item(item_id="item-3"),
                make_item(item_id="item-exit", exposure_class=ExposureClass.EXIT_PROBE),
            )
        )

        assert ValidationReasonCode.UNKNOWN_CLAIM_ID.value in reason_codes(pack, validator)


class TestCardinalityAndExposure:
    def test_baseline_must_hold_exactly_the_policy_count(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(baseline_item_ids=("item-1", "item-2"))

        assert ValidationReasonCode.BASELINE_CARDINALITY.value in reason_codes(pack, validator)

    def test_exposure_sets_must_not_overlap(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(
            baseline_item_ids=("item-1", "item-2", "item-3"),
            exit_probe_item_ids=("item-1",),
        )

        assert ValidationReasonCode.EXPOSURE_SETS_OVERLAP.value in reason_codes(pack, validator)

    def test_an_item_used_outside_its_declared_exposure_class_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(
            items=(
                make_item(item_id="item-1"),
                make_item(item_id="item-2"),
                make_item(item_id="item-3"),
                # Declared baseline, but referenced as the exit probe.
                make_item(item_id="item-exit", exposure_class=ExposureClass.BASELINE),
            )
        )

        assert ValidationReasonCode.EXPOSURE_SETS_OVERLAP.value in reason_codes(pack, validator)


class TestObjectiveVersionConsistency:
    def test_a_claim_from_a_different_blueprint_version_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(claims=(make_claim(objective_ref=OTHER_OBJECTIVE_REF),))

        assert ValidationReasonCode.OBJECTIVE_REF_MISMATCH.value in reason_codes(pack, validator)

    def test_an_item_from_a_different_blueprint_version_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(
            items=(
                make_item(item_id="item-1", objective_ref=OTHER_OBJECTIVE_REF),
                make_item(item_id="item-2"),
                make_item(item_id="item-3"),
                make_item(item_id="item-exit", exposure_class=ExposureClass.EXIT_PROBE),
            )
        )

        assert ValidationReasonCode.OBJECTIVE_REF_MISMATCH.value in reason_codes(pack, validator)


class TestRecomputedCoverage:
    def test_declared_coverage_that_lies_cannot_pass(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(declared_coverage={"baseline_items": 12})

        report = validator.validate(pack)

        assert not report.is_valid
        assert ValidationReasonCode.DECLARED_COVERAGE_MISMATCH.value in report.reason_codes
        assert report.recomputed_coverage["baseline_items"] == 3

    def test_declared_coverage_that_agrees_passes(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(declared_coverage={"baseline_items": 3, "claims": 1})

        assert validator.validate(pack).is_valid

    def test_coverage_for_an_uncomputed_dimension_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(declared_coverage={"invented_dimension": 1})

        assert ValidationReasonCode.DECLARED_COVERAGE_MISMATCH.value in reason_codes(
            pack, validator
        )


class TestClaimGrounding:
    def test_an_unsupported_answer_bearing_claim_scores_zero_and_fails(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(claims=(make_claim(support=()),))

        report = validator.validate(pack)

        assert report.recomputed_claim_scores["claim-1"] == 0
        assert ValidationReasonCode.GROUNDING_BELOW_THRESHOLD.value in report.reason_codes

    def test_a_weakly_supported_answer_bearing_claim_fails_the_threshold(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(claims=(make_claim(support=(make_support(strength=5_000),)),))

        assert ValidationReasonCode.GROUNDING_BELOW_THRESHOLD.value in reason_codes(pack, validator)

    def test_a_claim_no_item_references_may_be_weakly_grounded(
        self, validator: ObjectivePackValidator
    ) -> None:
        """Background context is gated only when an answer key depends on it.

        The claim must be unreferenced by every item — an authored
        ``is_answer_bearing=False`` is not enough, because membership is
        derived from item references.
        """
        pack = make_pack(
            claims=(
                make_claim(claim_id="claim-1"),
                make_claim(
                    claim_id="claim-background",
                    support=(make_support(strength=1_000),),
                    is_answer_bearing=False,
                ),
            )
        )

        report = validator.validate(pack)

        assert "claim-background" not in report.answer_bearing_claim_ids
        assert ValidationReasonCode.GROUNDING_BELOW_THRESHOLD.value not in report.reason_codes

    def test_a_declared_score_that_disagrees_with_recomputation_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(claims=(make_claim(declared_grounding_score_basis_points=1),))

        assert ValidationReasonCode.DECLARED_COVERAGE_MISMATCH.value in reason_codes(
            pack, validator
        )

    def test_a_material_conflict_blocks_a_claim(self, validator: ObjectivePackValidator) -> None:
        """Blocking is its own state, distinct from a low score."""
        pack = make_pack(claims=(make_claim(conflict_status="material"),))

        report = validator.validate(pack)

        assert report.blocked_claim_ids == ("claim-1",)
        assert ValidationReasonCode.CLAIM_BLOCKED_BY_CONFLICT.value in report.reason_codes
        assert ValidationReasonCode.GROUNDING_BELOW_THRESHOLD.value in report.reason_codes


class TestFollowUpRules:
    def test_an_unmapped_misconception_is_an_error(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(
            items=(
                make_item(item_id="item-1", misconception_tags=("orphan-tag",)),
                make_item(item_id="item-2"),
                make_item(item_id="item-3"),
                make_item(item_id="item-exit", exposure_class=ExposureClass.EXIT_PROBE),
            )
        )

        report = validator.validate(pack)
        unmapped = [
            finding
            for finding in report.findings
            if finding.reason_code is ValidationReasonCode.FOLLOWUP_RULE_UNMAPPED
        ]

        assert unmapped
        assert unmapped[0].severity is ValidationSeverity.ERROR
        assert not report.is_valid
