"""Extraction proof and CLI store composition (review findings #21, #24).

Two defects:

- ``G1-FX-02`` passed merely because a ``LocalFixtureExtractor`` object had
  been constructed. Nothing was extracted, nothing was verified, and with
  no PDF parser nothing *could* be — yet the check reported ``passed``.
- The gate CLI silently substituted an in-memory review database, so the
  reviewer command and the gate command never shared a store. Every
  approval recorded by the reviewer was invisible to the gate, and the
  gate's "no approvals" result looked like a fixture problem rather than a
  wiring bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personal_lms.labs.ccna_mastery.gates import GateCheckStatus

from ..objective_packs._helpers import (
    PNG_HEADER_ONLY_BYTES,
    make_image_region,
    make_pack,
    make_source,
    write_pack_directory,
)


@pytest.fixture
def pack_root(tmp_path: Path) -> Path:
    root = tmp_path / "packs"
    root.mkdir()
    write_pack_directory(root)
    return root


class TestExtractionProof:
    def test_configuring_an_extractor_does_not_pass_the_extraction_check(
        self, pack_root: Path, tmp_path: Path
    ) -> None:
        """A constructed object is not an extraction result."""
        from personal_lms.evidence_review.service import EvidenceReviewService
        from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
        from personal_lms.extraction.local_fixture import LocalFixtureExtractor
        from personal_lms.labs.ccna_mastery.wiring import build_ccna_mastery_use_case
        from personal_lms.objective_packs.loader import ObjectivePackLoader, PackFileReader

        reader = PackFileReader(roots=[pack_root])
        repository = SQLiteEvidenceReviewRepository.open(":memory:")
        repository.initialize_schema()
        runner = build_ccna_mastery_use_case(
            loader=ObjectivePackLoader(reader),
            review_service=EvidenceReviewService(repository),
            extractor=LocalFixtureExtractor(reader),
        )

        result = runner.run(pack_directory="pack-a", run_id="run-1")
        statuses = {c.check_id: c.status for c in result.report.checks}

        assert statuses["G1-FX-02"] is not GateCheckStatus.PASSED

    def test_a_pdf_region_reports_blocked_while_no_parser_is_approved(
        self, pack_root: Path
    ) -> None:
        from personal_lms.evidence_review.service import EvidenceReviewService
        from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
        from personal_lms.extraction.local_fixture import LocalFixtureExtractor
        from personal_lms.labs.ccna_mastery.wiring import build_ccna_mastery_use_case
        from personal_lms.objective_packs.loader import ObjectivePackLoader, PackFileReader

        reader = PackFileReader(roots=[pack_root])
        repository = SQLiteEvidenceReviewRepository.open(":memory:")
        repository.initialize_schema()
        runner = build_ccna_mastery_use_case(
            loader=ObjectivePackLoader(reader),
            review_service=EvidenceReviewService(repository),
            extractor=LocalFixtureExtractor(reader),
        )

        result = runner.run(pack_directory="pack-a", run_id="run-1")
        extraction = next(c for c in result.report.checks if c.check_id == "G1-FX-02")

        assert extraction.status in (GateCheckStatus.BLOCKED, GateCheckStatus.NOT_RUN)
        assert extraction.reason_code in ("pdf_parser_unavailable", "extractor_not_configured")

    def test_a_header_only_pseudo_image_is_rejected(self, tmp_path: Path) -> None:
        """A signature plus IHDR is not a real image."""
        from personal_lms.extraction.artifacts import ExtractionOutcome
        from personal_lms.extraction.local_fixture import LocalFixtureExtractor
        from personal_lms.objective_packs.loader import PackFileReader

        root = tmp_path / "sources"
        root.mkdir()
        (root / "header-only.png").write_bytes(PNG_HEADER_ONLY_BYTES)
        extractor = LocalFixtureExtractor(PackFileReader(roots=[tmp_path]))
        artifact = make_source(
            source_id="src-png", payload=PNG_HEADER_ONLY_BYTES, media_type="image/png"
        )

        result = extractor.extract_region(
            make_image_region(image_payload=PNG_HEADER_ONLY_BYTES),
            artifact,
            relative_path="sources/header-only.png",
        )

        assert result.outcome is ExtractionOutcome.MALFORMED_SOURCE

    def test_every_source_must_bind_to_exactly_one_manifest_record(self, pack_root: Path) -> None:
        from personal_lms.objective_packs.loader import ObjectivePackLoader, PackFileReader

        loader = ObjectivePackLoader(PackFileReader(roots=[pack_root]))
        result = loader.load(pack_directory="pack-a")

        assert result.source_manifest_bindings
        for artifact in result.pack.source_artifacts:
            binding = result.source_manifest_bindings[artifact.source_id]
            assert binding.sha256 == artifact.sha256
            assert binding.size_bytes == artifact.size_bytes

    def test_a_source_with_no_manifest_binding_is_refused(self, tmp_path: Path) -> None:
        from personal_lms.objective_packs.errors import PackManifestError
        from personal_lms.objective_packs.loader import ObjectivePackLoader, PackFileReader

        root = tmp_path / "packs"
        root.mkdir()
        orphan = make_source(source_id="src-unbound", payload=b"%PDF-1.7\norphan\n")
        write_pack_directory(root, pack=make_pack(sources=(orphan,)))

        loader = ObjectivePackLoader(PackFileReader(roots=[root]))

        with pytest.raises(PackManifestError, match="manifest"):
            loader.load(pack_directory="pack-a")


class TestCliPersistentStore:
    def test_the_gate_command_fails_closed_when_no_store_is_configured(
        self, pack_root: Path
    ) -> None:
        """Silently substituting :memory: hid a real wiring bug.

        Now a required argument, so the refusal happens at parse time —
        the strongest available form of failing closed.
        """
        from personal_lms.cli import main

        with pytest.raises(SystemExit) as excinfo:
            main(
                [
                    "ccna-lab",
                    "gate",
                    "evidence",
                    "--pack-root",
                    str(pack_root),
                    "--pack-directory",
                    "pack-a",
                ]
            )

        assert excinfo.value.code == 2

    def test_the_gate_command_fails_closed_when_the_store_is_absent(
        self, pack_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from personal_lms.cli import main

        exit_code = main(
            [
                "ccna-lab",
                "gate",
                "evidence",
                "--pack-root",
                str(pack_root),
                "--pack-directory",
                "pack-a",
                "--review-database",
                str(tmp_path / "never-created.sqlite3"),
            ]
        )

        assert exit_code == 2
        assert "does not exist" in capsys.readouterr().out

    def test_the_reviewer_and_gate_commands_share_one_store(
        self, pack_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An approval recorded by the reviewer must be visible to the gate."""
        from personal_lms.cli import main

        database = tmp_path / "review.sqlite3"
        assert (
            main(
                [
                    "ccna-lab",
                    "evidence",
                    "approve-region",
                    "--pack-root",
                    str(pack_root),
                    "--pack-directory",
                    "pack-a",
                    "--evidence-id",
                    "ev-text-1",
                    "--reviewer-id",
                    "reviewer-1",
                    "--outcome",
                    "approved",
                    "--reason",
                    "Synthetic draft fixture, test only.",
                    "--review-database",
                    str(database),
                ]
            )
            == 0
        )
        capsys.readouterr()

        main(
            [
                "ccna-lab",
                "gate",
                "evidence",
                "--pack-root",
                str(pack_root),
                "--pack-directory",
                "pack-a",
                "--run-id",
                "run-1",
                "--project-root",
                str(tmp_path),
                "--review-database",
                str(database),
            ]
        )

        assert "visual_review_pending" not in capsys.readouterr().out

    def test_the_report_records_a_real_code_revision(self, pack_root: Path, tmp_path: Path) -> None:
        """ "unversioned" silently written into a gate report is not provenance."""
        from personal_lms.labs.ccna_mastery.wiring import resolve_code_revision

        revision = resolve_code_revision()

        assert revision
        assert revision != "unversioned"
