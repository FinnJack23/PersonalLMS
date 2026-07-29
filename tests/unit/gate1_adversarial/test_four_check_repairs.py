"""G1-FX-07, G1-FX-08, G1-FX-09, and G1-NG-05: honest completion, not fabrication.

Each of these four rows previously reported ``NOT_RUN`` unconditionally,
with no code path that could ever turn them ``FAILED`` or ``PASSED``. Every
test here proves the *opposite* end of the new behavior too: that a real
defect in the thing each check verifies is actually caught, not merely that
the honest fixtures happen to pass.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_lms.domain.evidence_review import EvidenceReviewOutcome
from personal_lms.evidence_review.authority import record_region_approval
from personal_lms.evidence_review.service import EvidenceReviewService
from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
from personal_lms.labs.ccna_mastery.gates import GateCheckStatus
from personal_lms.labs.ccna_mastery.wiring import EvidenceGateRunner
from personal_lms.objective_packs.linchpin_fixture import (
    FixtureExtensions,
    load_frozen_fixture,
)
from personal_lms.objective_packs.loader import PackFileReader, PackLoadResult

from ..objective_packs._helpers import make_pack


def _empty_review_service() -> EvidenceReviewService:
    """An isolated, empty, in-memory-backed review store for a test's
    "the gate run's own review database" role -- distinct from whatever
    temporary store G1-FX-08's adversarial exercise builds internally."""
    repository = SQLiteEvidenceReviewRepository.open(":memory:")
    repository.initialize_schema()
    return EvidenceReviewService(repository)


def _load_result(*, fixture_extensions: object | None) -> PackLoadResult:
    pack = make_pack()
    return PackLoadResult(pack=pack, manifest=pack.manifest, fixture_extensions=fixture_extensions)


class TestG1FX07NarrowP0SemanticPins:
    @staticmethod
    def _frozen_result() -> PackLoadResult:
        pytest.importorskip("yaml", reason="requires ccna-lab extra")
        project_root = Path(__file__).parents[3]
        return load_frozen_fixture(
            PackFileReader(roots=[project_root]),
            fixture_directory="tests/linchpin",
        )

    def test_no_fixture_extensions_is_honestly_not_run(self) -> None:
        result = EvidenceGateRunner._manifest_versions_and_hash_provenance_check(
            _load_result(fixture_extensions=None)
        )

        assert result.status is GateCheckStatus.NOT_RUN
        assert result.reason_code == "fixture_extensions_absent"

    def test_frozen_p0_semantic_pins_pass(self) -> None:
        result = EvidenceGateRunner._manifest_versions_and_hash_provenance_check(
            self._frozen_result()
        )

        assert result.status is GateCheckStatus.PASSED
        assert result.reason_code == "p0_semantic_pins_verified"
        assert result.observed_hash is not None

    def test_missing_version_fails(self) -> None:
        loaded = self._frozen_result()
        extensions = loaded.fixture_extensions
        assert isinstance(extensions, FixtureExtensions)
        versions = dict(extensions.manifest_versions)
        versions.pop("scenario_version")
        altered = replace(
            loaded,
            fixture_extensions=replace(extensions, manifest_versions=versions),
        )

        result = EvidenceGateRunner._manifest_versions_and_hash_provenance_check(altered)

        assert result.status is GateCheckStatus.FAILED
        assert result.reason_code == "manifest_versions_incomplete"

    def test_edited_wp6_marker_fails(self) -> None:
        loaded = self._frozen_result()
        extensions = loaded.fixture_extensions
        assert isinstance(extensions, FixtureExtensions)
        notes = dict(extensions.manifest_canonicalization_notes)
        notes["event_stream_hash"] = "edited ownership marker"
        altered = replace(
            loaded,
            fixture_extensions=replace(
                extensions,
                manifest_canonicalization_notes=notes,
            ),
        )

        result = EvidenceGateRunner._manifest_versions_and_hash_provenance_check(altered)

        assert result.status is GateCheckStatus.FAILED
        assert result.reason_code == "event_stream_wp6_marker_mismatch"

    def test_missing_learner_pins_fails(self) -> None:
        loaded = self._frozen_result()
        extensions = loaded.fixture_extensions
        assert isinstance(extensions, FixtureExtensions)
        altered = replace(
            loaded,
            fixture_extensions=replace(extensions, scripted_learner_pins=()),
        )

        result = EvidenceGateRunner._manifest_versions_and_hash_provenance_check(altered)

        assert result.status is GateCheckStatus.FAILED
        assert result.reason_code == "scripted_learner_pins_incomplete"

    def test_missing_scenario_fails(self) -> None:
        loaded = self._frozen_result()
        extensions = loaded.fixture_extensions
        assert isinstance(extensions, FixtureExtensions)
        altered = replace(
            loaded,
            fixture_extensions=replace(extensions, scenario_state_hash_pins=None),
        )

        result = EvidenceGateRunner._manifest_versions_and_hash_provenance_check(altered)

        assert result.status is GateCheckStatus.FAILED
        assert result.reason_code == "scenario_state_hash_pins_missing"

    def test_profile_drift_fails(self) -> None:
        loaded = self._frozen_result()
        extensions = loaded.fixture_extensions
        assert isinstance(extensions, FixtureExtensions)
        altered = replace(
            loaded,
            fixture_extensions=replace(extensions, allowed_profile_provider_pins=()),
        )

        result = EvidenceGateRunner._manifest_versions_and_hash_provenance_check(altered)

        assert result.status is GateCheckStatus.FAILED
        assert result.reason_code == "execution_profile_pins_mismatch"


