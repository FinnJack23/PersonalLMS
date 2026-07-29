"""Gate 1 over the real frozen fixture: extraction, retrieval, and guards.

Runs against the committed ``tests/linchpin/`` bytes with the declared
optional adapters, so what is asserted here is what the gate actually
does with real PDF and PNG files rather than with doubles.

Skipped wholesale when the ``ccna-lab`` extra is absent: these tests
exist to prove the *declared* adapters work, and silently substituting a
double would defeat the point.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from personal_lms.domain.evidence_review import (
    EvidenceReviewDecision,
    EvidenceReviewKind,
    EvidenceReviewOutcome,
    ReviewerIdentity,
    derive_decision_id,
)
from personal_lms.domain.objective_packs import ClaimSupport
from personal_lms.evidence_review.authority import subject_digest_for
from personal_lms.evidence_review.service import EvidenceReviewService
from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
from personal_lms.extraction.artifacts import (
    ExtractionOutcome,
    locate_passage,
    normalize_extracted_text,
    normalized_bbox_to_pixel_box,
    same_extracted_text,
)
from personal_lms.labs.ccna_mastery.gates import (
    GATE_1_EXPECTATION_REFS,
    GateArtifactPaths,
    GateCheck,
    GateCheckStatus,
    GateDefinition,
    GateStatus,
    GoldenArtifactGuard,
    GoldenWriteRefusedError,
    ObservedGateReportStore,
    provenance_path_for_primary,
)
from personal_lms.labs.ccna_mastery.report_schema import (
    FROZEN_DEFERRABLE_CHECK_IDS,
    STATUS_PROJECTION,
    ReportProjectionError,
    frozen_schema_view,
    report_from_bound_provenance,
    validate_against_frozen_schema,
)
from personal_lms.labs.ccna_mastery.retrieval import RetrievalHarness
from personal_lms.labs.ccna_mastery.wiring import EvidenceGateRunner
from personal_lms.objective_packs.linchpin_fixture import load_frozen_fixture
from personal_lms.objective_packs.loader import ObjectivePackLoader, PackFileReader
from personal_lms.objective_packs.scoring import (
    CLAIM_SCORE_ALGORITHM_PROVENANCE,
    CLAIM_SCORE_POLICY_VERSION,
    GROUP_WEIGHTS_PER_TEN_THOUSAND,
    ClaimEvidencePolicy,
)
from personal_lms.objective_packs.validation import ObjectivePackValidator

pytest.importorskip("pdfminer", reason="requires the ccna-lab extra (uv sync --extra ccna-lab)")
pytest.importorskip("PIL", reason="requires the ccna-lab extra (uv sync --extra ccna-lab)")
yaml = pytest.importorskip("yaml", reason="requires the ccna-lab extra (uv sync --extra ccna-lab)")

from personal_lms.extraction.local_fixture import (  # noqa: E402 - after the extra check
    LocalFixtureExtractor,
    PdfMinerTextExtractor,
    PillowPngDecoder,
)

PROJECT_ROOT = Path(__file__).parents[4]
FIXTURE_DIRECTORY = "tests/linchpin"
MANIFEST_SELF_HASH = "3798e2181eccf666c27df267df0c784460be1615e9229ae101644ff0333997a3"

#: The two regions Alan attested to, and the only ones any human decision
#: covers. Everything else stays pending.
ATTESTED_REGION_IDS = ("evid-infographic-native-region", "evid-infographic-allowed-region")

FULL_APPROVED_REGION_IDS = (
    "evid-trunk-multi-vlan-p1",
    "evid-dot1q-tag-p1",
    "evid-native-untagged-p1",
    "evid-native-agreement-p2",
    "evid-allowed-vlan-p2",
    "evid-verify-trunk-p2",
    "evid-synthetic-show-p3",
    *ATTESTED_REGION_IDS,
)

#: Frozen image-region hashes, under the image-region-rgb-v1 scheme.
FROZEN_REGION_PIXEL_HASHES = {
    "evid-infographic-native-region": (
        "559a0b8c866049543e13fafba9470c08f79acf0a989f7b44cdd011e7fc777325"
    ),
    "evid-infographic-allowed-region": (
        "fa3472d908fb620265e2496b75f8cf39b86c153e2d3a16b46aac94d11a05f7fe"
    ),
}


class _FixtureLoader(ObjectivePackLoader):
    """Points the gate runner's loader seam at the split-YAML adapter."""

    def load(self, *, pack_directory: str, **_: Any):  # type: ignore[no-untyped-def]
        return load_frozen_fixture(self.reader, fixture_directory=pack_directory)


@pytest.fixture
def reader() -> PackFileReader:
    return PackFileReader(roots=[PROJECT_ROOT])


@pytest.fixture
def extractor(reader: PackFileReader) -> LocalFixtureExtractor:
    return LocalFixtureExtractor(
        reader,
        pdf_text_extractor=PdfMinerTextExtractor(),
        png_pixel_decoder=PillowPngDecoder(),
    )


