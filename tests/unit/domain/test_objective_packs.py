"""Objective Pack domain-contract tests.

Concentrates on the cross-field validators — the rules that stop an
internally inconsistent record from existing at all, so no downstream
service has to defend against it.
"""

from __future__ import annotations

import pytest

from personal_lms.domain.objective_packs import (
    ImageRegionSelector,
    ObjectiveRef,
    PageTextSelector,
    PermittedUse,
    QuarantineStatus,
    ReviewState,
    TrustStatus,
    ValidationFinding,
    ValidationReasonCode,
    validate_objective_ref,
)
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import SourceRightsStatus

from ..objective_packs._helpers import (
    OBJECTIVE_REF,
    PNG_BYTES,
    make_image_region,
    make_source,
    make_text_region,
    sha256_of,
    text_hash,
)


class TestObjectiveRef:
    def test_a_canonical_reference_round_trips(self) -> None:
        parsed = ObjectiveRef.parse(OBJECTIVE_REF)

        assert parsed.exam_code == "synthetic-exam"
        assert parsed.blueprint_version == "1.1"
        assert parsed.number == "2.2"
        assert parsed.value == OBJECTIVE_REF

    @pytest.mark.parametrize(
        "candidate",
        [
            "no-version:2.2",
            "synthetic-exam-v1.1",
            "synthetic-exam-v1.1:",
            "SYNTHETIC-EXAM-v1.1:2.2",
            "",
            "  ",
        ],
    )
    def test_a_malformed_reference_is_refused(self, candidate: str) -> None:
        with pytest.raises(ValueError, match="objective_ref"):
            validate_objective_ref(candidate)

    def test_two_blueprint_versions_are_different_references(self) -> None:
        first = ObjectiveRef.parse("synthetic-exam-v1.1:2.2")
        second = ObjectiveRef.parse("synthetic-exam-v2.0:2.2")

        assert first.value != second.value
        assert first.number == second.number


class TestSourceArtifactRef:
    def test_restricted_rights_may_not_carry_a_permitted_use(self) -> None:
        with pytest.raises(ValueError, match="restricted material grants no use"):
            make_source(
                rights_status=SourceRightsStatus.RESTRICTED,
                permitted_uses=frozenset({PermittedUse.LOCAL_TEACH}),
            )

    def test_a_quarantined_artifact_may_not_be_trusted(self) -> None:
        with pytest.raises(ValueError, match="quarantined artifact"):
            make_source(
                quarantine_status=QuarantineStatus.QUARANTINED,
                trust_status=TrustStatus.TRUSTED,
            )

    def test_trust_requires_approved_review(self) -> None:
        with pytest.raises(ValueError, match="requires review_state=approved"):
            make_source(trust_status=TrustStatus.TRUSTED, review_state=ReviewState.PENDING)

    def test_a_malformed_hash_is_refused(self) -> None:
        with pytest.raises(ValueError, match="64 lowercase hex"):
            make_source().model_copy(update={"sha256": "nope"}).model_validate(
                make_source().model_dump() | {"sha256": "nope"}
            )

    def test_objective_refs_are_sorted_and_deduplicated(self) -> None:
        artifact = make_source(
            current_for_objective_refs=(
                "synthetic-exam-v2.0:2.2",
                "synthetic-exam-v1.1:2.2",
                "synthetic-exam-v1.1:2.2",
            )
        )

        assert artifact.current_for_objective_refs == (
            "synthetic-exam-v1.1:2.2",
            "synthetic-exam-v2.0:2.2",
        )


class TestEvidenceRegion:
    def test_a_region_with_no_readable_content_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not reviewable"):
            make_text_region().model_validate(
                make_text_region().model_dump()
                | {"exact_text": None, "accessible_description": None}
            )

    def test_an_image_region_requires_an_accessible_description(self) -> None:
        """Text alone does not make an image readable — a human must describe it."""
        with pytest.raises(ValueError, match="does not claim OCR"):
            make_image_region().model_validate(
                make_image_region().model_dump()
                | {"accessible_description": None, "exact_text": "some caption text"}
            )

    def test_a_quarantined_region_may_not_be_trusted(self) -> None:
        with pytest.raises(ValueError, match="quarantined region"):
            make_text_region(
                quarantine_status=QuarantineStatus.QUARANTINED,
                trust_status=TrustStatus.TRUSTED,
            )

    def test_region_trust_requires_approved_review(self) -> None:
        with pytest.raises(ValueError, match="requires review_state=approved"):
            make_text_region(trust_status=TrustStatus.TRUSTED, review_state=ReviewState.PENDING)

    def test_concept_tags_are_sorted_and_deduplicated(self) -> None:
        region = make_text_region(concept_tags=("zeta", "alpha", "alpha"))

        assert region.concept_tags == ("alpha", "zeta")

    def test_a_region_defaults_to_the_most_conservative_states(self) -> None:
        region = make_text_region(
            trust_status=TrustStatus.UNTRUSTED, review_state=ReviewState.PENDING
        )

        assert region.quarantine_status is QuarantineStatus.CLEAR
        assert region.privacy_classification is PrivacyClassification.INTERNAL


class TestSelectors:
    def test_a_page_text_selector_requires_an_ordered_range(self) -> None:
        with pytest.raises(ValueError, match="strictly less than end_offset"):
            PageTextSelector(page_number=1, start_offset=10, end_offset=10)

    def test_a_page_number_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            PageTextSelector(page_number=0, start_offset=0, end_offset=1)

    def test_an_image_selector_pins_its_image_hash(self) -> None:
        selector = ImageRegionSelector(
            image_sha256=sha256_of(PNG_BYTES),
            left_basis_points=0,
            top_basis_points=0,
            right_basis_points=10_000,
            bottom_basis_points=10_000,
        )

        assert selector.image_sha256 == sha256_of(PNG_BYTES)

    def test_selectors_are_discriminated_by_kind(self) -> None:
        text_region = make_text_region()
        image_region = make_image_region()

        assert text_region.selector.kind == "page_text"
        assert image_region.selector.kind == "image_region"


class TestValidationFinding:
    def test_findings_sort_by_reason_then_subject(self) -> None:
        first = ValidationFinding(
            reason_code=ValidationReasonCode.UNKNOWN_ITEM_ID,
            subject_id="b",
            message="m",
        )
        second = ValidationFinding(
            reason_code=ValidationReasonCode.UNKNOWN_ITEM_ID,
            subject_id="a",
            message="m",
        )

        assert sorted([first, second], key=lambda f: f.sort_key)[0] is second


class TestStrictness:
    def test_unknown_fields_are_forbidden(self) -> None:
        with pytest.raises(ValueError, match="Extra inputs"):
            make_text_region().model_validate(
                make_text_region().model_dump() | {"smuggled_field": 1}
            )

    def test_a_content_hash_must_be_a_sha256(self) -> None:
        with pytest.raises(ValueError, match="64 lowercase hex"):
            make_text_region().model_validate(
                make_text_region().model_dump() | {"content_sha256": text_hash("x")[:10]}
            )
