"""Gate report and golden-guard tests.

Two properties dominate: a gate status cannot be asserted into existence,
and an approved golden cannot be written by an ordinary run. Both are
tested from the "attacker" direction — the tests try the shortcut and
assert it fails.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from personal_lms.labs.ccna_mastery.gates import (
    GateCheck,
    GateCheckStatus,
    GateDefinition,
    GateId,
    GateReport,
    GateStatus,
)

STARTED_AT = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=42)


def make_check(
    *,
    check_id: str = "G1-GO-01",
    status: GateCheckStatus = GateCheckStatus.PASSED,
    required: bool = True,
    reason_code: str = "ok",
) -> GateCheck:
    return GateCheck(check_id=check_id, status=status, required=required, reason_code=reason_code)


def full_checks(**overrides: GateCheckStatus) -> tuple[GateCheck, ...]:
    """One passing check per required id, with named ids overridden.

    A report must carry the whole required inventory now — absence no
    longer reads as success — so tests build from the complete set rather
    than from a single check.
    """
    return tuple(
        make_check(check_id=check_id, status=overrides.get(check_id, GateCheckStatus.PASSED))
        for check_id in GateDefinition.gate_1().required_check_ids
    )


def make_report(
    *,
    checks: tuple[GateCheck, ...] | None = None,
    fixture_authority: str = "reviewed",
    run_id: str = "run-1",
    **overrides: object,
) -> GateReport:
    defaults: dict[str, object] = {
        "gate_id": GateId.GATE_1,
        "run_id": run_id,
        "code_revision": "abc123",
        "fixture_manifest_hash": "a" * 64,
        "fixture_authority": fixture_authority,
        "checks": full_checks() if checks is None else checks,
        "started_at": STARTED_AT,
        "finished_at": FINISHED_AT,
    }
    return GateReport(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestDerivedStatus:
    def test_all_required_checks_passing_over_reviewed_fixtures_passes(self) -> None:
        assert make_report().status is GateStatus.PASSED

    def test_a_failing_required_check_fails_the_gate(self) -> None:
        report = make_report(checks=full_checks(**{"G1-GO-02": GateCheckStatus.FAILED}))

        assert report.status is GateStatus.FAILED

    def test_a_blocked_required_check_blocks_the_gate(self) -> None:
        report = make_report(checks=full_checks(**{"G1-GO-02": GateCheckStatus.BLOCKED}))

        assert report.status is GateStatus.BLOCKED

    def test_a_not_run_required_check_leaves_the_gate_not_run(self) -> None:
        report = make_report(checks=full_checks(**{"G1-GO-07": GateCheckStatus.NOT_RUN}))

        assert report.status is GateStatus.NOT_RUN

    def test_a_report_with_no_required_checks_is_not_run(self) -> None:
        report = make_report(checks=(make_check(required=False),))

        assert report.status is GateStatus.NOT_RUN

    def test_failure_outranks_blocking_and_not_run(self) -> None:
        report = make_report(
            checks=full_checks(
                **{
                    "G1-GO-01": GateCheckStatus.NOT_RUN,
                    "G1-GO-02": GateCheckStatus.BLOCKED,
                    "G1-GO-03": GateCheckStatus.FAILED,
                }
            )
        )

        assert report.status is GateStatus.FAILED

    def test_a_non_required_failure_does_not_fail_the_gate(self) -> None:
        report = make_report(
            checks=(
                *full_checks(),
                make_check(check_id="G1-INFO-01", status=GateCheckStatus.FAILED, required=False),
            )
        )

        assert report.status is GateStatus.PASSED


class TestFixtureAuthority:
    def test_passing_checks_over_draft_fixtures_cannot_claim_a_pass(self) -> None:
        """A green run over unreviewed fixtures proves nothing about readiness."""
        report = make_report(fixture_authority="draft_for_human_review")

        assert report.status is GateStatus.UNAPPROVED_AUTHORITY

    def test_a_real_failure_is_still_reported_as_a_failure_over_draft_fixtures(self) -> None:
        report = make_report(
            fixture_authority="draft_for_human_review",
            checks=full_checks(**{"G1-GO-01": GateCheckStatus.FAILED}),
        )

        assert report.status is GateStatus.FAILED

    def test_an_unknown_authority_value_is_refused(self) -> None:
        with pytest.raises(ValueError):
            make_report(fixture_authority="approved_by_me")


class TestDeferralAllowlist:
    def test_an_arbitrary_check_may_not_defer(self) -> None:
        with pytest.raises(ValueError, match="may not report 'deferred'"):
            make_check(check_id="G1-GO-01", status=GateCheckStatus.DEFERRED)

    def test_the_named_optional_qwen_checks_may_defer(self) -> None:
        check = make_check(check_id="G3-RI-QWEN-01", status=GateCheckStatus.DEFERRED)

        assert check.status is GateCheckStatus.DEFERRED

    def test_deferred_checks_are_listed(self) -> None:
        report = make_report(
            checks=(
                *full_checks(),
                make_check(check_id="G3-RI-QWEN-01", status=GateCheckStatus.DEFERRED),
            )
        )

        assert report.deferred_check_ids == ("G3-RI-QWEN-01",)


class TestReportIntegrity:
    def test_a_duplicated_check_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="same check_id twice"):
            make_report(checks=(*full_checks(), make_check(check_id="G1-GO-01")))

    def test_finishing_before_starting_is_refused(self) -> None:
        with pytest.raises(ValueError, match="finished_at"):
            make_report(finished_at=STARTED_AT - timedelta(seconds=1))

    def test_a_malformed_observed_hash_is_refused(self) -> None:
        with pytest.raises(ValueError, match="observed_hash"):
            GateCheck(
                check_id="G1-GO-01",
                status=GateCheckStatus.PASSED,
                reason_code="ok",
                observed_hash="not-a-hash",
            )

    def test_blocking_checks_are_listed_sorted(self) -> None:
        report = make_report(
            checks=full_checks(
                **{
                    "G1-GO-09": GateCheckStatus.FAILED,
                    "G1-GO-02": GateCheckStatus.BLOCKED,
                }
            )
        )

        assert report.blocking_check_ids == ("G1-GO-02", "G1-GO-09")


class TestCanonicalSerialization:
    def test_canonical_json_includes_derived_fields(self) -> None:
        payload = json.loads(make_report().to_canonical_json())

        assert payload["status"] == "passed"
        assert payload["elapsed_milliseconds"] == 42_000
        assert payload["blocking_checks"] == []

    def test_the_content_hash_ignores_timing(self) -> None:
        """Timestamps are the only fields a comparison may normalize."""
        early = make_report()
        late = make_report(
            started_at=STARTED_AT + timedelta(days=3),
            finished_at=FINISHED_AT + timedelta(days=3),
        )

        assert early.content_hash == late.content_hash

    def test_the_content_hash_reflects_a_changed_status(self) -> None:
        passing = make_report()
        failing = make_report(checks=full_checks(**{"G1-GO-01": GateCheckStatus.FAILED}))

        assert passing.content_hash != failing.content_hash

    def test_the_content_hash_reflects_a_changed_code_revision(self) -> None:
        assert make_report().content_hash != make_report(code_revision="def456").content_hash