@pytest.fixture
def review_service(tmp_path: Path):  # type: ignore[no-untyped-def]
    repository = SQLiteEvidenceReviewRepository.open(str(tmp_path / "review.sqlite3"))
    repository.initialize_schema()
    try:
        yield EvidenceReviewService(repository)
    finally:
        repository.close()


def _record_attestations(service: EvidenceReviewService, reader: PackFileReader) -> None:
    """Replay Alan's recorded region attestation through the ordinary path.

    The fixture *records* that a human approved both regions. Nothing reads
    that record as authority; a reviewer replays it through the same
    persistence the approval command uses, and only then does the gate see
    an approval. This helper is that replay.
    """
    _record_region_decisions(service, reader, ATTESTED_REGION_IDS)


def _record_region_decisions(
    service: EvidenceReviewService,
    reader: PackFileReader,
    evidence_ids: tuple[str, ...],
) -> None:
    """Record exact-subject approvals through the production review contract."""
    pack = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY).pack
    decided_at = datetime(2026, 7, 27, tzinfo=UTC)
    for evidence_id in evidence_ids:
        region = pack.evidence_by_id[evidence_id]
        artifact = pack.sources_by_id[region.source_id]
        digest = subject_digest_for(pack, region, artifact)
        kind = (
            EvidenceReviewKind.VISUAL
            if region.selector.kind == "image_region"
            else EvidenceReviewKind.TEXT
        )
        service.record_decision(
            EvidenceReviewDecision(
                decision_id=derive_decision_id(
                    evidence_id=evidence_id,
                    subject_digest=digest,
                    reviewer_id="alan",
                    decided_at=decided_at.isoformat(),
                ),
                evidence_id=evidence_id,
                source_id=artifact.source_id,
                pack_id=pack.manifest.pack_id,
                pack_version=pack.manifest.pack_version,
                objective_ref=pack.objective_ref,
                kind=kind,
                outcome=EvidenceReviewOutcome.APPROVED,
                subject_digest=digest,
                source_sha256=artifact.sha256,
                reviewer=ReviewerIdentity(reviewer_id="alan", role="content_reviewer"),
                reason="Recorded exact-subject technical approval",
                accessible_description=(
                    region.accessible_description if kind is EvidenceReviewKind.VISUAL else None
                ),
                decided_at=decided_at,
            ),
            pack=pack,
            region=region,
            artifact=artifact,
        )


def _run_gate(reader: PackFileReader, extractor, service):  # type: ignore[no-untyped-def]
    runner = EvidenceGateRunner(
        loader=_FixtureLoader(reader),
        validator=ObjectivePackValidator(),
        review_service=service,
        extractor=extractor,
        code_revision="test-revision",
    )
    return runner.run(pack_directory=FIXTURE_DIRECTORY, run_id="test-run")


class TestNormalizationContract:
    """Whitespace may vary; tokens may not."""

    def test_line_wrapping_and_column_padding_are_absorbed(self) -> None:
        assert same_extracted_text("a trunk\ncarries VLANs", "a  trunk   carries VLANs")

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("native VLAN 99", "native VLAN 9"),
            ("show interfaces trunk", "show interface trunk"),
            ("frames are sent untagged", "frames are not sent untagged"),
            ("Gi0/1 trunking", "Gi0/2 trunking"),
            ("allowed 10,20,99", "allowed 10,20,9"),
            ("VLAN 99", "VLAN99"),
        ],
    )
    def test_a_semantic_difference_is_never_absorbed(self, left: str, right: str) -> None:
        assert not same_extracted_text(left, right)

    def test_normalization_preserves_the_token_sequence_exactly(self) -> None:
        raw = "Interface  Mode\n\nGi0/1      trunk"
        assert normalize_extracted_text(raw).split() == raw.split()

    def test_an_ambiguous_passage_does_not_locate(self) -> None:
        """Two occurrences do not identify a region, so neither is chosen."""
        assert locate_passage("trunk here and trunk there", "trunk") is None

    def test_a_located_passage_returns_offsets_into_the_real_text(self) -> None:
        page = "Header line\nAn 802.1Q trunk link\ncarries VLANs."
        located = locate_passage(page, "An 802.1Q trunk link carries VLANs.")
        assert located is not None
        start, end = located
        # The slice comes from the page, so it keeps the page's newline.
        assert page[start:end] == "An 802.1Q trunk link\ncarries VLANs."


