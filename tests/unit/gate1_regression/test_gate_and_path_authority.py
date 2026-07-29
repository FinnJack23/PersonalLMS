"""Gate authority and artifact-path security (review findings #19–#24).

Three defects drove these tests:

- A report could pass while *omitting* required checks entirely. Absence
  read as success, so the cheapest way to make a gate green was to build a
  report with one passing check and nothing else.
- ``fixture_authority`` was an authoring field inside the manifest, so a
  pack could grant itself reviewed status.
- A CLI caller chose both artifact roots, so a normal run could point
  ``--golden-root`` somewhere harmless and write anywhere; the temporary
  file used a predictable name that a pre-created symlink could redirect.
"""

from __future__ import annotations

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
    GateStatus,
    GoldenArtifactGuard,
    GoldenWriteRefusedError,
    ObservedGateReportStore,
)

STARTED_AT = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=42)


def check(check_id: str, status: GateCheckStatus = GateCheckStatus.PASSED) -> GateCheck:
    return GateCheck(check_id=check_id, status=status, required=True, reason_code="ok")


def full_checks() -> tuple[GateCheck, ...]:
    return tuple(check(check_id) for check_id in GateDefinition.gate_1().required_check_ids)


def make_report(
    *,
    checks: tuple[GateCheck, ...] | None = None,
    fixture_authority: str = "reviewed",
    **overrides: object,
) -> GateReport:
    defaults: dict[str, object] = {
        "gate_id": GateId.GATE_1,
        "run_id": "run-1",
        "code_revision": "abc123",
        "fixture_manifest_hash": "a" * 64,
        "fixture_authority": fixture_authority,
        "checks": full_checks() if checks is None else checks,
        "started_at": STARTED_AT,
        "finished_at": FINISHED_AT,
    }
    return GateReport(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestRequiredCheckInventory:
    def test_a_gate_definition_names_its_required_checks(self) -> None:
        definition = GateDefinition.gate_1()

        assert "G1-GO-02" in definition.required_check_ids
        assert definition.definition_version

    def test_a_report_missing_a_required_check_cannot_pass(self) -> None:
        """Absence must not read as success."""
        report = make_report(checks=(check("G1-GO-01"),))

        assert report.status is not GateStatus.PASSED

    def test_the_missing_check_ids_are_reported(self) -> None:
        report = make_report(checks=(check("G1-GO-01"),))

        assert "G1-GO-02" in report.missing_required_check_ids

    def test_a_complete_passing_report_over_reviewed_fixtures_passes(self) -> None:
        assert make_report().status is GateStatus.PASSED

    def test_one_failing_check_in_a_complete_report_fails(self) -> None:
        checks = tuple(
            check(
                check_id,
                GateCheckStatus.FAILED if check_id == "G1-GO-06" else GateCheckStatus.PASSED,
            )
            for check_id in GateDefinition.gate_1().required_check_ids
        )

        assert make_report(checks=checks).status is GateStatus.FAILED


class TestFixtureAuthorityIsExternal:
    def test_an_authoring_manifest_cannot_grant_reviewed_authority(self) -> None:
        """``fixture_status: reviewed`` in a pack file is a claim, not authority."""
        from personal_lms.labs.ccna_mastery.gates import FixtureAuthority

        authority = FixtureAuthority.from_manifest_claim(
            claimed_status="reviewed", manifest_hash="a" * 64
        )

        assert authority.is_authoritative is False

    def test_an_external_decision_pinned_to_the_manifest_hash_grants_authority(self) -> None:
        from personal_lms.labs.ccna_mastery.gates import FixtureAuthority

        authority = FixtureAuthority.from_reviewer_decision(
            manifest_hash="a" * 64, reviewer_id="reviewer-1", decided_at=STARTED_AT
        )

        assert authority.is_authoritative is True

    def test_a_decision_pinned_to_a_different_manifest_does_not_transfer(self) -> None:
        from personal_lms.labs.ccna_mastery.gates import FixtureAuthority

        authority = FixtureAuthority.from_reviewer_decision(
            manifest_hash="b" * 64, reviewer_id="reviewer-1", decided_at=STARTED_AT
        )

        assert authority.applies_to("a" * 64) is False

    def test_a_run_with_no_external_decision_reports_unapproved_authority(self) -> None:
        assert make_report(fixture_authority="draft_for_human_review").status is (
            GateStatus.UNAPPROVED_AUTHORITY
        )


class TestArtifactPathAuthority:
    def test_canonical_roots_come_from_trusted_configuration(self, tmp_path: Path) -> None:
        paths = GateArtifactPaths.for_project_root(tmp_path)

        assert paths.observed_root.is_relative_to(tmp_path / "var" / "ccna-mastery" / "gates")

    def test_the_two_roots_must_be_disjoint(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()

        with pytest.raises(ValueError, match="disjoint"):
            GateArtifactPaths(expected_root=shared, observed_root=shared)

    def test_an_observed_root_nested_in_the_golden_tree_is_refused(self, tmp_path: Path) -> None:
        golden = tmp_path / "expected"
        (golden / "nested").mkdir(parents=True)

        with pytest.raises(ValueError, match="disjoint"):
            GateArtifactPaths(expected_root=golden, observed_root=golden / "nested")

    def test_observed_output_must_stay_beneath_the_canonical_var_directory(
        self, tmp_path: Path
    ) -> None:
        paths = GateArtifactPaths.for_project_root(tmp_path)

        assert "var" in paths.observed_root.parts
        assert "ccna-mastery" in paths.observed_root.parts


class TestGoldenWriteSecurity:
    @pytest.fixture
    def paths(self, tmp_path: Path) -> GateArtifactPaths:
        return GateArtifactPaths.for_project_root(tmp_path)

    def test_a_normal_run_cannot_redirect_output_into_the_real_expected_tree(
        self, paths: GateArtifactPaths
    ) -> None:
        guard = GoldenArtifactGuard(paths=paths)
        store = ObservedGateReportStore(guard=guard)

        with pytest.raises(GoldenWriteRefusedError):
            store.write(make_report(), destination_override=paths.expected_root / "x.json")

    def test_a_symlinked_run_directory_escaping_var_is_refused(
        self, paths: GateArtifactPaths, tmp_path: Path
    ) -> None:
        paths.observed_root.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (paths.observed_root / "escaping-run").symlink_to(outside, target_is_directory=True)

        store = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths))

        with pytest.raises(GoldenWriteRefusedError):
            store.write(make_report(run_id="escaping-run"))

    def test_a_pre_created_temporary_symlink_cannot_overwrite_a_golden(
        self, paths: GateArtifactPaths
    ) -> None:
        """A predictable temp name plus a planted symlink was the attack."""
        paths.expected_root.mkdir(parents=True, exist_ok=True)
        golden = paths.expected_root / "evidence-report.json"
        golden.write_text('{"approved": true}', encoding="utf-8")

        run_directory = paths.observed_root / "run-1"
        run_directory.mkdir(parents=True, exist_ok=True)
        (run_directory / "gate-1.json.tmp").symlink_to(golden)

        store = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths))
        store.write(make_report())

        assert golden.read_text(encoding="utf-8") == '{"approved": true}'

    def test_temporary_files_are_not_predictable(self, paths: GateArtifactPaths) -> None:
        store = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths))
        store.write(make_report())

        assert list(paths.observed_root.rglob("*.tmp")) == []

    def test_rewriting_an_existing_observed_report_is_refused_without_a_new_attempt(
        self, paths: GateArtifactPaths
    ) -> None:
        store = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths))
        store.write(make_report())

        with pytest.raises(GoldenWriteRefusedError, match="attempt"):
            store.write(make_report())

    def test_an_explicit_new_attempt_id_is_accepted(self, paths: GateArtifactPaths) -> None:
        store = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths))
        store.write(make_report())

        destination = store.write(make_report(), attempt_id="attempt-2")

        assert destination.exists()

    def test_a_traversing_run_id_is_refused(self, paths: GateArtifactPaths) -> None:
        store = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths))

        with pytest.raises(GoldenWriteRefusedError):
            store.write(make_report(run_id="../../expected"))

    def test_the_written_file_is_not_group_or_world_writable(
        self, paths: GateArtifactPaths
    ) -> None:
        store = ObservedGateReportStore(guard=GoldenArtifactGuard(paths=paths))

        destination = store.write(make_report())

        assert destination.stat().st_mode & 0o022 == 0
