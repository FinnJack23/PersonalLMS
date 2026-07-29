"""Gate authority: exact inventory, requiredness, and status derivation.

Covers red items 1–6 and reviewer finding A.

Finding A is the one that matters most. Earlier reviews tested two
*non*-attacks — all checks optional (yields NOT_RUN) and all checks
required-and-failing (yields FAILED) — and concluded requiredness was
sound. The real bypass is neither: include every defined check ID, mark
exactly one of them ``required=True`` and passing, demote the rest to
``required=False`` while letting them FAIL, and the gate reports PASSED.
``missing_required_check_ids`` counted IDs as *present* regardless of
whether the report treated them as required, so a caller could silence
the entire inventory one flag at a time.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
FINISHED_AT = STARTED_AT + timedelta(seconds=1)

TRACEABILITY = Path("docs/plans/ccna-mastery-micro-lab/LINCHPIN_TRACEABILITY.md")


def authoritative_gate_1_ids() -> frozenset[str]:
    """Every Gate 1 check ID the traceability document actually names.

    Read from the document rather than hard-coded, so the test tracks the
    plan instead of restating one engineer's reading of it. The root is
    the executing checkout — the same one ``resolve_code_revision`` and
    ``GateArtifactPaths`` bind to — rather than a hardcoded worktree path,
    so this test is portable across machines and CI.
    """
    from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

    text = executing_checkout_root().joinpath(TRACEABILITY).read_text(encoding="utf-8")
    return frozenset(re.findall(r"G1-(?:GO|NG|FX)-\d+", text))


def report(
    *,
    checks: tuple[GateCheck, ...],
    fixture_authority: str = "reviewed",
    **overrides: object,
) -> GateReport:
    defaults: dict[str, object] = {
        "gate_id": GateId.GATE_1,
        "run_id": "run-1",
        "code_revision": "rev",
        "fixture_manifest_hash": "a" * 64,
        "fixture_authority": fixture_authority,
        "checks": checks,
        "started_at": STARTED_AT,
        "finished_at": FINISHED_AT,
    }
    return GateReport(**{**defaults, **overrides})  # type: ignore[arg-type]


def check(
    check_id: str,
    status: GateCheckStatus = GateCheckStatus.PASSED,
    *,
    required: bool = True,
) -> GateCheck:
    return GateCheck(check_id=check_id, status=status, required=required, reason_code="probe")


def all_defined(status: GateCheckStatus = GateCheckStatus.PASSED) -> tuple[GateCheck, ...]:
    return tuple(check(cid, status) for cid in GateDefinition.gate_1().required_check_ids)


class TestExactInventory:
    def test_the_definition_contains_every_authoritative_gate_1_id(self) -> None:
        """26 IDs: 11 GO, 6 NG, 9 FX. A shorter gate is a different gate."""
        assert set(GateDefinition.gate_1().required_check_ids) == authoritative_gate_1_ids()

    def test_the_inventory_has_no_duplicates(self) -> None:
        ids = GateDefinition.gate_1().required_check_ids

        assert len(ids) == len(set(ids))

    def test_the_inventory_is_sorted_for_stable_comparison(self) -> None:
        ids = GateDefinition.gate_1().required_check_ids

        assert list(ids) == sorted(ids)

    def test_the_definition_carries_a_version(self) -> None:
        assert GateDefinition.gate_1().definition_version


class TestRequirednessCannotBeDowngraded:
    def test_a_defined_check_cannot_be_demoted_to_optional(self) -> None:
        """Reviewer finding A, the actual bypass."""
        checks = (
            check(GateDefinition.gate_1().required_check_ids[0], GateCheckStatus.PASSED),
            *(
                check(cid, GateCheckStatus.FAILED, required=False)
                for cid in GateDefinition.gate_1().required_check_ids[1:]
            ),
        )

        assert report(checks=checks).status is not GateStatus.PASSED

    def test_a_demoted_failing_check_still_fails_the_gate(self) -> None:
        checks = tuple(
            check(
                cid,
                GateCheckStatus.FAILED if index == 3 else GateCheckStatus.PASSED,
                required=index != 3,
            )
            for index, cid in enumerate(GateDefinition.gate_1().required_check_ids)
        )

        assert report(checks=checks).status is GateStatus.FAILED

    def test_requiredness_is_derived_from_the_definition_not_the_report(self) -> None:
        """A defined check is required whatever the report's flag says."""
        checks = tuple(
            check(cid, GateCheckStatus.PASSED, required=False)
            for cid in GateDefinition.gate_1().required_check_ids
        )

        effective = report(checks=checks).effective_required_checks

        assert {entry.check_id for entry in effective} == set(
            GateDefinition.gate_1().required_check_ids
        )


