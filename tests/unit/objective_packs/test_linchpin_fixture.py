"""Admission and translation tests for the frozen split-YAML fixture tree.

These run against the real committed ``tests/linchpin/`` bytes, because
the properties under test are properties *of those bytes*: the documented
self-hash algorithm, the exact tree, and — most of all — that nothing in
the loading path converts an authoring file's own words into approval.

Negative cases copy the tree into ``tmp_path`` and mutate the copy. The
committed fixture is never written to.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from personal_lms.domain.objective_packs import (
    ImageRegionSelector,
    ObjectiveRef,
    PageTextSelector,
    QuarantineStatus,
    ReviewState,
    TrustStatus,
)
from personal_lms.objective_packs.errors import (
    PackHashMismatchError,
    PackManifestError,
    PackSchemaError,
)
from personal_lms.objective_packs.linchpin_fixture import (
    RetrievalCaseSet,
    compute_manifest_self_hash,
    load_frozen_fixture,
)
from personal_lms.objective_packs.loader import PackFileReader, PackLoadResult
from personal_lms.objective_packs.validation import ObjectivePackValidator

# The frozen fixture is YAML, and the safe decoder belongs to the optional
# ``ccna-lab`` extra. Skipping keeps the lightweight core install clean:
# these tests exist to prove the *declared* adapter reads the frozen bytes,
# and a core-only run has no adapter to prove anything about.
pytest.importorskip("yaml", reason="requires the ccna-lab extra (uv sync --extra ccna-lab)")

PROJECT_ROOT = Path(__file__).parents[3]
LINCHPIN_ROOT = PROJECT_ROOT / "tests" / "linchpin"

#: The reviewed P0 manifest self-hash. Every gate report must cite this
#: exact value; a change here means the frozen tree changed.
EXPECTED_SELF_HASH = "3798e2181eccf666c27df267df0c784460be1615e9229ae101644ff0333997a3"

#: Alan's technical approval, as recorded in the P0 approval ledger.
APPROVED_ITEM_SUFFIXES = frozenset(f"{ordinal:04d}" for ordinal in range(1, 13))
APPROVED_CLAIM_COUNT = 6

#: The two infographic regions carry a recorded human attestation. Every
#: other region is pending or rejected, and *none* of them may arrive
#: approved from the loader — approval is a persisted decision, not a file.
HUMAN_ATTESTED_REGION_IDS = frozenset(
    {"evid-infographic-native-region", "evid-infographic-allowed-region"}
)
REJECTED_REGION_IDS = frozenset({"evid-malicious-p3", "evid-wrong-blueprint-p1"})


def load(root: Path, directory: str = "tests/linchpin") -> PackLoadResult:
    return load_frozen_fixture(PackFileReader(roots=[root]), fixture_directory=directory)


@pytest.fixture
def frozen() -> PackLoadResult:
    return load(PROJECT_ROOT)


@pytest.fixture
def copied_tree(tmp_path: Path) -> Path:
    shutil.copytree(LINCHPIN_ROOT, tmp_path / "linchpin")
    return tmp_path


class TestManifestAdmission:
    def test_self_hash_reproduces_the_reviewed_value(self, frozen: PackLoadResult) -> None:
        assert frozen.fixture_manifest_hash == EXPECTED_SELF_HASH

    def test_self_hash_algorithm_is_the_documented_placeholder_substitution(self) -> None:
        payload = (LINCHPIN_ROOT / "fixture-manifest.yaml").read_bytes()
        assert compute_manifest_self_hash(payload) == EXPECTED_SELF_HASH

    def test_a_manifest_without_a_self_hash_line_fails_closed(self, copied_tree: Path) -> None:
        manifest = copied_tree / "linchpin" / "fixture-manifest.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace("manifest_self_sha256:", "unrelated_key:"),
            encoding="utf-8",
        )
        with pytest.raises(PackManifestError, match="exactly one manifest_self_sha256"):
            load(copied_tree, "linchpin")

    def test_editing_any_manifest_byte_breaks_the_self_hash(self, copied_tree: Path) -> None:
        manifest = copied_tree / "linchpin" / "fixture-manifest.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "ccna-objective-2.2-p0-fixture-baseline-v2.1.3-content-approved",
                "ccna-objective-2.2-p0-fixture-baseline-v2.1.3-tampered",
            ),
            encoding="utf-8",
        )
        with pytest.raises(PackManifestError, match="self-hash mismatch"):
            load(copied_tree, "linchpin")

    def test_the_pack_version_is_the_frozen_tree_identity(self, frozen: PackLoadResult) -> None:
        """Binding pack_version to the self-hash is what revokes stale approvals.

        A persisted review decision pins the pack version it was made
        against, so any fixture edit produces a different version and every
        prior approval stops applying without anyone remembering to sweep.
        """
        assert frozen.pack.manifest.pack_version == EXPECTED_SELF_HASH

    def test_a_manifest_never_grants_itself_reviewed_status(self, frozen: PackLoadResult) -> None:
        assert frozen.manifest.fixture_status == "draft_for_human_review"


class TestTreeAdmission:
    def test_every_listed_file_is_byte_verified(self, frozen: PackLoadResult) -> None:
        assert len(frozen.verified_file_hashes) == 22

    def test_a_tampered_source_fails_before_any_yaml_is_decoded(self, copied_tree: Path) -> None:
        source = copied_tree / "linchpin" / "sources" / "objective-2.2-synthetic.pdf"
        source.write_bytes(source.read_bytes() + b"tamper")
        with pytest.raises(PackHashMismatchError):
            load(copied_tree, "linchpin")

    def test_an_unlisted_file_breaks_the_exact_tree_contract(self, copied_tree: Path) -> None:
        (copied_tree / "linchpin" / "unlisted.txt").write_text("not frozen", encoding="utf-8")
        with pytest.raises(PackManifestError, match="differs from its frozen inventory"):
            load(copied_tree, "linchpin")

    def test_a_missing_file_breaks_the_exact_tree_contract(self, copied_tree: Path) -> None:
        (copied_tree / "linchpin" / "learners" / "ambiguous.json").unlink()
        with pytest.raises(PackManifestError, match="differs from its frozen inventory"):
            load(copied_tree, "linchpin")

    def test_a_symlink_is_refused_rather_than_followed(self, copied_tree: Path) -> None:
        target = copied_tree / "linchpin" / "learners" / "ambiguous.json"
        payload = target.read_bytes()
        decoy = copied_tree / "linchpin" / "learners" / ".decoy"
        decoy.write_bytes(payload)
        target.unlink()
        target.symlink_to(decoy)
        # The decoy itself also makes the tree unlisted, so either failure
        # is correct; what must never happen is a successful load.
        with pytest.raises(PackManifestError):
            load(copied_tree, "linchpin")

    @pytest.mark.parametrize(
        "bad_path",
        ["/etc/passwd", "../escape.yaml", "packs//claims.yaml", "C:/windows/system32"],
    )
    def test_an_inadmissible_inventory_path_fails_closed(
        self, copied_tree: Path, bad_path: str
    ) -> None:
        manifest = copied_tree / "linchpin" / "fixture-manifest.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                '- {path: "learners/ambiguous.json"', f'- {{path: "{bad_path}"'
            ),
            encoding="utf-8",
        )
        with pytest.raises(PackManifestError):
            load(copied_tree, "linchpin")

    def test_loading_twice_produces_identical_identity(self, frozen: PackLoadResult) -> None:
        again = load(PROJECT_ROOT)
        assert again.fixture_manifest_hash == frozen.fixture_manifest_hash
        assert again.verified_file_hashes == frozen.verified_file_hashes
        assert [region.evidence_id for region in again.pack.evidence_regions] == [
            region.evidence_id for region in frozen.pack.evidence_regions
        ]


class TestApprovalBoundary:
    """The whole point of the adapter: a file may restrict, never grant."""

    def test_no_region_ever_arrives_approved_from_the_loader(self, frozen: PackLoadResult) -> None:
        approved = [
            region.evidence_id
            for region in frozen.pack.evidence_regions
            if region.review_state is ReviewState.APPROVED
        ]
        assert approved == []

    def test_no_region_ever_arrives_trusted_from_the_loader(self, frozen: PackLoadResult) -> None:
        trusted = [
            region.evidence_id
            for region in frozen.pack.evidence_regions
            if region.trust_status is not TrustStatus.UNTRUSTED
        ]
        assert trusted == []

    def test_a_fixture_authored_approval_lands_exactly_where_pending_does(
        self, frozen: PackLoadResult
    ) -> None:
        """The two human-attested regions are indistinguishable here.

        Their attestation is real and is preserved in the extension
        envelope, but it becomes authority only by being replayed through
        the ordinary approval command into the persisted store.
        """
        by_id = frozen.pack.evidence_by_id
        for evidence_id in HUMAN_ATTESTED_REGION_IDS:
            region = by_id[evidence_id]
            assert region.review_state is ReviewState.PENDING
            assert region.trust_status is TrustStatus.UNTRUSTED
            assert region.quarantine_status is QuarantineStatus.CLEAR

    def test_a_fixture_authored_rejection_is_honoured(self, frozen: PackLoadResult) -> None:
        by_id = frozen.pack.evidence_by_id
        for evidence_id in REJECTED_REGION_IDS:
            region = by_id[evidence_id]
            assert region.review_state is ReviewState.REJECTED
            assert region.quarantine_status is QuarantineStatus.QUARANTINED

    def test_every_assessment_item_is_technically_approved(self, frozen: PackLoadResult) -> None:
        approved = {
            item.item_id[-4:]
            for item in frozen.pack.items
            if item.review_state is ReviewState.APPROVED
        }
        assert approved == APPROVED_ITEM_SUFFIXES

    def test_no_assessment_item_remains_pending(self, frozen: PackLoadResult) -> None:
        pending = [
            item.item_id
            for item in frozen.pack.items
            if item.review_state is not ReviewState.APPROVED
        ]
        assert pending == []

    def test_exactly_six_claims_are_technically_approved(self, frozen: PackLoadResult) -> None:
        approved = [
            claim.claim_id
            for claim in frozen.pack.claims
            if claim.review_state is ReviewState.APPROVED
        ]
        assert len(approved) == APPROVED_CLAIM_COUNT

    def test_answer_bearing_claims_are_derived_not_asserted(self, frozen: PackLoadResult) -> None:
        """No claim arrives carrying the flag; the set comes from item refs."""
        assert all(not claim.is_answer_bearing for claim in frozen.pack.claims)
        assert len(frozen.pack.answer_bearing_claim_ids) == APPROVED_CLAIM_COUNT

    def test_the_pack_itself_is_pending(self, frozen: PackLoadResult) -> None:
        assert frozen.pack.review_state is ReviewState.PENDING

    def test_approved_assessment_content_has_no_review_blocker(
        self, frozen: PackLoadResult
    ) -> None:
        report = ObjectivePackValidator().validate(frozen.pack)
        not_reviewed = [
            finding.subject_id
            for finding in report.errors
            if finding.reason_code.value == "record_not_reviewed"
        ]
        assert not_reviewed == []


class TestTranslation:
    def test_source_currency_comes_from_the_source_not_its_regions(
        self, frozen: PackLoadResult
    ) -> None:
        by_id = frozen.pack.sources_by_id
        assert by_id["src-objective-2.2-synthetic-pdf"].current_for_objective_refs == (
            "ccna-200-301-v1.1:2.2",
        )
        # The distractor declares blueprint v2.0 and can never become
        # current for the v1.1 objective, whatever its regions claim.
        assert by_id["src-wrong-blueprint-source-pdf"].current_for_objective_refs == (
            "ccna-200-301-v2.0:2.1.b",
        )

    def test_a_quarantined_source_grants_no_use(self, frozen: PackLoadResult) -> None:
        distractor = frozen.pack.sources_by_id["src-wrong-blueprint-source-pdf"]
        assert distractor.permitted_uses == frozenset()
        assert distractor.quarantine_status is QuarantineStatus.QUARANTINED

    def test_page_text_selectors_are_page_scoped_not_fabricated_offsets(
        self, frozen: PackLoadResult
    ) -> None:
        """Offsets are a property of a parser's output, never of the fixture."""
        text_selectors = [
            region.selector
            for region in frozen.pack.evidence_regions
            if isinstance(region.selector, PageTextSelector)
        ]
        assert text_selectors
        assert all(selector.is_page_scoped for selector in text_selectors)

    def test_image_selectors_carry_no_derived_pixel_box(self, frozen: PackLoadResult) -> None:
        image_selectors = [
            region.selector
            for region in frozen.pack.evidence_regions
            if isinstance(region.selector, ImageRegionSelector)
        ]
        assert len(image_selectors) == 2
        assert all(not hasattr(selector, "pixel_box") for selector in image_selectors)

    def test_authoring_only_metadata_lives_in_the_extension_envelope(
        self, frozen: PackLoadResult
    ) -> None:
        extensions = frozen.fixture_extensions
        assert extensions is not None
        assert extensions.source_sizes["src-objective-2.2-synthetic-pdf"] == 3252
        assert extensions.image_dimensions["src-objective-2.2-infographic-png"] == (900, 500)
        assert set(extensions.region_pixel_sha256) == HUMAN_ATTESTED_REGION_IDS

    def test_recorded_human_decisions_are_inert_data(self, frozen: PackLoadResult) -> None:
        """They are preserved so a reviewer can replay them, and do nothing here."""
        extensions = frozen.fixture_extensions
        assert extensions is not None
        reviewers = {record["human_reviewer_id"] for record in extensions.recorded_human_decisions}
        assert reviewers == {"alan"}
        # ...and still nothing is approved.
        assert all(
            region.review_state is not ReviewState.APPROVED
            for region in frozen.pack.evidence_regions
        )

    def test_a_subobjective_reference_parses_without_widening_the_grammar(self) -> None:
        parsed = ObjectiveRef.parse("ccna-200-301-v2.0:2.1.b")
        assert parsed.number == "2.1.b"
        assert parsed.value == "ccna-200-301-v2.0:2.1.b"

    @pytest.mark.parametrize(
        "malformed", ["ccna-200-301-v2.0:2.abc", "ccna-200-301-v2.0:2.1.zz", "x-v1:2.1.b.c"]
    )
    def test_a_malformed_reference_is_still_refused(self, malformed: str) -> None:
        with pytest.raises(ValueError, match="objective_ref"):
            ObjectiveRef.parse(malformed)


