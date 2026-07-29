"""Persisted decisions are the only review authority (review findings #2–#5, #18, #25).

The first implementation let an *authoring* pack file declare
``review_state=approved`` and ``trust_status=trusted``, and the
eligibility policy believed it. That made the entire evidence-review
boundary decorative: a fixture could approve itself, which is exactly the
property the boundary exists to prevent.

It also accepted ``content_sha256`` as unchecked authority — a pack could
state any digest it liked for its own content.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from personal_lms.domain.evidence_review import (
    EvidenceReviewDecision,
    EvidenceReviewKind,
    EvidenceReviewOutcome,
    ReviewerIdentity,
    compute_review_subject_digest,
    derive_decision_id,
)
from personal_lms.domain.objective_packs import (
    EligibilityState,
    PermittedUse,
    ValidationReasonCode,
)
from personal_lms.evidence_review.authority import EvidenceAuthoritySnapshot
from personal_lms.evidence_review.errors import EvidenceReviewContractError
from personal_lms.evidence_review.service import EvidenceReviewService
from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
from personal_lms.objective_packs.eligibility import EvidenceEligibility, EvidencePolicy

from ..objective_packs._helpers import (
    OBJECTIVE_REF,
    OTHER_OBJECTIVE_REF,
    make_image_region,
    make_pack,
    make_source,
    make_text_region,
)

REVIEWED_AT = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)


@pytest.fixture
def repository() -> SQLiteEvidenceReviewRepository:
    repo = SQLiteEvidenceReviewRepository.open(":memory:")
    repo.initialize_schema()
    return repo


@pytest.fixture
def service(repository: SQLiteEvidenceReviewRepository) -> EvidenceReviewService:
    return EvidenceReviewService(repository)


@pytest.fixture
def policy() -> EvidencePolicy:
    return EvidencePolicy(
        policy_version="test-1.0",
        objective_ref=OBJECTIVE_REF,
        requested_use=PermittedUse.LOCAL_TEACH,
    )


def build_decision(
    *,
    pack,  # type: ignore[no-untyped-def]
    region,  # type: ignore[no-untyped-def]
    artifact,  # type: ignore[no-untyped-def]
    outcome: EvidenceReviewOutcome = EvidenceReviewOutcome.APPROVED,
    kind: EvidenceReviewKind | None = None,
    accessible_description: str | None = None,
    decided_at: datetime = REVIEWED_AT,
    reviewer_id: str = "reviewer-1",
    supersedes=None,  # type: ignore[no-untyped-def]
    subject_digest: str | None = None,
) -> EvidenceReviewDecision:
    resolved_kind = (
        kind
        if kind is not None
        else (
            EvidenceReviewKind.VISUAL
            if region.selector.kind == "image_region"
            else EvidenceReviewKind.TEXT
        )
    )
    description = (
        accessible_description
        if accessible_description is not None
        else region.accessible_description
    )
    digest = (
        subject_digest
        if subject_digest is not None
        else compute_review_subject_digest(
            pack_id=pack.manifest.pack_id,
            pack_version=pack.manifest.pack_version,
            objective_ref=pack.objective_ref,
            evidence_id=region.evidence_id,
            kind=resolved_kind,
            source_id=artifact.source_id,
            source_sha256=artifact.sha256,
            selector=region.selector,
            resolved_content=region.review_content_for(resolved_kind),
        )
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
        kind=resolved_kind,
        outcome=outcome,
        subject_digest=digest,
        source_sha256=artifact.sha256,
        reviewer=ReviewerIdentity(reviewer_id=reviewer_id, role="content_reviewer"),
        reason="Synthetic draft_for_human_review test decision.",
        accessible_description=description if resolved_kind is EvidenceReviewKind.VISUAL else None,
        decided_at=decided_at,
        supersedes_decision_id=supersedes,
    )


class TestAuthoredFlagsGrantNothing:
    def test_pack_authored_approval_cannot_make_evidence_eligible(
        self, service: EvidenceReviewService, policy: EvidencePolicy
    ) -> None:
        """The heart of the finding: a fixture must not approve itself."""
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        decision = EvidenceEligibility(policy, authority=snapshot).evaluate(region, artifact)

        assert decision.state is not EligibilityState.ELIGIBLE
        assert decision.reason_code is ValidationReasonCode.EVIDENCE_NOT_REVIEWED

    def test_the_same_evidence_becomes_eligible_once_a_decision_is_persisted(
        self, service: EvidenceReviewService, policy: EvidencePolicy
    ) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        service.record_decision(
            build_decision(pack=pack, region=region, artifact=artifact),
            pack=pack,
            region=region,
            artifact=artifact,
        )
        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        decision = EvidenceEligibility(policy, authority=snapshot).evaluate(region, artifact)

        assert decision.state is EligibilityState.ELIGIBLE

    def test_authored_trust_without_a_decision_is_not_trust(
        self, service: EvidenceReviewService, policy: EvidencePolicy
    ) -> None:
        pack = make_pack()
        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.approved_evidence_ids == ()


class TestNonApprovalsGrantNothing:
    @pytest.mark.parametrize(
        "outcome",
        [EvidenceReviewOutcome.REJECTED, EvidenceReviewOutcome.NEEDS_CHANGES],
    )
    def test_an_explicit_non_approval_is_not_an_approval(
        self,
        service: EvidenceReviewService,
        policy: EvidencePolicy,
        outcome: EvidenceReviewOutcome,
    ) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        service.record_decision(
            build_decision(pack=pack, region=region, artifact=artifact, outcome=outcome),
            pack=pack,
            region=region,
            artifact=artifact,
        )
        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.approved_evidence_ids == ()

    def test_a_rejected_review_cannot_produce_visual_review_recorded(
        self, service: EvidenceReviewService
    ) -> None:
        pack = _image_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        service.record_decision(
            build_decision(
                pack=pack,
                region=region,
                artifact=artifact,
                outcome=EvidenceReviewOutcome.REJECTED,
            ),
            pack=pack,
            region=region,
            artifact=artifact,
        )
        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.approved_visual_evidence_ids == ()


class TestVisualReviewIsRequiredAndSpecific:
    def test_a_pack_with_no_image_region_cannot_satisfy_the_visual_check(
        self, service: EvidenceReviewService
    ) -> None:
        """Zero image regions is not vacuous success — it is missing evidence."""
        pack = make_pack()
        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.required_visual_evidence_ids == ()
        assert snapshot.visual_review_satisfied is False

    def test_a_text_decision_cannot_approve_an_image_region(
        self, service: EvidenceReviewService
    ) -> None:
        pack = _image_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]

        with pytest.raises(EvidenceReviewContractError, match="kind"):
            service.record_decision(
                build_decision(
                    pack=pack,
                    region=region,
                    artifact=artifact,
                    kind=EvidenceReviewKind.TEXT,
                ),
                pack=pack,
                region=region,
                artifact=artifact,
            )

    def test_a_decision_describing_different_content_is_refused(
        self, service: EvidenceReviewService
    ) -> None:
        pack = _image_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]

        with pytest.raises(EvidenceReviewContractError):
            service.record_decision(
                build_decision(
                    pack=pack,
                    region=region,
                    artifact=artifact,
                    accessible_description="A different description than the region carries.",
                ),
                pack=pack,
                region=region,
                artifact=artifact,
            )

    def test_an_approved_visual_decision_satisfies_the_check(
        self, service: EvidenceReviewService
    ) -> None:
        pack = _image_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        service.record_decision(
            build_decision(pack=pack, region=region, artifact=artifact),
            pack=pack,
            region=region,
            artifact=artifact,
        )
        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.visual_review_satisfied is True


class TestSubjectDigestCoversEverythingThatMatters:
    @pytest.mark.parametrize(
        "mutation",
        ["exact_text", "selector", "source_id", "source_sha256", "objective_ref", "pack_version"],
    )
    def test_changing_any_bound_component_invalidates_the_digest(self, mutation: str) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        base = dict(
            pack_id=pack.manifest.pack_id,
            pack_version=pack.manifest.pack_version,
            objective_ref=pack.objective_ref,
            evidence_id=region.evidence_id,
            kind=EvidenceReviewKind.TEXT,
            source_id=artifact.source_id,
            source_sha256=artifact.sha256,
            selector=region.selector,
            resolved_content=region.exact_text or "",
        )
        original = compute_review_subject_digest(**base)

        mutated = dict(base)
        if mutation == "exact_text":
            mutated["resolved_content"] = "different content entirely"
        elif mutation == "selector":
            mutated["selector"] = region.selector.model_copy(update={"page_number": 2})
        elif mutation == "source_id":
            mutated["source_id"] = "src-other"
        elif mutation == "source_sha256":
            mutated["source_sha256"] = "b" * 64
        elif mutation == "objective_ref":
            mutated["objective_ref"] = OTHER_OBJECTIVE_REF
        else:
            mutated["pack_version"] = "9.9"

        assert compute_review_subject_digest(**mutated) != original

    def test_a_decision_whose_digest_no_longer_matches_authorizes_nothing(
        self, service: EvidenceReviewService, policy: EvidencePolicy
    ) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        service.record_decision(
            build_decision(pack=pack, region=region, artifact=artifact),
            pack=pack,
            region=region,
            artifact=artifact,
        )

        edited = make_text_region(text="The synthetic passage was revised after review.")
        edited_pack = make_pack(regions=(edited,))
        snapshot = EvidenceAuthoritySnapshot.build(pack=edited_pack, review_service=service)

        assert snapshot.approved_evidence_ids == ()

    def test_an_authored_content_hash_is_recomputed_not_believed(self) -> None:
        """A pack stating a false digest for its own text must not be trusted."""
        forged = make_text_region().model_copy(update={"content_sha256": "c" * 64})
        pack = make_pack(regions=(forged,))

        from personal_lms.objective_packs.validation import ObjectivePackValidator

        report = ObjectivePackValidator().validate(pack)

        assert ValidationReasonCode.CONTENT_DIGEST_MISMATCH.value in report.reason_codes


class TestLinearSupersession:
    def test_a_correction_supersedes_the_current_leaf(self, service: EvidenceReviewService) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        first = service.record_decision(
            build_decision(
                pack=pack,
                region=region,
                artifact=artifact,
                outcome=EvidenceReviewOutcome.REJECTED,
            ),
            pack=pack,
            region=region,
            artifact=artifact,
        )
        second = build_decision(
            pack=pack,
            region=region,
            artifact=artifact,
            decided_at=REVIEWED_AT + timedelta(hours=1),
            supersedes=first.decision_id,
        )
        service.record_decision(second, pack=pack, region=region, artifact=artifact)

        assert service.current_decision(region.evidence_id) == second

    def test_a_second_unsuperseded_root_is_rejected(self, service: EvidenceReviewService) -> None:
        """Two roots means two "current" decisions — an ambiguous authority."""
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        service.record_decision(
            build_decision(pack=pack, region=region, artifact=artifact),
            pack=pack,
            region=region,
            artifact=artifact,
        )

        with pytest.raises(EvidenceReviewContractError, match="root"):
            service.record_decision(
                build_decision(
                    pack=pack,
                    region=region,
                    artifact=artifact,
                    reviewer_id="reviewer-2",
                    decided_at=REVIEWED_AT + timedelta(hours=2),
                ),
                pack=pack,
                region=region,
                artifact=artifact,
            )

    def test_a_fork_is_rejected(self, service: EvidenceReviewService) -> None:
        """Two children of one decision is a branch, not a history."""
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        root = service.record_decision(
            build_decision(pack=pack, region=region, artifact=artifact),
            pack=pack,
            region=region,
            artifact=artifact,
        )
        service.record_decision(
            build_decision(
                pack=pack,
                region=region,
                artifact=artifact,
                reviewer_id="reviewer-2",
                decided_at=REVIEWED_AT + timedelta(hours=1),
                supersedes=root.decision_id,
            ),
            pack=pack,
            region=region,
            artifact=artifact,
        )

        with pytest.raises(EvidenceReviewContractError, match="superseded"):
            service.record_decision(
                build_decision(
                    pack=pack,
                    region=region,
                    artifact=artifact,
                    reviewer_id="reviewer-3",
                    decided_at=REVIEWED_AT + timedelta(hours=2),
                    supersedes=root.decision_id,
                ),
                pack=pack,
                region=region,
                artifact=artifact,
            )

    def test_superseding_a_non_leaf_is_rejected_by_a_database_constraint(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        """Enforced in the store, not only in the service."""
        import sqlite3

        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        root = repository.append(build_decision(pack=pack, region=region, artifact=artifact))
        repository.append(
            build_decision(
                pack=pack,
                region=region,
                artifact=artifact,
                reviewer_id="reviewer-2",
                decided_at=REVIEWED_AT + timedelta(hours=1),
                supersedes=root.decision_id,
            )
        )

        with pytest.raises((EvidenceReviewContractError, sqlite3.IntegrityError)):
            repository.append(
                build_decision(
                    pack=pack,
                    region=region,
                    artifact=artifact,
                    reviewer_id="reviewer-3",
                    decided_at=REVIEWED_AT + timedelta(hours=3),
                    supersedes=root.decision_id,
                )
            )


def _image_pack():  # type: ignore[no-untyped-def]
    """A pack whose single region is an image, for visual-review tests."""
    from ..objective_packs._helpers import PNG_BYTES

    region = make_image_region()
    artifact = make_source(source_id="src-png", payload=PNG_BYTES, media_type="image/png")
    return make_pack(sources=(artifact,), regions=(region,))