class TestRealSourceExtraction:
    def test_pdf_text_comes_from_the_frozen_bytes_via_pdfminer(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor
    ) -> None:
        load = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY)
        pack = load.pack
        region = pack.evidence_by_id["evid-trunk-multi-vlan-p1"]
        artifact = pack.sources_by_id[region.source_id]
        binding = load.source_manifest_bindings[artifact.source_id]

        result = extractor.extract_region(
            region, artifact, relative_path=f"{FIXTURE_DIRECTORY}/{binding.relative_path}"
        )

        assert result.outcome is ExtractionOutcome.EXTRACTED
        assert result.extractor_id == "pdfminer-six"
        assert result.text is not None
        assert same_extracted_text(result.text, region.exact_text or "")
        # Proof it is the PDF's text and not a copy of the fixture string:
        # the PDF wraps this sentence across a line, the fixture does not.
        assert "\n" in result.text
        assert result.text != region.exact_text

    def test_the_injected_paragraph_extracts_as_inert_data(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor
    ) -> None:
        """It is readable, it is quarantined, and it changes nothing."""
        load = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY)
        pack = load.pack
        region = pack.evidence_by_id["evid-malicious-p3"]
        artifact = pack.sources_by_id[region.source_id]
        binding = load.source_manifest_bindings[artifact.source_id]

        result = extractor.extract_region(
            region, artifact, relative_path=f"{FIXTURE_DIRECTORY}/{binding.relative_path}"
        )

        assert result.outcome is ExtractionOutcome.EXTRACTED
        assert result.text is not None
        assert "SYSTEM NOTICE" in result.text
        # Extraction produced a string. It did not produce an approval,
        # a score, or a policy change — the region is still quarantined
        # and still rejected.
        assert region.quarantine_status.value == "quarantined"
        assert region.review_state.value == "rejected"

    def test_the_wrong_version_pdf_extracts_but_stays_out_of_scope(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor
    ) -> None:
        load = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY)
        pack = load.pack
        region = pack.evidence_by_id["evid-wrong-blueprint-p1"]
        artifact = pack.sources_by_id[region.source_id]
        binding = load.source_manifest_bindings[artifact.source_id]

        result = extractor.extract_region(
            region, artifact, relative_path=f"{FIXTURE_DIRECTORY}/{binding.relative_path}"
        )

        assert result.outcome is ExtractionOutcome.EXTRACTED
        assert pack.objective_ref not in region.objective_refs
        assert artifact.permitted_uses == frozenset()

    def test_png_pixels_reproduce_the_frozen_region_hashes(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor
    ) -> None:
        """Full decode, the frozen round-half-up box, and the frozen hash."""
        load = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY)
        pack = load.pack
        for evidence_id, expected_hash in FROZEN_REGION_PIXEL_HASHES.items():
            region = pack.evidence_by_id[evidence_id]
            artifact = pack.sources_by_id[region.source_id]
            binding = load.source_manifest_bindings[artifact.source_id]

            result = extractor.extract_region(
                region, artifact, relative_path=f"{FIXTURE_DIRECTORY}/{binding.relative_path}"
            )

            assert result.outcome is ExtractionOutcome.EXTRACTED
            assert result.extractor_id == "pillow"
            assert (result.image_width, result.image_height) == (900, 500)
            assert result.region_content_sha256 == expected_hash
            assert result.text is None  # no OCR claim, ever

    def test_derived_pixel_boxes_match_the_frozen_review_record(
        self, reader: PackFileReader
    ) -> None:
        load = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY)
        extensions = load.fixture_extensions
        assert extensions is not None
        from personal_lms.extraction.artifacts import ImageDimensions

        dimensions = ImageDimensions(width=900, height=500)
        for evidence_id, frozen_box in extensions.region_pixel_boxes.items():
            selector = load.pack.evidence_by_id[evidence_id].selector
            assert normalized_bbox_to_pixel_box(selector, dimensions) == frozen_box  # type: ignore[arg-type]


