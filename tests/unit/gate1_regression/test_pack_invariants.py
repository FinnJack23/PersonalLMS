"""Missing gate-critical pack invariants (review findings #15–#17, #14).

The first validator recomputed coverage and references but silently
accepted several conditions that make a pack unusable as a gate subject:
duplicate source identities, a wrong exit-probe count, uncovered required
facets, unresolved required claims, invalid facet weights, and evidence
whose objective scope disagrees with its own artifact.
"""

from __future__ import annotations

import pytest

from personal_lms.domain.objective_packs import (
    ExposureClass,
    ObjectiveFacet,
    ReviewState,
    ValidationReasonCode,
)
from personal_lms.objective_packs.validation import ObjectivePackValidator

from ..objective_packs._helpers import (
    OBJECTIVE_REF,
    OTHER_OBJECTIVE_REF,
    make_claim,
    make_item,
    make_mastery_policy,
    make_pack,
    make_source,
    make_text_region,
)


@pytest.fixture
def validator() -> ObjectivePackValidator:
    return ObjectivePackValidator()


def codes(pack, validator: ObjectivePackValidator) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    return validator.validate(pack).reason_codes


class TestDuplicateSourceIdentity:
    def test_duplicate_source_ids_fail_with_a_stable_reason_code(
        self, validator: ObjectivePackValidator
    ) -> None:
        """Two artifacts sharing an ID makes every region's provenance ambiguous."""
        pack = make_pack(sources=(make_source(), make_source(payload=b"%PDF-1.7\ndifferent\n")))

        assert ValidationReasonCode.DUPLICATE_SOURCE_ID.value in codes(pack, validator)


