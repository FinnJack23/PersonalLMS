"""Persisted fixture authority: missing, stale, mismatched, and valid decisions.

The CLI never supplied a real ``FixtureAuthority`` to the gate runner, so
every real run fell back to a manifest-claim-derived authority that always
resolves to ``draft_for_human_review`` — safe, but permanently so: no real
run could ever report Gate 1 as passed even after a genuine human reviewer
approved the exact fixture tree.

``current_fixture_authority`` is the only path a persisted decision can now
reach a gate report through. It must fail closed for everything except a
current, matching approval, and the failure modes below are exactly the
ones named in the finding: missing, stale, mismatched, and anonymous
authority, plus the one thing that must never grant it — a pack's own
authored claim about itself.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from personal_lms.domain.evidence_review import EvidenceReviewOutcome, ReviewerIdentity
from personal_lms.labs.ccna_mastery.fixture_authority_store import (
    FixtureAuthorityDecision,
    SQLiteFixtureAuthorityRepository,
    current_fixture_authority,
)
from personal_lms.labs.ccna_mastery.gates import FixtureAuthority, provenance_path_for_primary
from personal_lms.labs.ccna_mastery.report_schema import report_from_bound_provenance

from ..objective_packs._helpers import write_pack_directory

HASH_A = "a" * 64
HASH_B = "b" * 64
DECIDED_AT = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def repository():  # type: ignore[no-untyped-def]
    repo = SQLiteFixtureAuthorityRepository.open(":memory:")
    repo.initialize_schema()
    try:
        yield repo
    finally:
        repo.close()


def _decision(
    *,
    manifest_hash: str = HASH_A,
    outcome: EvidenceReviewOutcome = EvidenceReviewOutcome.APPROVED,
    decided_at: datetime = DECIDED_AT,
    reviewer_id: str = "alan",
) -> FixtureAuthorityDecision:
    return FixtureAuthorityDecision(
        manifest_hash=manifest_hash,
        reviewer=ReviewerIdentity(reviewer_id=reviewer_id, role="content_reviewer"),
        outcome=outcome,
        reason="synthetic isolated test decision",
        decided_at=decided_at,
    )


class TestMissingDecisionFailsClosed:
    def test_no_decision_at_all_is_draft_for_human_review(self, repository) -> None:  # type: ignore[no-untyped-def]
        authority = current_fixture_authority(repository, manifest_hash=HASH_A)
        assert authority.resolved_status(HASH_A) == "draft_for_human_review"
        assert not authority.is_authoritative


class TestStaleAndMismatchedDecisionsFailClosed:
    def test_a_decision_for_a_different_hash_does_not_authorize_this_one(self, repository) -> None:  # type: ignore[no-untyped-def]
        repository.append(_decision(manifest_hash=HASH_A))

        authority = current_fixture_authority(repository, manifest_hash=HASH_B)

        assert authority.resolved_status(HASH_B) == "draft_for_human_review"

    def test_a_since_changed_manifest_silently_revokes_the_prior_approval(self, repository) -> None:  # type: ignore[no-untyped-def]
        """No separate staleness check is needed: the lookup key is the check."""
        repository.append(_decision(manifest_hash=HASH_A))

        # The fixture changed; its hash is now HASH_B. The approval made
        # against HASH_A is simply never found under HASH_B.
        authority = current_fixture_authority(repository, manifest_hash=HASH_B)

        assert not authority.is_authoritative

    def test_a_non_approval_outcome_does_not_authorize(self, repository) -> None:  # type: ignore[no-untyped-def]
        repository.append(_decision(outcome=EvidenceReviewOutcome.REJECTED))

        authority = current_fixture_authority(repository, manifest_hash=HASH_A)

        assert authority.resolved_status(HASH_A) == "draft_for_human_review"

    def test_a_later_rejection_revokes_an_earlier_approval(self, repository) -> None:  # type: ignore[no-untyped-def]
        repository.append(_decision(outcome=EvidenceReviewOutcome.APPROVED, decided_at=DECIDED_AT))
        repository.append(
            _decision(
                outcome=EvidenceReviewOutcome.REJECTED,
                decided_at=DECIDED_AT.replace(hour=13),
            )
        )

        authority = current_fixture_authority(repository, manifest_hash=HASH_A)

        assert authority.resolved_status(HASH_A) == "draft_for_human_review"

    def test_a_later_rejection_in_a_different_offset_still_revokes_the_approval(  # type: ignore[no-untyped-def]
        self, repository
    ) -> None:
        """Ordering must be chronological, never lexical.

        Both decisions below are valid aware datetimes, but in different UTC
        offsets. The rejection is two hours *later* in real time, yet its raw
        ISO-8601 text sorts *before* the approval's, because ``09`` precedes
        ``12`` as characters. Ordering that raw text would return the stale
        approval and leave a revoked fixture authoritative — so decisions are
        normalized to canonical UTC before they are stored or compared.
        """
        approval_at = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
        rejection_at = datetime(2026, 7, 27, 9, 0, tzinfo=timezone(timedelta(hours=-5)))
        # The trap, asserted rather than described: later in time, earlier in text.
        assert rejection_at > approval_at
        assert rejection_at.isoformat() < approval_at.isoformat()

        repository.append(_decision(outcome=EvidenceReviewOutcome.APPROVED, decided_at=approval_at))
        repository.append(
            _decision(outcome=EvidenceReviewOutcome.REJECTED, decided_at=rejection_at)
        )

        current = repository.current_for(HASH_A)
        assert current is not None
        assert current.outcome is EvidenceReviewOutcome.REJECTED
        # Same instant, now carried in canonical UTC.
        assert current.decided_at == rejection_at
        assert current.decided_at.utcoffset() == timedelta(0)

        authority = current_fixture_authority(repository, manifest_hash=HASH_A)

        assert authority.resolved_status(HASH_A) == "draft_for_human_review"
        assert not authority.is_authoritative


class TestAnonymousDecisionsAreRejected:
    def test_an_empty_reviewer_id_is_refused_by_reviewer_identity(self) -> None:
        with pytest.raises(ValueError):
            ReviewerIdentity(reviewer_id="", role="content_reviewer")

    def test_from_reviewer_decision_refuses_an_empty_reviewer_id(self) -> None:
        with pytest.raises(ValueError, match="explicit reviewer identity"):
            FixtureAuthority.from_reviewer_decision(
                manifest_hash=HASH_A, reviewer_id="", decided_at=DECIDED_AT
            )


class TestFixtureAuthoredClaimsAreNeverAuthority:
    def test_a_manifest_claiming_reviewed_confers_no_authority(self) -> None:
        """The one legitimate path in is a persisted decision, never a pack's own claim."""
        authority = FixtureAuthority.from_manifest_claim(
            claimed_status="reviewed", manifest_hash=HASH_A
        )

        assert authority.resolved_status(HASH_A) == "draft_for_human_review"
        assert not authority.is_authoritative


