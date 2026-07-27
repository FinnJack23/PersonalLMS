"""Evidence review repository mechanics.

The *authority* semantics — subject binding, staleness, visual-review
specificity, and linear supersession — are covered in
``tests/unit/gate1_regression/test_review_authority.py``, which was written
against the repaired design. This module keeps what is genuinely separate:
repository plumbing, identity derivation, schema namespacing, and the
structural absence of any mutation path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from personal_lms.domain.evidence_review import (
    EvidenceReviewDecision,
    EvidenceReviewKind,
    EvidenceReviewOutcome,
    ReviewerIdentity,
    compute_review_subject_digest,
    derive_decision_id,
)
from personal_lms.evidence_review.errors import (
    EvidenceReviewContractError,
    EvidenceReviewImmutableError,
    EvidenceReviewNotFoundError,
)
from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository

from ..objective_packs._helpers import make_pack, make_source, make_text_region

REVIEWED_AT = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)


@pytest.fixture
def repository() -> SQLiteEvidenceReviewRepository:
    repo = SQLiteEvidenceReviewRepository.open(":memory:")
    repo.initialize_schema()
    return repo


def make_decision(
    *,
    evidence_id: str = "ev-text-1",
    outcome: EvidenceReviewOutcome = EvidenceReviewOutcome.APPROVED,
    reviewer_id: str = "reviewer-1",
    decided_at: datetime = REVIEWED_AT,
    supersedes=None,  # type: ignore[no-untyped-def]
) -> EvidenceReviewDecision:
    pack = make_pack()
    region = make_text_region(evidence_id=evidence_id)
    artifact = make_source()
    digest = compute_review_subject_digest(
        pack_id=pack.manifest.pack_id,
        pack_version=pack.manifest.pack_version,
        objective_ref=pack.objective_ref,
        evidence_id=region.evidence_id,
        kind=EvidenceReviewKind.TEXT,
        source_id=artifact.source_id,
        source_sha256=artifact.sha256,
        selector=region.selector,
        resolved_content=region.resolved_content,
    )
    return EvidenceReviewDecision(
        decision_id=derive_decision_id(
            evidence_id=region.evidence_id,
            subject_digest=digest,
            reviewer_id=reviewer_id,
            decided_at=decided_at.isoformat(),
        ),
        evidence_id=region.evidence_id,
        source_id=artifact.source_id,
        pack_id=pack.manifest.pack_id,
        pack_version=pack.manifest.pack_version,
        objective_ref=pack.objective_ref,
        kind=EvidenceReviewKind.TEXT,
        outcome=outcome,
        subject_digest=digest,
        source_sha256=artifact.sha256,
        reviewer=ReviewerIdentity(reviewer_id=reviewer_id, role="content_reviewer"),
        reason="Synthetic draft_for_human_review test decision.",
        decided_at=decided_at,
        supersedes_decision_id=supersedes,
    )


class TestAppendOnlyStorage:
    def test_a_decision_round_trips(self, repository: SQLiteEvidenceReviewRepository) -> None:
        decision = make_decision()

        repository.append(decision)

        assert repository.get(decision.decision_id) == decision

    def test_repeating_an_identical_decision_is_idempotent(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        decision = make_decision()

        repository.append(decision)
        repository.append(decision)

        assert len(repository.history_for(decision.evidence_id)) == 1

    def test_overwriting_a_decision_id_with_different_content_is_refused(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        original = make_decision()
        repository.append(original)
        forged = original.model_copy(update={"outcome": EvidenceReviewOutcome.REJECTED})

        with pytest.raises(EvidenceReviewImmutableError):
            repository.append(forged)

    def test_superseding_an_unknown_decision_is_refused(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        with pytest.raises(EvidenceReviewContractError, match="does not hold"):
            repository.append(make_decision(supersedes=uuid4()))

    def test_superseding_a_decision_about_another_region_is_refused(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        other = make_decision(evidence_id="ev-other")
        repository.append(other)

        with pytest.raises(EvidenceReviewContractError, match="same evidence region"):
            repository.append(make_decision(supersedes=other.decision_id))

    def test_the_repository_exposes_no_mutation_method(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        """Append-only is structural: there is nothing to call to overwrite."""
        for forbidden in ("update", "delete", "remove", "overwrite"):
            assert not hasattr(repository, forbidden)

    def test_history_is_ordered_oldest_first(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        root = repository.append(make_decision(outcome=EvidenceReviewOutcome.REJECTED))
        later = make_decision(
            decided_at=REVIEWED_AT + timedelta(hours=1), supersedes=root.decision_id
        )
        repository.append(later)

        history = repository.history_for("ev-text-1")

        assert [entry.decision_id for entry in history] == [root.decision_id, later.decision_id]

    def test_an_unknown_decision_id_raises(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        with pytest.raises(EvidenceReviewNotFoundError):
            repository.get(uuid4())

    def test_a_region_with_no_history_has_no_current_decision(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        assert repository.current_for("ev-never-reviewed") is None


class TestDeterministicIdentity:
    def test_decision_ids_are_deterministic(self) -> None:
        assert make_decision().decision_id == make_decision().decision_id

    def test_a_different_reviewer_gets_a_different_decision_id(self) -> None:
        assert make_decision().decision_id != make_decision(reviewer_id="reviewer-2").decision_id

    def test_a_different_instant_gets_a_different_decision_id(self) -> None:
        later = make_decision(decided_at=REVIEWED_AT + timedelta(seconds=1))

        assert make_decision().decision_id != later.decision_id


class TestSchema:
    def test_initialize_schema_is_idempotent(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        repository.initialize_schema()
        repository.initialize_schema()

        assert repository.history_for("ev-text-1") == ()

    def test_migrations_are_namespaced_so_stores_can_share_a_file(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        """The unnamespaced schema_migrations name is deliberately not used."""
        tables = {
            row[0]
            for row in repository._connection.execute(  # noqa: SLF001
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        assert "evidence_review_schema_migrations" in tables
        assert "schema_migrations" not in tables


class TestVisualDecisionShape:
    def test_an_approved_visual_review_requires_a_description(self) -> None:
        base = make_decision()

        with pytest.raises(ValueError, match="accessible_description"):
            base.model_validate(
                base.model_dump() | {"kind": "visual", "accessible_description": None}
            )

    def test_a_rejected_visual_review_needs_no_description(self) -> None:
        base = make_decision(outcome=EvidenceReviewOutcome.REJECTED)

        revised = base.model_validate(base.model_dump() | {"kind": "visual"})

        assert revised.accessible_description is None


class TestReviewerIdentity:
    def test_an_email_address_is_refused_as_a_reviewer_id(self) -> None:
        with pytest.raises(ValueError, match="opaque local identifier"):
            ReviewerIdentity(reviewer_id="person@example.com", role="content_reviewer")

    def test_an_opaque_identifier_is_accepted(self) -> None:
        assert ReviewerIdentity(reviewer_id="reviewer-1", role="curator").reviewer_id
