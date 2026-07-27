"""Report publication, filesystem safety, runner reentrancy, and CLI durability.

Covers red items 27–33 and 36.

Reproduced defects:

- A reused ``attempt_id`` silently replaced a previous run's evidence.
- The destination directory was created *before* the containment check, so
  a refused write still left a directory outside the observed root.
- ``EvidenceGateRunner`` held ``_pack_directory`` as per-run mutable state,
  so one runner shared between two packs could resolve source paths against
  the wrong pack directory.
- ``SQLiteEvidenceReviewRepository.open`` treated a SQLite memory URI as a
  literal filename, creating ``file:memdb1?mode=memory&cache=shared`` in the
  worktree — a review probe actually did this.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from personal_lms.labs.ccna_mastery.gates import (
    GateArtifactPaths,
    GateCheck,
    GateCheckStatus,
    GateDefinition,
    GateId,
    GateReport,
    GoldenArtifactGuard,
    GoldenWriteRefusedError,
    ObservedGateReportStore,
)

from ..objective_packs._helpers import make_manifest, make_pack, write_pack_directory

STARTED_AT = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=1)


def report(run_id: str = "run-1") -> GateReport:
    return GateReport(
        gate_id=GateId.GATE_1,
        run_id=run_id,
        code_revision="rev",
        fixture_manifest_hash="a" * 64,
        fixture_authority="reviewed",
        checks=tuple(
            GateCheck(check_id=cid, status=GateCheckStatus.PASSED, reason_code="ok")
            for cid in GateDefinition.gate_1().required_check_ids
        ),
        started_at=STARTED_AT,
        finished_at=FINISHED_AT,
    )


@pytest.fixture
def paths(tmp_path: Path) -> GateArtifactPaths:
    resolved = GateArtifactPaths.for_project_root(tmp_path)
    resolved.observed_root.mkdir(parents=True, exist_ok=True)
    return resolved


@pytest.fixture
def store(paths: GateArtifactPaths) -> ObservedGateReportStore:
    return ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths))


class TestStoredArtifactValidates:
    def test_the_stored_report_round_trips_through_its_model(
        self, store: ObservedGateReportStore
    ) -> None:
        destination = store.write(report())

        payload = json.loads(destination.read_text(encoding="utf-8"))
        restored = GateReport.model_validate(
            {key: value for key, value in payload.items() if key in GateReport.model_fields}
        )

        assert restored.content_hash == report().content_hash

    def test_the_stored_report_carries_the_definition_version(
        self, store: ObservedGateReportStore
    ) -> None:
        destination = store.write(report())

        payload = json.loads(destination.read_text(encoding="utf-8"))

        assert payload["definition_version"] == GateDefinition.gate_1().definition_version


class TestImmutablePublication:
    def test_reusing_an_attempt_id_is_refused(self, store: ObservedGateReportStore) -> None:
        store.write(report(), attempt_id="attempt-2")

        with pytest.raises(GoldenWriteRefusedError, match="already exists"):
            store.write(report(), attempt_id="attempt-2")

    def test_rewriting_a_run_without_an_attempt_id_is_refused(
        self, store: ObservedGateReportStore
    ) -> None:
        store.write(report())

        with pytest.raises(GoldenWriteRefusedError):
            store.write(report())

    def test_a_distinct_attempt_id_is_accepted(self, store: ObservedGateReportStore) -> None:
        store.write(report())

        assert store.write(report(), attempt_id="attempt-2").exists()

    def test_two_writers_racing_one_path_do_not_both_succeed(
        self, store: ObservedGateReportStore, paths: GateArtifactPaths
    ) -> None:
        """Create-if-absent publication, not last-writer-wins."""
        first = store.write(report())
        original = first.read_text(encoding="utf-8")

        second = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths))
        with pytest.raises(GoldenWriteRefusedError):
            second.write(report())

        assert first.read_text(encoding="utf-8") == original


class TestContainmentBeforeSideEffects:
    def test_a_refused_destination_creates_no_directory(
        self, store: ObservedGateReportStore, tmp_path: Path
    ) -> None:
        """Containment must be decided before anything is created."""
        outside = tmp_path / "outside-the-observed-root"

        with pytest.raises(GoldenWriteRefusedError):
            store.write(report(), destination_override=outside / "report.json")

        assert not outside.exists()

    def test_a_traversing_run_id_creates_no_directory(
        self, store: ObservedGateReportStore, paths: GateArtifactPaths
    ) -> None:
        with pytest.raises(GoldenWriteRefusedError):
            store.write(report(run_id="../../escaped"))

        assert not (paths.observed_root.parent.parent / "escaped").exists()

    def test_a_symlinked_run_directory_escaping_var_is_refused(
        self, store: ObservedGateReportStore, paths: GateArtifactPaths, tmp_path: Path
    ) -> None:
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (paths.observed_root / "escaping").symlink_to(outside, target_is_directory=True)

        with pytest.raises(GoldenWriteRefusedError):
            store.write(report(run_id="escaping"))

        assert list(outside.iterdir()) == []

    def test_a_hardlinked_destination_does_not_leak_into_the_golden_tree(
        self, store: ObservedGateReportStore, paths: GateArtifactPaths
    ) -> None:
        paths.expected_root.mkdir(parents=True, exist_ok=True)
        golden = paths.expected_root / "evidence-report.json"
        golden.write_text('{"approved": true}', encoding="utf-8")

        run_directory = paths.observed_root / "run-1"
        run_directory.mkdir(parents=True, exist_ok=True)
        (run_directory / "gate-1.json").hardlink_to(golden)

        with pytest.raises(GoldenWriteRefusedError):
            store.write(report())

        assert golden.read_text(encoding="utf-8") == '{"approved": true}'


class TestRunnerReentrancy:
    def test_the_runner_holds_no_per_run_pack_directory(self) -> None:
        from personal_lms.labs.ccna_mastery.wiring import EvidenceGateRunner

        assert not hasattr(EvidenceGateRunner, "_pack_directory")

    def test_one_runner_serves_two_packs_without_cross_contamination(self, tmp_path: Path) -> None:
        from personal_lms.evidence_review.service import EvidenceReviewService
        from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
        from personal_lms.labs.ccna_mastery.wiring import build_ccna_mastery_use_case
        from personal_lms.objective_packs.loader import ObjectivePackLoader, PackFileReader

        root = tmp_path / "packs"
        root.mkdir()
        write_pack_directory(root, directory_name="pack-a")
        write_pack_directory(
            root,
            directory_name="pack-b",
            pack=make_pack(manifest=make_manifest(pack_id="pack-b-id")),
        )

        repository = SQLiteEvidenceReviewRepository.open(":memory:")
        repository.initialize_schema()
        runner = build_ccna_mastery_use_case(
            loader=ObjectivePackLoader(PackFileReader(roots=[root])),
            review_service=EvidenceReviewService(repository),
            code_revision="test-revision",
        )

        first = runner.run(pack_directory="pack-a", run_id="r1")
        second = runner.run(pack_directory="pack-b", run_id="r2")

        assert first.pack is not None
        assert second.pack is not None
        assert first.pack.manifest.pack_id != second.pack.manifest.pack_id


class TestReviewStorePersistence:
    @pytest.mark.parametrize(
        "candidate",
        [
            ":memory:",
            "file:memdb1?mode=memory&cache=shared",
            "file::memory:?cache=shared",
            "",
            "   ",
        ],
    )
    def test_a_non_persistent_store_path_is_refused_at_the_cli_boundary(
        self, candidate: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """And refusing must not create a file named after the URI."""
        from personal_lms.labs.ccna_mastery.cli import resolve_persistent_review_database

        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValueError):
            resolve_persistent_review_database(candidate)

        assert list(tmp_path.iterdir()) == []

    def test_a_directory_is_refused(self, tmp_path: Path) -> None:
        from personal_lms.labs.ccna_mastery.cli import resolve_persistent_review_database

        with pytest.raises(ValueError, match="directory"):
            resolve_persistent_review_database(str(tmp_path))

    def test_an_absent_file_is_refused_for_readers(self, tmp_path: Path) -> None:
        from personal_lms.labs.ccna_mastery.cli import resolve_persistent_review_database

        with pytest.raises(ValueError, match="does not exist"):
            resolve_persistent_review_database(str(tmp_path / "never-created.sqlite3"))

    def test_an_absent_file_is_allowed_for_the_writer(self, tmp_path: Path) -> None:
        """The reviewer command creates the store on its first decision."""
        from personal_lms.labs.ccna_mastery.cli import resolve_persistent_review_database

        target = tmp_path / "new-review.sqlite3"

        assert resolve_persistent_review_database(str(target), must_exist=False) == target.resolve()
        assert not target.exists()

    def test_a_real_database_file_is_accepted(self, tmp_path: Path) -> None:
        from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
        from personal_lms.labs.ccna_mastery.cli import resolve_persistent_review_database

        database = tmp_path / "review.sqlite3"
        repository = SQLiteEvidenceReviewRepository.open(str(database))
        repository.initialize_schema()
        repository.close()

        assert resolve_persistent_review_database(str(database)) == database.resolve()

    def test_in_memory_composition_remains_available_for_unit_tests(self) -> None:
        """The prohibition is a CLI boundary rule, not a repository rule."""
        from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository

        repository = SQLiteEvidenceReviewRepository.open(":memory:")
        repository.initialize_schema()
        repository.close()


class TestManifestAliasAuthority:
    def test_two_sources_cannot_bind_to_one_manifest_entry(self, tmp_path: Path) -> None:
        """Shared bytes with differing authority is ambiguous, not convenient."""
        from personal_lms.domain.objective_packs import TrustStatus
        from personal_lms.objective_packs.errors import PackManifestError
        from personal_lms.objective_packs.loader import ObjectivePackLoader, PackFileReader

        from ..objective_packs._helpers import PDF_BYTES, make_source

        root = tmp_path / "packs"
        root.mkdir()
        trusted = make_source(source_id="src-a", payload=PDF_BYTES)
        untrusted = make_source(
            source_id="src-b",
            payload=PDF_BYTES,
            trust_status=TrustStatus.UNTRUSTED,
        )
        write_pack_directory(root, pack=make_pack(sources=(trusted, untrusted)))

        loader = ObjectivePackLoader(PackFileReader(roots=[root]))

        with pytest.raises(PackManifestError, match="ambiguous|more than one"):
            loader.load(pack_directory="pack-a")
