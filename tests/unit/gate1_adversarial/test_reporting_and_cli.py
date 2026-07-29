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

import argparse
import hashlib
import json
import uuid
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
    provenance_path_for_primary,
)
from personal_lms.labs.ccna_mastery.report_schema import (
    ReportProvenanceError,
    frozen_schema_view,
    report_from_bound_provenance,
    validate_against_frozen_schema,
)
from personal_lms.objective_packs.hashing import canonical_json

from ..objective_packs._helpers import make_manifest, make_pack, write_pack_directory

STARTED_AT = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=1)


def _directory_snapshot(directory: Path) -> tuple[Path, ...]:
    """Stable no-write view that also handles a not-yet-created golden root."""
    return tuple(sorted(directory.iterdir())) if directory.is_dir() else ()


def report(run_id: str = "run-1") -> GateReport:
    definition = GateDefinition.gate_1()
    return GateReport(
        gate_id=GateId.GATE_1,
        run_id=run_id,
        code_revision="0123456789abcdef",
        fixture_manifest_hash="a" * 64,
        fixture_authority="reviewed",
        checks=definition.bind_expectations(
            tuple(
                GateCheck(check_id=cid, status=GateCheckStatus.PASSED, reason_code="ok")
                for cid in definition.required_check_ids
            )
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
    def test_the_primary_is_the_literal_frozen_schema_view(
        self, store: ObservedGateReportStore
    ) -> None:
        destination = store.write(report())

        payload = json.loads(destination.read_text(encoding="utf-8"))
        schema = (
            Path(__file__).resolve().parents[3]
            / "tests"
            / "linchpin"
            / "schemas"
            / "gate-report.schema.json"
        ).read_bytes()

        assert payload == frozen_schema_view(report())
        assert validate_against_frozen_schema(payload, schema_bytes=schema) == ()

    def test_the_sidecar_preserves_rich_provenance_and_binds_the_primary(
        self, store: ObservedGateReportStore
    ) -> None:
        destination = store.write(report())
        primary_bytes = destination.read_bytes()
        sidecar = provenance_path_for_primary(destination)

        restored = report_from_bound_provenance(
            primary_bytes=primary_bytes,
            provenance_bytes=sidecar.read_bytes(),
        )
        sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))

        assert restored.to_canonical_json() == report().to_canonical_json()
        assert sidecar_payload["primary_sha256"] == hashlib.sha256(primary_bytes).hexdigest()
        assert (
            sidecar_payload["report"]["definition_version"]
            == GateDefinition.gate_1().definition_version
        )


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

        primary = store.write(report(), attempt_id="attempt-2")

        assert primary.exists()
        assert provenance_path_for_primary(primary).exists()

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