class TestRecomputation:
    def test_cardinality_is_recomputed_from_the_reference_graph(
        self, frozen: PackLoadResult
    ) -> None:
        report = ObjectivePackValidator().validate(frozen.pack)
        assert report.recomputed_coverage["baseline_items"] == 12
        assert report.recomputed_coverage["followup_items"] == 6
        assert report.recomputed_coverage["exit_probe_items"] == 1
        assert report.recomputed_coverage["claims"] == 6
        assert report.recomputed_coverage["evidence_regions"] == 11
        assert report.recomputed_coverage["source_artifacts"] == 3

    def test_claim_grounding_reproduces_the_frozen_scores_exactly(
        self, frozen: PackLoadResult
    ) -> None:
        """Basis points, so 8500 is 85.00 and 9326 is 93.26 with no rounding drift."""
        report = ObjectivePackValidator().validate(frozen.pack)
        assert report.recomputed_claim_scores == {
            "claim-trunk-multiple-vlans": 8_500,
            "claim-dot1q-identifies-vlan": 8_500,
            "claim-native-vlan-treatment": 9_326,
            "claim-native-vlan-agreement": 8_500,
            "claim-allowed-vlan-effect": 9_326,
            "claim-trunk-verification-state": 8_500,
        }

    def test_the_recomputed_scores_equal_the_declared_ones(self, frozen: PackLoadResult) -> None:
        """Evidence that the two policy *names* describe the same arithmetic.

        The fixture's supports declare ``ccna-grounding-v1`` while this
        build implements ``design-03-ingestion-rag-evidence-1.0``, which
        the validator reports as a mismatch. That reconciliation is a
        reviewer decision; this test records that it is a naming question
        and not an arithmetic one.
        """
        report = ObjectivePackValidator().validate(frozen.pack)
        for claim in frozen.pack.claims:
            assert (
                claim.declared_grounding_score_basis_points
                == (report.recomputed_claim_scores[claim.claim_id])
            )

    def test_exposure_sets_are_pairwise_disjoint(self, frozen: PackLoadResult) -> None:
        pack = frozen.pack
        baseline = set(pack.baseline_item_ids)
        exit_probe = set(pack.exit_probe_item_ids)
        followup = {item_id for rule in pack.followup_rules for item_id in rule.followup_item_ids}
        assert baseline & exit_probe == set()
        assert baseline & followup == set()
        assert exit_probe & followup == set()

    def test_declared_coverage_is_compared_against_recomputation(
        self, frozen: PackLoadResult
    ) -> None:
        assert frozen.pack.declared_coverage == {"baseline_items": 12}
        report = ObjectivePackValidator().validate(frozen.pack)
        assert "declared_coverage_mismatch" not in report.reason_codes