class TestUnknownChecksCannotSubstitute:
    def test_an_unknown_required_pass_cannot_stand_in_for_the_inventory(self) -> None:
        assert report(checks=(check("TOTALLY-UNKNOWN-01"),)).status is not GateStatus.PASSED

    def test_unknown_checks_are_reported_separately(self) -> None:
        result = report(checks=(*all_defined(), check("EXTRA-99")))

        assert "EXTRA-99" in result.unknown_check_ids

    def test_an_unknown_check_never_makes_a_complete_report_pass_faster(self) -> None:
        """Padding with unknown passes must not offset a defined failure."""
        checks = (
            *all_defined(),
            *(check(f"PAD-{index:02d}") for index in range(5)),
        )
        failing = tuple(
            entry.model_copy(update={"status": GateCheckStatus.FAILED})
            if entry.check_id == "G1-GO-06"
            else entry
            for entry in checks
        )

        assert report(checks=failing).status is GateStatus.FAILED


class TestStatusDerivation:
    def test_a_complete_passing_reviewed_report_passes(self) -> None:
        assert report(checks=all_defined()).status is GateStatus.PASSED

    @pytest.mark.parametrize(
        "status",
        [GateCheckStatus.FAILED, GateCheckStatus.BLOCKED, GateCheckStatus.NOT_RUN],
    )
    def test_any_non_passing_required_check_prevents_a_pass(self, status: GateCheckStatus) -> None:
        checks = tuple(
            check(cid, status if cid == "G1-GO-03" else GateCheckStatus.PASSED)
            for cid in GateDefinition.gate_1().required_check_ids
        )

        assert report(checks=checks).status is not GateStatus.PASSED

    def test_a_missing_required_check_prevents_a_pass(self) -> None:
        checks = tuple(entry for entry in all_defined() if entry.check_id != "G1-GO-09")

        result = report(checks=checks)

        assert result.status is not GateStatus.PASSED
        assert "G1-GO-09" in result.missing_required_check_ids

    def test_an_improperly_deferred_required_check_cannot_pass(self) -> None:
        """No Gate 1 check is on the deferral allowlist."""
        with pytest.raises(ValueError, match="may not report 'deferred'"):
            check("G1-GO-11", GateCheckStatus.DEFERRED)

    def test_draft_fixture_authority_prevents_a_pass(self) -> None:
        assert (
            report(checks=all_defined(), fixture_authority="draft_for_human_review").status
            is GateStatus.UNAPPROVED_AUTHORITY
        )


class TestGlobalContractsAreExecutable:
    def test_global_03_a_gate_passes_only_when_every_required_check_passes(self) -> None:
        for index in range(len(GateDefinition.gate_1().required_check_ids)):
            checks = tuple(
                check(cid, GateCheckStatus.FAILED if position == index else GateCheckStatus.PASSED)
                for position, cid in enumerate(GateDefinition.gate_1().required_check_ids)
            )
            assert report(checks=checks).status is GateStatus.FAILED

    def test_global_04_deferral_cannot_hide_a_core_failure(self) -> None:
        allowlisted = GateCheck(
            check_id="G3-RI-QWEN-01",
            status=GateCheckStatus.DEFERRED,
            required=False,
            reason_code="ollama_absent",
        )
        checks = (
            *tuple(
                check(cid, GateCheckStatus.FAILED if cid == "G1-GO-01" else GateCheckStatus.PASSED)
                for cid in GateDefinition.gate_1().required_check_ids
            ),
            allowlisted,
        )

        assert report(checks=checks).status is GateStatus.FAILED

    def test_global_06_a_report_records_its_definition_version(self) -> None:
        payload = json.loads(report(checks=all_defined()).to_canonical_json())

        assert payload["definition_version"] == GateDefinition.gate_1().definition_version

    def test_global_06_a_report_records_every_required_structural_field(self) -> None:
        payload = json.loads(report(checks=all_defined()).to_canonical_json())

        for field in (
            "schema_version",
            "gate_id",
            "status",
            "fixture_manifest_hash",
            "code_revision",
            "started_at",
            "finished_at",
            "definition_version",
        ):
            assert field in payload, f"{field} missing from the serialized report"

    def test_global_06_gate_id_is_constrained_to_the_three_gates(self) -> None:
        assert {gate.value for gate in GateId} == {"gate-1", "gate-2", "gate-3"}