def _valid_passing_report(**overrides: object) -> GateReport:
    """A report that is PASSED, reviewed, and satisfies the frozen schema.

    Distinct from this module's ``report()`` helper: that one leaves every
    check's ``expected_ref`` unset and uses a 3-character ``code_revision``,
    both of which the *new* stored-report and golden-acceptance validation
    this class covers now correctly refuses.
    """
    definition = GateDefinition.gate_1()
    checks = definition.bind_expectations(
        tuple(
            GateCheck(check_id=check_id, status=GateCheckStatus.PASSED, reason_code="ok")
            for check_id in definition.required_check_ids
        )
    )
    defaults: dict[str, object] = {
        "gate_id": GateId.GATE_1,
        "run_id": "run-1",
        "code_revision": "0123456789abcdef",
        "fixture_manifest_hash": "a" * 64,
        "fixture_authority": "reviewed",
        "checks": checks,
        "started_at": STARTED_AT,
        "finished_at": FINISHED_AT,
    }
    return GateReport(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestCliPathTraversalIsRefused:
    """``run_id``/``attempt_id`` are caller-controlled strings that reach a
    path join. An earlier revision joined them with no validation at all,
    so ``--run-id ../../../../etc`` would have the report and
    golden-acceptance commands attempt to read or write anywhere on disk.
    """

    def test_a_traversing_run_id_is_refused_before_any_read(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _observed_report_path
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        args = argparse.Namespace(
            project_root=str(executing_checkout_root()),
            run_id="../../../../tmp/escaped",
            attempt_id=None,
        )

        with pytest.raises(GoldenWriteRefusedError):
            _observed_report_path(args)

    def test_a_run_id_containing_a_separator_is_refused(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _observed_report_path
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        args = argparse.Namespace(
            project_root=str(executing_checkout_root()), run_id="a/b", attempt_id=None
        )

        with pytest.raises(GoldenWriteRefusedError):
            _observed_report_path(args)

    def test_an_absolute_run_id_is_refused(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _observed_report_path
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        args = argparse.Namespace(
            project_root=str(executing_checkout_root()), run_id="/etc/passwd", attempt_id=None
        )

        with pytest.raises(GoldenWriteRefusedError):
            _observed_report_path(args)

    def test_a_traversing_attempt_id_is_refused(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _observed_report_path
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        args = argparse.Namespace(
            project_root=str(executing_checkout_root()), run_id="run-1", attempt_id=".."
        )

        with pytest.raises(GoldenWriteRefusedError):
            _observed_report_path(args)

    def test_the_gate_report_command_refuses_a_traversing_run_id(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _gate_report_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        args = argparse.Namespace(
            project_root=str(executing_checkout_root()),
            run_id="../../../../tmp/escaped",
            attempt_id=None,
            schema="tests/linchpin/schemas/gate-report.schema.json",
        )

        assert _gate_report_command(args) == 2

    def test_the_accept_goldens_command_refuses_a_traversing_run_id_before_any_write(
        self,
    ) -> None:
        from personal_lms.labs.ccna_mastery.cli import _accept_goldens_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        golden_root = GateArtifactPaths.for_project_root(executing_checkout_root()).expected_root
        before = _directory_snapshot(golden_root)

        args = argparse.Namespace(
            project_root=str(executing_checkout_root()),
            run_id="../../../../tmp/escaped",
            attempt_id=None,
            reviewer_id="alan",
            expect_report_sha256="0" * 64,
            expect_manifest_sha256="0" * 64,
            expect_code_revision="rev",
            i_am_authorized_to_accept_goldens=True,
            schema="tests/linchpin/schemas/gate-report.schema.json",
        )

        assert _accept_goldens_command(args) == 2
        assert _directory_snapshot(golden_root) == before

    def test_a_symlinked_run_directory_escaping_var_is_refused_by_the_cli(
        self, tmp_path: Path
    ) -> None:
        """The CLI read path must resolve and re-check containment, not just
        validate the ``run_id`` string. A directory named by a perfectly
        safe single-segment ``run_id`` can still be a symlink to anywhere.
        """
        from personal_lms.labs.ccna_mastery.cli import _observed_report_path
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        gates_root = root / "var" / "ccna-mastery" / "gates"
        gates_root.mkdir(parents=True, exist_ok=True)
        run_id = f"adversarial-symlink-escape-{uuid.uuid4().hex}"
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        link = gates_root / run_id
        link.symlink_to(outside, target_is_directory=True)
        try:
            args = argparse.Namespace(project_root=str(root), run_id=run_id, attempt_id=None)

            with pytest.raises(GoldenWriteRefusedError):
                _observed_report_path(args)

            assert list(outside.iterdir()) == []
        finally:
            link.unlink()


@pytest.fixture
def published_bundle(store: ObservedGateReportStore) -> tuple[Path, Path]:
    primary = store.write(_valid_passing_report())
    return primary, provenance_path_for_primary(primary)


class TestBoundProvenanceFailsClosed:
    def test_a_well_formed_bundle_restores_the_exact_rich_report(
        self, published_bundle: tuple[Path, Path]
    ) -> None:
        primary, sidecar = published_bundle

        restored = report_from_bound_provenance(
            primary_bytes=primary.read_bytes(), provenance_bytes=sidecar.read_bytes()
        )

        assert restored.to_canonical_json() == _valid_passing_report().to_canonical_json()

    def test_a_tampered_primary_is_rejected(self, published_bundle: tuple[Path, Path]) -> None:
        primary, sidecar = published_bundle
        payload = json.loads(primary.read_text(encoding="utf-8"))
        payload["code_revision"] = "fedcba9876543210"
        tampered = canonical_json(payload).encode("utf-8")

        with pytest.raises(ReportProvenanceError, match="does not bind"):
            report_from_bound_provenance(
                primary_bytes=tampered, provenance_bytes=sidecar.read_bytes()
            )

    def test_a_tampered_sidecar_hash_is_rejected(self, published_bundle: tuple[Path, Path]) -> None:
        primary, sidecar = published_bundle
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        payload["primary_sha256"] = "0" * 64

        with pytest.raises(ReportProvenanceError, match="does not bind"):
            report_from_bound_provenance(
                primary_bytes=primary.read_bytes(),
                provenance_bytes=canonical_json(payload).encode("utf-8"),
            )

    def test_tampered_rich_authority_is_rejected(self, published_bundle: tuple[Path, Path]) -> None:
        primary, sidecar = published_bundle
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        payload["report"]["fixture_authority"] = "draft_for_human_review"

        with pytest.raises(ReportProvenanceError, match="exact canonical form"):
            report_from_bound_provenance(
                primary_bytes=primary.read_bytes(),
                provenance_bytes=canonical_json(payload).encode("utf-8"),
            )

    def test_a_spoofed_expectation_reference_is_rejected(
        self, published_bundle: tuple[Path, Path]
    ) -> None:
        primary, sidecar = published_bundle
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        payload["report"]["checks"][0]["expected_ref"] = "docs/spoofed.md#nowhere"

        with pytest.raises(ReportProvenanceError, match="not the trusted"):
            report_from_bound_provenance(
                primary_bytes=primary.read_bytes(),
                provenance_bytes=canonical_json(payload).encode("utf-8"),
            )

    def test_noncanonical_primary_bytes_are_rejected_even_with_a_matching_hash(
        self, published_bundle: tuple[Path, Path]
    ) -> None:
        primary, sidecar = published_bundle
        noncanonical = json.dumps(json.loads(primary.read_text(encoding="utf-8")), indent=2).encode(
            "utf-8"
        )
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        payload["primary_sha256"] = hashlib.sha256(noncanonical).hexdigest()

        with pytest.raises(ReportProvenanceError, match="exact canonical frozen-schema view"):
            report_from_bound_provenance(
                primary_bytes=noncanonical,
                provenance_bytes=canonical_json(payload).encode("utf-8"),
            )


class TestGateReportCommandNeverPrintsOkForACorruptedStoredReport:
    @staticmethod
    def _args(*, root: Path, run_id: str) -> argparse.Namespace:
        return argparse.Namespace(
            project_root=str(root),
            run_id=run_id,
            attempt_id=None,
            schema="tests/linchpin/schemas/gate-report.schema.json",
        )

    def test_a_schema_invalid_primary_returns_nonzero_and_prints_no_ok(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from personal_lms.labs.ccna_mastery.cli import _gate_report_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        run_id = f"adversarial-corrupted-report-{uuid.uuid4().hex}"
        paths = GateArtifactPaths.for_project_root(root)
        primary = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths)).write(
            _valid_passing_report(run_id=run_id)
        )
        payload = json.loads(primary.read_text(encoding="utf-8"))
        del payload["elapsed_seconds"]
        primary.write_text(canonical_json(payload), encoding="utf-8")
        try:
            exit_code = _gate_report_command(self._args(root=root, run_id=run_id))

            assert exit_code != 0
            assert "OK" not in capsys.readouterr().out
        finally:
            for artifact in primary.parent.iterdir():
                artifact.unlink()
            primary.parent.rmdir()

    def test_a_missing_provenance_sidecar_returns_nonzero_and_prints_no_ok(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from personal_lms.labs.ccna_mastery.cli import _gate_report_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        run_id = f"adversarial-missing-provenance-{uuid.uuid4().hex}"
        paths = GateArtifactPaths.for_project_root(root)
        primary = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths)).write(
            _valid_passing_report(run_id=run_id)
        )
        provenance_path_for_primary(primary).unlink()
        try:
            exit_code = _gate_report_command(self._args(root=root, run_id=run_id))

            assert exit_code != 0
            assert "OK" not in capsys.readouterr().out
        finally:
            primary.unlink()
            primary.parent.rmdir()


class TestGoldenAcceptanceValidatesBeforeAnyWrite:
    """Golden acceptance must never copy an unvalidated artifact.

    Every scenario here writes a real observed report under the executing
    checkout's ``var/`` (gitignored, unique run ids) and drives the real
    ``_accept_goldens_command`` against the real project root — the only
    way to exercise its hardcoded ``require_executing_checkout`` binding.
    None of them may be a scenario that actually succeeds: this suite must
    never perform a real golden write, so every case here is one that
    ``_accept_goldens_command`` refuses, and each asserts the real
    external accepted-golden tree is untouched.
    """

    @staticmethod
    def _write_observed(report_obj: GateReport, *, executing_checkout_root: Path) -> Path:
        paths = GateArtifactPaths.for_project_root(executing_checkout_root)
        return ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths)).write(report_obj)

    @staticmethod
    def _args_for(destination: Path, *, project_root: str) -> argparse.Namespace:
        raw = destination.read_bytes()
        payload = json.loads(raw)
        run_id = destination.parent.name
        return argparse.Namespace(
            project_root=project_root,
            run_id=run_id,
            attempt_id=None,
            reviewer_id="alan",
            expect_report_sha256=hashlib.sha256(raw).hexdigest(),
            expect_manifest_sha256=payload["fixture_manifest_sha256"],
            expect_code_revision=payload["code_revision"],
            i_am_authorized_to_accept_goldens=True,
            schema="tests/linchpin/schemas/gate-report.schema.json",
        )

    def test_missing_required_checks_are_refused(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _accept_goldens_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        golden_root = GateArtifactPaths.for_project_root(root).expected_root
        before = _directory_snapshot(golden_root)

        run_id = f"adversarial-missing-checks-{uuid.uuid4().hex}"
        definition = GateDefinition.gate_1()
        incomplete = definition.bind_expectations(
            tuple(
                GateCheck(check_id=check_id, status=GateCheckStatus.PASSED, reason_code="ok")
                for check_id in definition.required_check_ids
                if check_id != "G1-GO-09"
            )
        )
        destination = self._write_observed(
            _valid_passing_report(run_id=run_id, checks=incomplete),
            executing_checkout_root=root,
        )

        exit_code = _accept_goldens_command(self._args_for(destination, project_root=str(root)))

        assert exit_code == 1
        assert _directory_snapshot(golden_root) == before

    def test_a_non_passed_status_is_refused(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _accept_goldens_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        golden_root = GateArtifactPaths.for_project_root(root).expected_root
        before = _directory_snapshot(golden_root)

        run_id = f"adversarial-not-passed-{uuid.uuid4().hex}"
        definition = GateDefinition.gate_1()
        failing = definition.bind_expectations(
            tuple(
                GateCheck(
                    check_id=check_id,
                    status=(
                        GateCheckStatus.FAILED if check_id == "G1-GO-01" else GateCheckStatus.PASSED
                    ),
                    reason_code="ok",
                )
                for check_id in definition.required_check_ids
            )
        )
        destination = self._write_observed(
            _valid_passing_report(run_id=run_id, checks=failing), executing_checkout_root=root
        )

        exit_code = _accept_goldens_command(self._args_for(destination, project_root=str(root)))

        assert exit_code == 1
        assert _directory_snapshot(golden_root) == before

    def test_unapproved_fixture_authority_is_refused(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _accept_goldens_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        golden_root = GateArtifactPaths.for_project_root(root).expected_root
        before = _directory_snapshot(golden_root)

        run_id = f"adversarial-unapproved-authority-{uuid.uuid4().hex}"
        destination = self._write_observed(
            _valid_passing_report(run_id=run_id, fixture_authority="draft_for_human_review"),
            executing_checkout_root=root,
        )

        exit_code = _accept_goldens_command(self._args_for(destination, project_root=str(root)))

        assert exit_code == 1
        assert _directory_snapshot(golden_root) == before

    def test_a_mismatched_expectation_reference_is_refused(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _accept_goldens_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        golden_root = GateArtifactPaths.for_project_root(root).expected_root
        before = _directory_snapshot(golden_root)

        run_id = f"adversarial-spoofed-ref-{uuid.uuid4().hex}"
        destination = self._write_observed(
            _valid_passing_report(run_id=run_id), executing_checkout_root=root
        )
        # GateReport itself does not validate expected_ref content -- only
        # trusted publication/provenance verification does -- so the attack this
        # guards against is exactly this: a hand-edited stored file whose
        # in-memory reconstruction would otherwise look well-formed.
        raw_payload = json.loads(destination.read_text(encoding="utf-8"))
        raw_payload["checks"][0]["expected_ref"] = "docs/spoofed.md#nowhere"
        destination.write_text(json.dumps(raw_payload), encoding="utf-8")

        exit_code = _accept_goldens_command(self._args_for(destination, project_root=str(root)))

        assert exit_code == 1
        assert _directory_snapshot(golden_root) == before

    def test_a_frozen_schema_violation_is_refused_even_when_the_internal_model_accepts_it(
        self,
    ) -> None:
        """``code_revision`` needs only 1 char internally but 7 in the frozen
        schema — a real gap between the two contracts this check closes."""
        from personal_lms.labs.ccna_mastery.cli import _accept_goldens_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        golden_root = GateArtifactPaths.for_project_root(root).expected_root
        before = _directory_snapshot(golden_root)

        run_id = f"adversarial-short-revision-{uuid.uuid4().hex}"
        destination = self._write_observed(
            _valid_passing_report(run_id=run_id, code_revision="ab"), executing_checkout_root=root
        )

        exit_code = _accept_goldens_command(self._args_for(destination, project_root=str(root)))

        assert exit_code == 1
        assert _directory_snapshot(golden_root) == before

    def test_an_unauthorized_call_is_refused_before_any_file_access(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _accept_goldens_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        golden_root = GateArtifactPaths.for_project_root(root).expected_root
        before = _directory_snapshot(golden_root)

        args = argparse.Namespace(
            project_root=str(root),
            run_id="does-not-matter",
            attempt_id=None,
            reviewer_id="alan",
            expect_report_sha256="0" * 64,
            expect_manifest_sha256="0" * 64,
            expect_code_revision="rev",
            i_am_authorized_to_accept_goldens=False,
            schema="tests/linchpin/schemas/gate-report.schema.json",
        )

        exit_code = _accept_goldens_command(args)

        assert exit_code == 2
        assert _directory_snapshot(golden_root) == before

    def test_a_missing_provenance_sidecar_is_refused_before_any_golden_write(self) -> None:
        from personal_lms.labs.ccna_mastery.cli import _accept_goldens_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        golden_root = GateArtifactPaths.for_project_root(root).expected_root
        before = _directory_snapshot(golden_root)
        run_id = f"adversarial-missing-sidecar-{uuid.uuid4().hex}"
        observed = self._write_observed(
            _valid_passing_report(run_id=run_id), executing_checkout_root=root
        )
        provenance_path_for_primary(observed).unlink()

        exit_code = _accept_goldens_command(self._args_for(observed, project_root=str(root)))

        assert exit_code == 1
        assert _directory_snapshot(golden_root) == before

    def test_acceptance_selects_the_exact_primary_bytes_not_the_sidecar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_lms.labs.ccna_mastery import cli as cli_module
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        golden_root = GateArtifactPaths.for_project_root(root).expected_root
        before = _directory_snapshot(golden_root)
        run_id = f"adversarial-payload-choice-{uuid.uuid4().hex}"
        observed = self._write_observed(
            _valid_passing_report(run_id=run_id), executing_checkout_root=root
        )
        sidecar = provenance_path_for_primary(observed)
        captured: dict[str, bytes] = {}

        def capture_write(*, directory: Path, filename: str, data: bytes) -> Path:
            captured["data"] = data
            return directory / filename

        monkeypatch.setattr(cli_module, "write_immutable_artifact", capture_write)

        exit_code = cli_module._accept_goldens_command(
            self._args_for(observed, project_root=str(root))
        )

        assert exit_code == 0
        assert captured["data"] == observed.read_bytes()
        assert captured["data"] != sidecar.read_bytes()
        assert _directory_snapshot(golden_root) == before

    def test_a_preexisting_symlink_at_the_golden_destination_is_not_followed(
        self, tmp_path: Path
    ) -> None:
        """The destination write is create-if-absent via ``os.link``, which
        refuses when *anything* — including a symlink planted in advance —
        already occupies that exact name, rather than following it.
        """
        from personal_lms.labs.ccna_mastery.cli import _accept_goldens_command
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()
        golden_root = GateArtifactPaths.for_project_root(root).expected_root
        before = _directory_snapshot(golden_root)

        run_id = f"adversarial-golden-symlink-{uuid.uuid4().hex}"
        outside = tmp_path / "elsewhere.json"
        outside.write_text("not a real golden", encoding="utf-8")
        golden_root.mkdir(parents=True, exist_ok=True)
        destination = golden_root / f"gate-1-{run_id}.json"
        destination.symlink_to(outside)
        try:
            observed = self._write_observed(
                _valid_passing_report(run_id=run_id), executing_checkout_root=root
            )
            args = self._args_for(observed, project_root=str(root))

            exit_code = _accept_goldens_command(args)

            assert exit_code == 1
            assert outside.read_text(encoding="utf-8") == "not a real golden"
        finally:
            destination.unlink()
            assert _directory_snapshot(golden_root) == before