class TestRetrievalContract:
    def test_the_frozen_cases_load_as_typed_records(self, frozen: PackLoadResult) -> None:
        cases = frozen.retrieval_cases
        assert isinstance(cases, RetrievalCaseSet)
        assert len(cases.supported) == 10
        assert len(cases.unsupported) == 2
        assert [case.case_id for case in cases.supported] == [
            f"rc-{index:02d}" for index in range(1, 11)
        ]
        assert [case.case_id for case in cases.unsupported] == ["rc-11", "rc-12"]

    def test_unsupported_cases_pin_their_exact_abstention_codes(
        self, frozen: PackLoadResult
    ) -> None:
        cases = frozen.retrieval_cases
        assert isinstance(cases, RetrievalCaseSet)
        codes = {case.case_id: case.expected_abstention_reason_code for case in cases.unsupported}
        assert codes == {
            "rc-11": "no_supporting_evidence_in_eligible_corpus",
            "rc-12": "wrong_blueprint_version_excluded",
        }

    def test_a_case_set_naming_another_objective_is_refused(self, copied_tree: Path) -> None:
        cases = copied_tree / "linchpin" / "queries" / "retrieval-cases.json"
        cases.write_text(
            cases.read_text(encoding="utf-8").replace(
                "ccna-200-301-v1.1:2.2", "ccna-200-301-v2.0:2.2"
            ),
            encoding="utf-8",
        )
        manifest = copied_tree / "linchpin" / "fixture-manifest.yaml"
        _repin(manifest, "queries/retrieval-cases.json", cases)
        with pytest.raises(PackSchemaError, match="retrieval cases declare objective"):
            load(copied_tree, "linchpin")


def _repin(manifest: Path, relative_path: str, changed: Path) -> None:
    """Rewrite one inventory hash and the manifest's own self-hash.

    Used only to reach a check *past* byte verification: without it a
    mutated fixture stops at the hash comparison, which is a different
    test.
    """
    import hashlib
    import re

    text = manifest.read_text(encoding="utf-8")
    digest = hashlib.sha256(changed.read_bytes()).hexdigest()
    text = re.sub(
        rf'(- \{{path: "{re.escape(relative_path)}", sha256: )[0-9a-f]{{64}}',
        rf"\g<1>{digest}",
        text,
    )
    updated = compute_manifest_self_hash(text.encode("utf-8"))
    text = re.sub(r"(manifest_self_sha256: )[0-9a-f]{64}", rf"\g<1>{updated}", text)
    manifest.write_text(text, encoding="utf-8")