class TestExitProbeCardinality:
    def test_a_wrong_exit_probe_count_is_reported(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(exit_probe_item_ids=())

        assert ValidationReasonCode.EXIT_PROBE_CARDINALITY.value in codes(pack, validator)

    def test_too_many_exit_probes_are_reported(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(
            items=(
                make_item(item_id="item-1"),
                make_item(item_id="item-2"),
                make_item(item_id="item-3"),
                make_item(item_id="item-exit", exposure_class=ExposureClass.EXIT_PROBE),
                make_item(item_id="item-exit-2", exposure_class=ExposureClass.EXIT_PROBE),
            ),
            exit_probe_item_ids=("item-exit", "item-exit-2"),
        )

        assert ValidationReasonCode.EXIT_PROBE_CARDINALITY.value in codes(pack, validator)


class TestRequiredFacetCoverage:
    def test_an_uncovered_required_facet_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(
            mastery_policy=make_mastery_policy(
                required_facets=frozenset(
                    {ObjectiveFacet.CONCEPT, ObjectiveFacet.CLI_CONFIGURATION}
                )
            )
        )

        assert ValidationReasonCode.REQUIRED_FACET_UNCOVERED.value in codes(pack, validator)

    def test_a_covered_required_facet_passes(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(
            mastery_policy=make_mastery_policy(required_facets=frozenset({ObjectiveFacet.CONCEPT}))
        )

        assert ValidationReasonCode.REQUIRED_FACET_UNCOVERED.value not in codes(pack, validator)


class TestFacetWeightValidity:
    def test_an_item_with_no_facet_weights_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(
            items=(
                make_item(item_id="item-1", facet_weights={}),
                make_item(item_id="item-2"),
                make_item(item_id="item-3"),
                make_item(item_id="item-exit", exposure_class=ExposureClass.EXIT_PROBE),
            )
        )

        assert ValidationReasonCode.FACET_WEIGHTS_INVALID.value in codes(pack, validator)

    def test_facet_weights_that_do_not_total_ten_thousand_are_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(
            items=(
                make_item(
                    item_id="item-1",
                    facet_weights={
                        ObjectiveFacet.CONCEPT: 5_000,
                        ObjectiveFacet.NOVEL_TRANSFER: 3_000,
                    },
                ),
                make_item(item_id="item-2"),
                make_item(item_id="item-3"),
                make_item(item_id="item-exit", exposure_class=ExposureClass.EXIT_PROBE),
            )
        )

        assert ValidationReasonCode.FACET_WEIGHTS_INVALID.value in codes(pack, validator)


class TestRequiredClaimResolution:
    def test_an_unresolved_required_claim_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(required_claim_ids=("claim-1", "claim-missing"))

        assert ValidationReasonCode.REQUIRED_CLAIM_UNRESOLVED.value in codes(pack, validator)


class TestPendingRecordsCannotValidateAsReady:
    def test_a_pending_required_claim_is_reported(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(claims=(make_claim(review_state=ReviewState.PENDING),))

        assert ValidationReasonCode.RECORD_NOT_REVIEWED.value in codes(pack, validator)

    def test_a_pending_referenced_item_is_reported(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(
            items=(
                make_item(item_id="item-1", review_state=ReviewState.PENDING),
                make_item(item_id="item-2"),
                make_item(item_id="item-3"),
                make_item(item_id="item-exit", exposure_class=ExposureClass.EXIT_PROBE),
            )
        )

        assert ValidationReasonCode.RECORD_NOT_REVIEWED.value in codes(pack, validator)

    def test_a_rejected_item_is_reported(self, validator: ObjectivePackValidator) -> None:
        pack = make_pack(
            items=(
                make_item(item_id="item-1", review_state=ReviewState.REJECTED),
                make_item(item_id="item-2"),
                make_item(item_id="item-3"),
                make_item(item_id="item-exit", exposure_class=ExposureClass.EXIT_PROBE),
            )
        )

        assert ValidationReasonCode.RECORD_NOT_REVIEWED.value in codes(pack, validator)


class TestObjectiveScopeAgreement:
    def test_a_region_scoped_to_another_objective_version_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(regions=(make_text_region(objective_refs=(OTHER_OBJECTIVE_REF,)),))

        assert ValidationReasonCode.OBJECTIVE_REF_MISMATCH.value in codes(pack, validator)

    def test_an_artifact_not_current_for_the_objective_is_reported(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(sources=(make_source(current_for_objective_refs=(OTHER_OBJECTIVE_REF,)),))

        assert ValidationReasonCode.OBJECTIVE_REF_MISMATCH.value in codes(pack, validator)

    def test_an_artifact_current_for_the_objective_passes(
        self, validator: ObjectivePackValidator
    ) -> None:
        pack = make_pack(sources=(make_source(current_for_objective_refs=(OBJECTIVE_REF,)),))

        assert ValidationReasonCode.OBJECTIVE_REF_MISMATCH.value not in codes(pack, validator)


class TestUnmappedFollowUpsAreErrors:
    def test_an_unmapped_misconception_is_an_error_not_a_warning(
        self, validator: ObjectivePackValidator
    ) -> None:
        """A detected gap with no deterministic remediation is a real defect."""
        pack = make_pack(
            items=(
                make_item(item_id="item-1", misconception_tags=("orphan-tag",)),
                make_item(item_id="item-2"),
                make_item(item_id="item-3"),
                make_item(item_id="item-exit", exposure_class=ExposureClass.EXIT_PROBE),
            )
        )

        report = validator.validate(pack)

        assert ValidationReasonCode.FOLLOWUP_RULE_UNMAPPED.value in report.reason_codes
        assert not report.is_valid


class TestGenericSchemaStaysDomainNeutral:
    def test_no_ccna_specific_identifier_appears_in_the_generic_schema(self) -> None:
        """Exact claim and item counts belong to a reviewed gate definition.

        Checks *code* rather than prose: the module docstring legitimately
        names CCNA when explaining that a CCNA pack and an A+ pack are the
        same schema with different data. Docstrings are stripped before
        the check so that explanation cannot trip it.
        """
        import ast
        from pathlib import Path

        tree = ast.parse(
            Path("src/personal_lms/domain/objective_packs.py").read_text(encoding="utf-8")
        )
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and ast.get_docstring(node) is not None
            ):
                node.body = node.body[1:]

        code = ast.unparse(tree).lower()
        for forbidden in ("ccna", "200-301", "cisco", "vlan", "trunk"):
            assert forbidden not in code, f"{forbidden!r} leaked into the generic schema"

    def test_the_generic_schema_hardcodes_no_domain_specific_counts(self) -> None:
        """12 baseline items and 6 claims are fixture facts, not schema facts."""
        from personal_lms.domain.objective_packs import MasteryPolicy

        fields = MasteryPolicy.model_fields

        assert (
            fields["baseline_item_count"].default is None
            or isinstance(fields["baseline_item_count"].default, type(None))
            or fields["baseline_item_count"].is_required()
        )