class TestValidPersistedAuthorityIsHonored:
    def test_a_current_matching_approval_resolves_to_reviewed(self, repository) -> None:  # type: ignore[no-untyped-def]
        repository.append(_decision(manifest_hash=HASH_A))

        authority = current_fixture_authority(repository, manifest_hash=HASH_A)

        assert authority.resolved_status(HASH_A) == "reviewed"
        assert authority.is_authoritative
        assert authority.reviewer_id == "alan"

    def test_the_hash_is_validated_before_any_lookup(self, repository) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ValueError, match="64 lowercase hex"):
            current_fixture_authority(repository, manifest_hash="not-a-hash")

    def test_a_decision_recorded_on_one_connection_is_visible_on_another(
        self, tmp_path: Path
    ) -> None:
        database = tmp_path / "authority.sqlite3"
        writer = SQLiteFixtureAuthorityRepository.open(str(database))
        writer.initialize_schema()
        writer.append(_decision(manifest_hash=HASH_A))
        writer.close()

        reader = SQLiteFixtureAuthorityRepository.open(str(database))
        try:
            authority = current_fixture_authority(reader, manifest_hash=HASH_A)
        finally:
            reader.close()

        assert authority.resolved_status(HASH_A) == "reviewed"


class TestCliWiringUsesPersistedAuthority:
    def test_a_recorded_approval_makes_the_next_gate_run_report_reviewed(
        self, tmp_path: Path
    ) -> None:
        """End to end through the real CLI handlers, over a synthetic pack.

        Isolated: the pack is synthetic (``write_pack_directory``'s
        default, always ``draft_for_human_review`` on its own), the review
        database is a fresh ``tmp_path`` file, and the run ids are unique
        to this test. This never touches the real objective-2.2 fixture or
        creates an approval a real gate run could be mistaken for.
        """
        from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
        from personal_lms.labs.ccna_mastery.cli import (
            _approve_fixture_command,
            _gate_evidence_command,
        )
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        pack_root = tmp_path / "packs"
        pack_root.mkdir()
        directory_name, _ = write_pack_directory(pack_root)
        database = tmp_path / "review.sqlite3"
        # The gate command's review reader fails closed on a missing store;
        # a real deployment provisions the file once. Pre-create it empty.
        bootstrap = SQLiteEvidenceReviewRepository.open(str(database))
        bootstrap.initialize_schema()
        bootstrap.close()
        run_id = f"adversarial-fixture-authority-{uuid.uuid4().hex}"
        project_root = str(executing_checkout_root())
        gates_root = executing_checkout_root() / "var" / "ccna-mastery" / "gates"

        def gate_args(run_id: str) -> argparse.Namespace:
            return argparse.Namespace(
                pack_root=str(pack_root),
                pack_directory=directory_name,
                pack_format="json",
                run_id=run_id,
                attempt_id=None,
                project_root=project_root,
                review_database=str(database),
            )

        _gate_evidence_command(gate_args(run_id))
        before_primary = gates_root / run_id / "gate-1.json"
        before = report_from_bound_provenance(
            primary_bytes=before_primary.read_bytes(),
            provenance_bytes=provenance_path_for_primary(before_primary).read_bytes(),
        )
        assert before.fixture_authority == "draft_for_human_review"

        approve_args = argparse.Namespace(
            pack_root=str(pack_root),
            pack_directory=directory_name,
            pack_format="json",
            reviewer_id="test-reviewer",
            reviewer_role="content_reviewer",
            outcome="approved",
            reason="synthetic isolated test approval",
            review_database=str(database),
        )
        assert _approve_fixture_command(approve_args) == 0

        second_run_id = f"{run_id}-second"
        _gate_evidence_command(gate_args(second_run_id))
        after_primary = gates_root / second_run_id / "gate-1.json"
        after = report_from_bound_provenance(
            primary_bytes=after_primary.read_bytes(),
            provenance_bytes=provenance_path_for_primary(after_primary).read_bytes(),
        )

        assert after.fixture_authority == "reviewed"
        assert after.fixture_manifest_hash == before.fixture_manifest_hash