class TestBindExpectationsCannotBeChosenByACheckSite:
    """``bind_expectations`` is the only source of a check's comparison target.

    An earlier revision preserved a caller-supplied ``expected_ref`` when
    one was already set, which meant a check site that happened to set its
    own reference bypassed the trusted lookup — including the "unknown
    check id raises" guarantee, since a pre-set reference skipped
    ``expectation_ref`` entirely.
    """

    def test_a_caller_supplied_reference_is_overwritten_not_preserved(self) -> None:
        smuggled = GateCheck(
            check_id="G1-GO-01",
            status=GateCheckStatus.PASSED,
            reason_code="probe",
            expected_ref="https://attacker.example/fake",
        )

        bound = GateDefinition.gate_1().bind_expectations((smuggled,))

        assert bound[0].expected_ref == GateDefinition.gate_1().expectation_ref("G1-GO-01")
        assert bound[0].expected_ref != "https://attacker.example/fake"

    def test_an_unknown_check_id_still_raises_even_with_a_preset_reference(self) -> None:
        smuggled = GateCheck(
            check_id="G9-NOT-A-ROW",
            status=GateCheckStatus.PASSED,
            reason_code="probe",
            expected_ref="docs/plans/ccna-mastery-micro-lab/LINCHPIN_TRACEABILITY.md",
        )

        with pytest.raises(KeyError, match="no expectation contract is defined"):
            GateDefinition.gate_1().bind_expectations((smuggled,))


class TestDeferralAllowlistsAreUnified:
    """The internal and frozen-schema deferral allowlists must name the same ids.

    A gate report is validated internally against ``gates._DEFERRABLE_CHECK_IDS``
    and, separately, projected onto the frozen schema using
    ``report_schema.FROZEN_DEFERRABLE_CHECK_IDS``. If the two ever disagreed, a
    report could accept a deferral internally that the frozen projection then
    refuses (or vice versa) — a report that is simultaneously legal and
    illegal depending only on which half of the pipeline looked at it.
    """

    def test_the_internal_and_frozen_allowlists_are_identical(self) -> None:
        from personal_lms.labs.ccna_mastery import gates as gates_module
        from personal_lms.labs.ccna_mastery.report_schema import FROZEN_DEFERRABLE_CHECK_IDS

        assert gates_module._DEFERRABLE_CHECK_IDS == FROZEN_DEFERRABLE_CHECK_IDS  # noqa: SLF001
        assert {  # noqa: SLF001
            "G3-RI-QWEN-01",
            "week-scale-retest-bank-comparison",
        } == gates_module._DEFERRABLE_CHECK_IDS


class TestFrozenSchemaViewCannotSubstituteAManifestHash:
    """``frozen_schema_view`` always cites the hash actually bound to the report."""

    def test_the_projected_hash_is_always_the_reports_own(self) -> None:
        from personal_lms.labs.ccna_mastery.report_schema import frozen_schema_view

        bound_checks = GateDefinition.gate_1().bind_expectations(all_defined())
        subject = report(checks=bound_checks, fixture_manifest_hash="c" * 64)

        view = frozen_schema_view(subject)

        assert view["fixture_manifest_sha256"] == "c" * 64

    def test_the_function_no_longer_accepts_a_manifest_override(self) -> None:
        """The removed footgun: no call site ever used it, and its only
        effect was a manifest-hash substitution primitive."""
        from personal_lms.labs.ccna_mastery.report_schema import frozen_schema_view

        bound_checks = GateDefinition.gate_1().bind_expectations(all_defined())
        subject = report(checks=bound_checks)

        with pytest.raises(TypeError):
            frozen_schema_view(subject, manifest_sha256="d" * 64)  # type: ignore[call-arg]