class TestG1FX08ApprovalBindingExercisedInProcess:
    def test_a_real_pack_passes_with_observed_evidence(self) -> None:
        pack = make_pack()

        result = EvidenceGateRunner._approval_binding_check(
            pack, review_service=_empty_review_service()
        )

        assert result.status is GateCheckStatus.PASSED
        assert result.reason_code == "approval_binding_exercised_by_gate"
        assert result.observed_hash is not None
        assert "no real reviewed record exists yet" in (result.detail or "")

    def test_a_pack_with_no_bound_region_is_blocked_not_faked(self) -> None:
        pack = make_pack(regions=())

        result = EvidenceGateRunner._approval_binding_check(
            pack, review_service=_empty_review_service()
        )

        assert result.status is GateCheckStatus.BLOCKED
        assert result.reason_code == "no_bound_region_to_exercise"

    def test_a_broken_binding_function_fails_the_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If verify_decision itself ever authorized a stale subject,
        this check must catch it -- not report a silent pass."""
        from personal_lms.labs.ccna_mastery import wiring

        def _always_authorizes(decision, *, pack, region, artifact):  # type: ignore[no-untyped-def]
            from personal_lms.evidence_review.authority import AuthorityVerdict

            return AuthorityVerdict(
                evidence_id=region.evidence_id, authorized=True, reason="broken"
            )

        monkeypatch.setattr(wiring, "verify_decision", _always_authorizes)

        result = EvidenceGateRunner._approval_binding_check(
            make_pack(), review_service=_empty_review_service()
        )

        assert result.status is GateCheckStatus.FAILED
        assert result.reason_code == "approval_binding_exercise_failed"

    def test_a_persistence_failure_is_reported_not_crashed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the real approval-recording path itself refuses the decision,
        this check must report that as a failure, not raise past its
        caller or silently pass."""
        from personal_lms.evidence_review.errors import EvidenceReviewContractError
        from personal_lms.labs.ccna_mastery import wiring

        def _always_refuses(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise EvidenceReviewContractError("synthetic refusal for this test")

        monkeypatch.setattr(wiring, "record_region_approval", _always_refuses)

        result = EvidenceGateRunner._approval_binding_check(
            make_pack(), review_service=_empty_review_service()
        )

        assert result.status is GateCheckStatus.FAILED
        assert result.reason_code == "approval_binding_exercise_failed"
        assert "synthetic refusal" in (result.detail or "")

    def test_the_adversarial_exercise_never_touches_the_real_store(self) -> None:
        """The isolated temporary store G1-FX-08 writes to must never be
        the review_service the gate run was actually constructed with."""
        pack = make_pack()
        region = pack.evidence_regions[0]
        real_service = _empty_review_service()

        result = EvidenceGateRunner._approval_binding_check(pack, review_service=real_service)

        assert result.status is GateCheckStatus.PASSED
        assert real_service.current_decision(region.evidence_id) is None

    def test_the_real_reviewed_record_is_inspected_when_one_exists(self) -> None:
        """When the pack's own review database already carries a genuine
        approval, the check reports on it read-only, alongside (not
        instead of) the adversarial exercise."""
        pack = make_pack()
        region = pack.evidence_regions[0]
        artifact = pack.sources_by_id[region.source_id]
        real_service = _empty_review_service()
        recorded = record_region_approval(
            pack=pack,
            region=region,
            artifact=artifact,
            review_service=real_service,
            reviewer_id="alan",
            reviewer_role="content_reviewer",
            outcome=EvidenceReviewOutcome.APPROVED,
            reason="a genuine prior approval for this test",
            accessible_description=None,
            decided_at=datetime(2026, 7, 28, 9, 0, tzinfo=UTC),
        )

        result = EvidenceGateRunner._approval_binding_check(pack, review_service=real_service)

        assert result.status is GateCheckStatus.PASSED
        assert str(recorded.decision_id) in (result.detail or "")
        assert "alan" in (result.detail or "")


class TestG1FX09ArchitectureGuardAsARuntimeCheck:
    def test_the_real_extractor_passes_with_observed_evidence(self) -> None:
        result = EvidenceGateRunner._architecture_guard_check()

        assert result.status is GateCheckStatus.PASSED
        assert result.reason_code == "architecture_guard_verified_by_gate"
        assert result.observed_hash is not None

    def test_a_guard_violation_fails_the_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from personal_lms.labs.ccna_mastery import wiring
        from personal_lms.labs.ccna_mastery.architecture_guard import ArchitectureGuardViolation

        def _always_violates() -> None:
            raise ArchitectureGuardViolation("synthetic scope-creep for this test")

        monkeypatch.setattr(wiring, "check_extraction_adapter_is_narrow", _always_violates)

        result = EvidenceGateRunner._architecture_guard_check()

        assert result.status is GateCheckStatus.FAILED
        assert result.reason_code == "architecture_guard_violation"
        assert "synthetic scope-creep" in (result.detail or "")