class TestRetrievalContract:
    def test_the_eligible_corpus_is_exactly_the_approved_regions(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        _record_attestations(review_service, reader)
        result = _run_gate(reader, extractor, review_service)
        assert result.retrieval is not None
        assert result.retrieval.eligible_evidence_ids == tuple(sorted(ATTESTED_REGION_IDS))

    def test_nothing_is_eligible_without_a_persisted_decision(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        """The fixture says two regions are approved. It is not enough."""
        result = _run_gate(reader, extractor, review_service)
        assert result.retrieval is not None
        assert result.retrieval.eligible_evidence_ids == ()

    def test_both_unsupported_cases_abstain_with_the_frozen_codes(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        _record_attestations(review_service, reader)
        result = _run_gate(reader, extractor, review_service)
        assert result.retrieval is not None
        outcomes = result.retrieval.outcomes_by_id
        assert outcomes["rc-11"].abstention_reason_code == (
            "no_supporting_evidence_in_eligible_corpus"
        )
        assert outcomes["rc-12"].abstention_reason_code == "wrong_blueprint_version_excluded"
        assert outcomes["rc-11"].satisfied
        assert outcomes["rc-12"].satisfied

    def test_both_unsupported_cases_still_abstain_with_the_full_approved_corpus(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        """Newly eligible generic trunk text must not create false answers."""
        _record_region_decisions(review_service, reader, FULL_APPROVED_REGION_IDS)
        result = _run_gate(reader, extractor, review_service)
        assert result.retrieval is not None
        outcomes = result.retrieval.outcomes_by_id
        assert [
            (outcome.case_id, outcome.ranked_ids, outcome.missing_evidence_ids)
            for outcome in result.retrieval.supported_outcomes
            if not outcome.satisfied
        ] == []
        assert outcomes["rc-11"].ranked == ()
        assert outcomes["rc-11"].abstention_reason_code == (
            "no_supporting_evidence_in_eligible_corpus"
        )
        assert outcomes["rc-12"].ranked == ()
        assert outcomes["rc-12"].abstention_reason_code == "wrong_blueprint_version_excluded"
        assert outcomes["rc-11"].satisfied
        assert outcomes["rc-12"].satisfied

    def test_the_diagram_cases_return_their_expected_region_in_the_top_five(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        _record_attestations(review_service, reader)
        result = _run_gate(reader, extractor, review_service)
        assert result.retrieval is not None
        outcomes = result.retrieval.outcomes_by_id
        for case_id, expected in (
            ("rc-08", "evid-infographic-native-region"),
            ("rc-09", "evid-infographic-allowed-region"),
        ):
            outcome = outcomes[case_id]
            assert outcome.satisfied
            assert expected in outcome.ranked_ids[:5]

    def test_every_remaining_miss_is_a_pending_approval_not_a_ranking_defect(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        """The honest blocker, pinned exactly.

        Eight supported cases cannot be satisfied, and every one of them
        fails for the same reason: its expected evidence carries no
        persisted human approval. None fails because the ranker put the
        wrong thing first.
        """
        _record_attestations(review_service, reader)
        result = _run_gate(reader, extractor, review_service)
        assert result.retrieval is not None
        unsatisfied = [
            outcome for outcome in result.retrieval.supported_outcomes if not outcome.satisfied
        ]
        assert {outcome.case_id for outcome in unsatisfied} == {
            "rc-01",
            "rc-02",
            "rc-03",
            "rc-04",
            "rc-05",
            "rc-06",
            "rc-07",
            "rc-10",
        }
        for outcome in unsatisfied:
            assert set(outcome.missing_evidence_ids) == set(outcome.ineligible_expected_ids)

    def test_the_injected_and_wrong_version_regions_are_never_returned(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        _record_attestations(review_service, reader)
        result = _run_gate(reader, extractor, review_service)
        assert result.retrieval is not None
        forbidden = {"evid-malicious-p3", "evid-wrong-blueprint-p1"}
        for outcome in result.retrieval.outcomes:
            assert not forbidden & set(outcome.ranked_ids)

    def test_a_fresh_run_reproduces_the_same_ids_and_index_hash(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        _record_attestations(review_service, reader)
        first = _run_gate(reader, extractor, review_service)
        second = _run_gate(reader, extractor, review_service)
        assert first.retrieval is not None and second.retrieval is not None
        assert first.retrieval.index_content_hash == second.retrieval.index_content_hash
        assert [outcome.ranked_ids for outcome in first.retrieval.outcomes] == [
            outcome.ranked_ids for outcome in second.retrieval.outcomes
        ]
        assert first.report.content_hash == second.report.content_hash

    def test_insertion_order_never_decides_a_ranking(
        self, reader: PackFileReader, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        _record_attestations(review_service, reader)
        load = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY)
        from personal_lms.evidence_review.authority import (
            EvidenceAuthoritySnapshot,
            authorized_view,
        )

        snapshot = EvidenceAuthoritySnapshot.build(pack=load.pack, review_service=review_service)
        authorized = authorized_view(load.pack, snapshot)
        envelope = ObjectivePackValidator().build_evidence_envelope(
            authorized,
            __import__(
                "personal_lms.labs.ccna_mastery.wiring", fromlist=["default_evidence_policy"]
            ).default_evidence_policy(authorized),
            authority=snapshot,
        )
        forward = RetrievalHarness(pack=authorized, envelope=envelope)
        reversed_pack = authorized.model_copy(
            update={"evidence_regions": tuple(reversed(authorized.evidence_regions))}
        )
        backward = RetrievalHarness(pack=reversed_pack, envelope=envelope)
        query = "Allowed VLANs caption on the trunk topology diagram"
        assert [hit.evidence_id for hit in forward.search(query, limit=5)] == [
            hit.evidence_id for hit in backward.search(query, limit=5)
        ]
        assert forward.index_content_hash == backward.index_content_hash


class TestGateReport:
    def test_the_report_cites_the_verified_manifest_self_hash(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        result = _run_gate(reader, extractor, review_service)
        assert result.report.fixture_manifest_hash == MANIFEST_SELF_HASH

    def test_no_required_check_is_missing_from_the_report(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        """Absence must never read as success, so every row is present."""
        result = _run_gate(reader, extractor, review_service)
        assert result.report.missing_required_check_ids == ()
        assert result.report.unknown_check_ids == ()

    def test_the_gate_does_not_pass_while_content_approval_is_pending(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        _record_attestations(review_service, reader)
        result = _run_gate(reader, extractor, review_service)
        assert result.report.status is not GateStatusPassed()

    def test_real_extraction_satisfies_the_fixture_extraction_row(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        result = _run_gate(reader, extractor, review_service)
        check = {check.check_id: check for check in result.report.checks}["G1-FX-02"]
        assert check.status is GateCheckStatus.PASSED
        assert check.reason_code == "regions_extracted_and_verified"

    def test_the_frozen_schema_view_validates(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        result = _run_gate(reader, extractor, review_service)
        view = frozen_schema_view(result.report)
        schema = (
            PROJECT_ROOT / FIXTURE_DIRECTORY / "schemas" / "gate-report.schema.json"
        ).read_bytes()
        assert validate_against_frozen_schema(view, schema_bytes=schema) == ()

    def test_the_written_primary_itself_validates_and_binds_rich_provenance(
        self,
        reader: PackFileReader,
        extractor: LocalFixtureExtractor,
        review_service,
        tmp_path: Path,
    ) -> None:  # type: ignore[no-untyped-def]
        result = _run_gate(reader, extractor, review_service)
        paths = GateArtifactPaths.for_project_root(tmp_path)
        primary = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths)).write(
            result.report
        )
        primary_bytes = primary.read_bytes()
        primary_payload = json.loads(primary_bytes)
        schema = (
            PROJECT_ROOT / FIXTURE_DIRECTORY / "schemas" / "gate-report.schema.json"
        ).read_bytes()

        assert validate_against_frozen_schema(primary_payload, schema_bytes=schema) == ()
        restored = report_from_bound_provenance(
            primary_bytes=primary_bytes,
            provenance_bytes=provenance_path_for_primary(primary).read_bytes(),
        )
        assert restored.to_canonical_json() == result.report.to_canonical_json()

    def test_the_schema_view_never_reports_a_non_pass_as_a_pass(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        result = _run_gate(reader, extractor, review_service)
        view = frozen_schema_view(result.report)
        internal = {check.check_id: check.status.value for check in result.report.checks}
        for rendered in view["checks"]:
            if rendered["status"] == "pass":
                assert internal[rendered["check_id"]] == "passed"

    def test_the_subset_validator_refuses_a_schema_it_cannot_fully_check(self) -> None:
        """An unimplemented keyword is a refusal, never a silent pass."""
        schema = json.dumps({"type": "object", "propertyNames": {"pattern": "^x"}}).encode()
        with pytest.raises(ValueError, match="unsupported JSON Schema keyword"):
            validate_against_frozen_schema({}, schema_bytes=schema)


class TestGoldenGuard:
    def test_accepted_goldens_live_outside_the_exact_frozen_fixture_tree(self) -> None:
        paths = GateArtifactPaths.for_project_root(PROJECT_ROOT)
        fixture_root = (PROJECT_ROOT / FIXTURE_DIRECTORY).resolve()

        assert (
            paths.accepted_root == (PROJECT_ROOT / "tests" / "goldens" / "ccna-mastery").resolve()
        )
        assert paths.expected_root == paths.accepted_root
        assert paths.accepted_root != fixture_root
        assert fixture_root not in paths.accepted_root.parents

    def test_a_normal_run_cannot_write_into_the_expected_tree(self, tmp_path: Path) -> None:
        paths = GateArtifactPaths(
            expected_root=tmp_path / "expected", observed_root=tmp_path / "observed"
        )
        guard = GoldenArtifactGuard(paths=paths, reviewer_authorized=False)
        with pytest.raises(GoldenWriteRefusedError):
            guard.assert_write_authorized(paths.expected_root / "gate-1.json")

    def test_an_observed_report_cannot_be_steered_into_the_expected_tree(
        self, tmp_path: Path
    ) -> None:
        paths = GateArtifactPaths(
            expected_root=tmp_path / "expected", observed_root=tmp_path / "observed"
        )
        paths.expected_root.mkdir(parents=True)
        store = ObservedGateReportStore(
            guard=GoldenArtifactGuard(paths=paths, reviewer_authorized=False)
        )
        report = _minimal_report()
        with pytest.raises(GoldenWriteRefusedError):
            store.write(report, destination_override=paths.expected_root / "gate-1.json")
        assert list(paths.expected_root.iterdir()) == []

    def test_the_committed_expected_tree_is_untouched_by_a_gate_run(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        """The strongest form of the guarantee: real files, real hashes."""
        import hashlib

        expected_root = PROJECT_ROOT / FIXTURE_DIRECTORY / "expected"
        before = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(expected_root.iterdir())
        }
        _run_gate(reader, extractor, review_service)
        after = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(expected_root.iterdir())
        }
        assert before == after
        assert len(before) == 4

    def test_an_authorized_guard_still_requires_a_named_reviewer(self, tmp_path: Path) -> None:
        paths = GateArtifactPaths(
            expected_root=tmp_path / "expected", observed_root=tmp_path / "observed"
        )
        with pytest.raises(ValueError, match="explicit reviewer_id"):
            GoldenArtifactGuard(paths=paths, reviewer_authorized=True)


def _minimal_report():  # type: ignore[no-untyped-def]
    from personal_lms.labs.ccna_mastery.gates import GateCheck, GateId, GateReport

    moment = datetime(2026, 7, 27, tzinfo=UTC)
    return GateReport(
        gate_id=GateId.GATE_1,
        run_id="guard-probe",
        code_revision="test",
        fixture_manifest_hash="0" * 64,
        checks=(
            GateCheck(
                check_id="G1-GO-01",
                status=GateCheckStatus.NOT_RUN,
                reason_code="probe",
            ),
        ),
        started_at=moment,
        finished_at=moment,
    )


def GateStatusPassed():  # noqa: N802 - reads as a value at the call site
    from personal_lms.labs.ccna_mastery.gates import GateStatus

    return GateStatus.PASSED


class TestCalculationPolicyReconciliation:
    """Decision 1: one public contract name, one separate specification.

    The frozen fixture declares ``ccna-grounding-v1`` for every support
    edge. That is the *public calculation-policy identifier* — the name
    two parties agreed to score under, and the key a gate comparison uses.
    ``design-03-ingestion-rag-evidence-1.0`` names the document the
    arithmetic is specified in. Treating the second as the first made a
    correctly-authored fixture look like it disagreed with an
    implementation that was faithfully implementing it.
    """

    def test_the_frozen_supports_match_the_runtime_public_policy(
        self, reader: PackFileReader
    ) -> None:
        pack = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY).pack
        declared = {
            support.calculation_policy_version for claim in pack.claims for support in claim.support
        }
        assert declared == {CLAIM_SCORE_POLICY_VERSION}
        assert CLAIM_SCORE_POLICY_VERSION == "ccna-grounding-v1"

    def test_no_calculation_policy_mismatch_remains(self, reader: PackFileReader) -> None:
        pack = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY).pack
        report = ObjectivePackValidator().validate(pack)
        assert "calculation_policy_mismatch" not in report.reason_codes

    def test_the_six_frozen_scores_are_unchanged(self, reader: PackFileReader) -> None:
        """The arithmetic did not move: the reconciliation was about a name."""
        pack = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY).pack
        report = ObjectivePackValidator().validate(pack)
        assert report.recomputed_claim_scores == {
            "claim-trunk-multiple-vlans": 8_500,
            "claim-dot1q-identifies-vlan": 8_500,
            "claim-native-vlan-treatment": 9_326,
            "claim-native-vlan-agreement": 8_500,
            "claim-allowed-vlan-effect": 9_326,
            "claim-trunk-verification-state": 8_500,
        }
        for claim in pack.claims:
            assert (
                claim.declared_grounding_score_basis_points
                == (report.recomputed_claim_scores[claim.claim_id])
            )

    def test_the_five_factor_product_and_group_weights_are_unchanged(self) -> None:
        """Pinned directly, so a formula edit cannot hide behind a rename."""
        assert GROUP_WEIGHTS_PER_TEN_THOUSAND == (10_000, 1_500, 500)
        policy = ClaimEvidencePolicy()
        # 0.85 x 1 x 1 x 1 x 1 = 0.85 -> 8500 bp, exact integer arithmetic.
        edge = ClaimEvidencePolicy.score_support(
            ClaimSupport(
                support_id="s",
                evidence_id="e",
                relationship="direct",
                authority_basis_points=8_500,
                directness_basis_points=10_000,
                provenance_completeness_basis_points=10_000,
                extraction_integrity_basis_points=10_000,
                fitness_and_currency_basis_points=10_000,
                independence_group="g",
                calculation_policy_version=CLAIM_SCORE_POLICY_VERSION,
            )
        )
        assert edge == 8_500
        assert policy.minor_conflict_penalty_basis_points == 0

    @pytest.mark.parametrize(
        "declared",
        [
            "design-03-ingestion-rag-evidence-1.0",  # the specification name
            "ccna-grounding-v2",  # a genuinely later policy
            "ccna-grounding",  # nearly right, still not the contract
            "totally-unknown-policy",
        ],
    )
    def test_any_other_policy_version_still_fails_closed(
        self, reader: PackFileReader, declared: str
    ) -> None:
        """No alias table: only exact equality with the public name passes."""
        pack = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY).pack
        first = pack.claims[0]
        rewritten = first.model_copy(
            update={
                "support": tuple(
                    support.model_copy(update={"calculation_policy_version": declared})
                    for support in first.support
                )
            }
        )
        drifted = pack.model_copy(update={"claims": (rewritten, *pack.claims[1:])})

        report = ObjectivePackValidator().validate(drifted)

        assert "calculation_policy_mismatch" in report.reason_codes

    def test_provenance_is_visible_and_distinct_from_the_policy_identifier(
        self, reader: PackFileReader
    ) -> None:
        pack = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY).pack
        report = ObjectivePackValidator().validate(pack)
        assert report.calculation_policy_version == "ccna-grounding-v1"
        assert report.calculation_algorithm_provenance == "design-03-ingestion-rag-evidence-1.0"
        assert report.calculation_policy_version != report.calculation_algorithm_provenance

    def test_the_specification_name_cannot_be_used_as_a_policy_identifier(self) -> None:
        """The one way the two could be confused, closed structurally."""
        with pytest.raises(ValueError, match="provenance, not a policy identifier"):
            ClaimEvidencePolicy(policy_version=CLAIM_SCORE_ALGORITHM_PROVENANCE)

    def test_a_grounding_result_carries_both_and_confuses_neither(
        self, reader: PackFileReader
    ) -> None:
        pack = load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY).pack
        result = ClaimEvidencePolicy().recompute_score(pack.claims[0])
        assert result.calculation_policy_version == CLAIM_SCORE_POLICY_VERSION
        assert result.algorithm_provenance == CLAIM_SCORE_ALGORITHM_PROVENANCE

    def test_no_frozen_fixture_byte_or_manifest_hash_changed(self) -> None:
        """The reconciliation happened entirely in code."""
        import hashlib
        import re

        manifest_path = PROJECT_ROOT / FIXTURE_DIRECTORY / "fixture-manifest.yaml"
        text = manifest_path.read_text(encoding="utf-8")
        declared = re.search(r"^manifest_self_sha256: ([0-9a-f]{64})\s*$", text, re.M)
        assert declared is not None
        assert declared.group(1) == MANIFEST_SELF_HASH

        substituted = text[: declared.start(1)] + "PENDING_COMPUTED_BELOW" + text[declared.end(1) :]
        assert hashlib.sha256(substituted.encode("utf-8")).hexdigest() == MANIFEST_SELF_HASH

        inventory = re.findall(
            r'^\s*-\s*\{path: "([^"]+)", sha256: ([0-9a-f]{64})\}\s*$', text, re.M
        )
        assert len(inventory) == 22
        root = PROJECT_ROOT / FIXTURE_DIRECTORY
        for relative_path, expected in inventory:
            actual = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
            assert actual == expected, relative_path


class TestFrozenSchemaProjection:
    """Decision 2: a projection that under-claims and never invents."""

    def test_every_gate_1_check_has_a_real_expectation_reference(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        result = _run_gate(reader, extractor, review_service)
        assert len(result.report.checks) == 26
        for check in result.report.checks:
            assert check.expected_ref == GATE_1_EXPECTATION_REFS[check.check_id]
            assert check.expected_ref

    def test_every_expectation_reference_resolves_to_a_real_target(self) -> None:
        """Not merely a file that exists: the fragment must name a real target.

        The earlier version of this test only checked that the path before
        ``#`` was a real file — it would have passed even while six of the
        26 references pointed at ``#/negative_cases/G1-NG-XX``, a JSON
        section the frozen ``evidence-report.json`` does not contain (the
        NG rows live in the same flat ``checks`` array as the GO rows).
        For a JSON/YAML reference the pointer is fully walked, and where it
        lands in an array the matched element's own ``check_id`` must equal
        the id being looked up — landing on *some* element at that position
        is not the same as landing on the element that actually claims to
        be that check. For a Markdown reference, GitHub-flavored Markdown
        generates no real anchor for a table cell, so the fragment must
        appear as an unambiguous table row instead: exactly one occurrence.
        """
        assert set(GATE_1_EXPECTATION_REFS) == set(GateDefinition.gate_1().required_check_ids)
        for check_id, reference in GATE_1_EXPECTATION_REFS.items():
            _assert_reference_resolves(check_id, reference, root=PROJECT_ROOT)

    def test_no_placeholder_expectation_appears_anywhere(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        """The removed fallback, pinned so it cannot come back."""
        result = _run_gate(reader, extractor, review_service)
        view = frozen_schema_view(result.report)
        for rendered in view["checks"]:
            assert "no approved expectation" not in rendered["expected_ref"]
            assert rendered["expected_ref"] == GATE_1_EXPECTATION_REFS[rendered["check_id"]]

    def test_a_missing_expectation_reference_is_rejected(self) -> None:
        report = _minimal_report()
        assert report.checks[0].expected_ref is None
        with pytest.raises(ReportProjectionError, match="no expectation reference"):
            frozen_schema_view(report)

    def test_an_undefined_check_cannot_be_given_a_reference(self) -> None:
        with pytest.raises(KeyError, match="no expectation contract is defined"):
            GateDefinition.gate_1().expectation_ref("G9-NOT-A-ROW")

    @pytest.mark.parametrize(
        "status", [GateCheckStatus.BLOCKED, GateCheckStatus.NOT_RUN, GateCheckStatus.FAILED]
    )
    def test_blocked_and_not_run_required_checks_project_to_failure(
        self, status: GateCheckStatus
    ) -> None:
        assert STATUS_PROJECTION[status.value] == "fail"

    def test_every_non_passing_gate_status_projects_to_failure(self) -> None:
        for gate_status in GateStatus:
            expected = "pass" if gate_status is GateStatus.PASSED else "fail"
            assert STATUS_PROJECTION[gate_status.value] == expected

    def test_only_the_two_frozen_optional_checks_may_be_deferred(self) -> None:
        assert {
            "G3-RI-QWEN-01",
            "week-scale-retest-bank-comparison",
        } == FROZEN_DEFERRABLE_CHECK_IDS
        schema = json.loads(
            (PROJECT_ROOT / FIXTURE_DIRECTORY / "schemas" / "gate-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        allowlist = schema["$defs"]["check"]["allOf"][0]["then"]["properties"]["check_id"]["enum"]
        assert set(allowlist) == FROZEN_DEFERRABLE_CHECK_IDS

    def test_a_gate_1_check_may_never_report_deferred(self) -> None:
        """Two independent guards, because this is the tempting shortcut.

        The internal model refuses to construct the check at all, and the
        projection refuses to render one even if it somehow existed.
        """
        with pytest.raises(ValueError, match="may not report 'deferred'"):
            GateCheck(
                check_id="G1-GO-07",
                status=GateCheckStatus.DEFERRED,
                reason_code="pending_approval",
                expected_ref=GATE_1_EXPECTATION_REFS["G1-GO-07"],
            )

    def test_the_projection_refuses_a_deferral_outside_the_frozen_allowlist(self) -> None:
        report = _minimal_report()
        smuggled = report.checks[0].model_copy(
            update={
                "check_id": "G3-RI-QWEN-02",
                "status": GateCheckStatus.DEFERRED,
                "expected_ref": "docs/plans/ccna-mastery-micro-lab/LINCHPIN_TRACEABILITY.md",
            }
        )
        with pytest.raises(ReportProjectionError, match="permits only"):
            frozen_schema_view(report.model_copy(update={"checks": (smuggled,)}))

    def test_the_projected_report_validates_against_the_unchanged_schema(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        result = _run_gate(reader, extractor, review_service)
        schema_path = PROJECT_ROOT / FIXTURE_DIRECTORY / "schemas" / "gate-report.schema.json"
        before = schema_path.read_bytes()
        view = frozen_schema_view(result.report)
        assert validate_against_frozen_schema(view, schema_bytes=before) == ()
        assert schema_path.read_bytes() == before

    def test_the_internal_report_keeps_the_precise_statuses(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        """Nothing is lost — the collapse happens only in the projection."""
        _record_attestations(review_service, reader)
        result = _run_gate(reader, extractor, review_service)
        internal = {check.check_id: check.status for check in result.report.checks}
        assert internal["G1-GO-07"] is GateCheckStatus.BLOCKED
        assert internal["G1-NG-02"] is GateCheckStatus.FAILED
        assert internal["G1-FX-07"] is GateCheckStatus.PASSED
        assert result.report.status is GateStatus.FAILED

        view = frozen_schema_view(result.report)
        projected = {check["check_id"]: check["status"] for check in view["checks"]}
        assert projected["G1-GO-07"] == "fail"
        assert projected["G1-NG-02"] == "fail"
        assert projected["G1-FX-07"] == "pass"

    def test_run_id_stays_internal_and_never_enters_the_frozen_view(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        result = _run_gate(reader, extractor, review_service)
        assert result.report.run_id == "test-run"
        assert "run_id" not in frozen_schema_view(result.report)

    def test_projecting_a_report_writes_nothing(
        self, reader: PackFileReader, extractor: LocalFixtureExtractor, review_service
    ) -> None:  # type: ignore[no-untyped-def]
        """An ordinary report command cannot mutate expected artifacts."""
        import hashlib

        expected_root = PROJECT_ROOT / FIXTURE_DIRECTORY / "expected"
        schema_root = PROJECT_ROOT / FIXTURE_DIRECTORY / "schemas"
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted([*expected_root.iterdir(), *schema_root.iterdir()])
        }
        result = _run_gate(reader, extractor, review_service)
        frozen_schema_view(result.report)
        after = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted([*expected_root.iterdir(), *schema_root.iterdir()])
        }
        assert before == after


def _assert_reference_resolves(check_id: str, reference: str, *, root: Path) -> None:
    relative_path, _, fragment = reference.partition("#")
    path = root / relative_path
    assert path.is_file(), f"{check_id} -> {reference}: {relative_path} does not exist"

    if path.suffix == ".md":
        if fragment:
            text = path.read_text(encoding="utf-8")
            occurrences = text.count(f"| {fragment} |")
            assert occurrences == 1, (
                f"{check_id} -> {reference}: expected exactly one unambiguous table "
                f"row for {fragment!r} in {relative_path}, found {occurrences}"
            )
        return

    if path.suffix in (".yaml", ".yml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    elif path.suffix == ".json":
        document = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise AssertionError(f"{check_id} -> {reference}: unsupported target type {path.suffix!r}")

    if not fragment:
        return
    node: Any = document
    for segment in (part for part in fragment.split("/") if part):
        if isinstance(node, dict):
            assert segment in node, f"{check_id} -> {reference}: no key {segment!r} at this level"
            node = node[segment]
        elif isinstance(node, list):
            matches = [
                item for item in node if isinstance(item, dict) and item.get("check_id") == segment
            ]
            assert len(matches) == 1, (
                f"{check_id} -> {reference}: expected exactly one array element with "
                f"check_id={segment!r}, found {len(matches)}"
            )
            node = matches[0]
        else:
            raise AssertionError(
                f"{check_id} -> {reference}: cannot descend into {segment!r}, not a container"
            )
    if isinstance(node, dict) and "check_id" in node:
        assert node["check_id"] == check_id, (
            f"{check_id} -> {reference}: resolved element's own check_id is "
            f"{node['check_id']!r}, not {check_id!r}"
        )
