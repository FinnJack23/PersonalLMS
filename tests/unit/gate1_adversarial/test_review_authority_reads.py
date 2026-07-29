"""Read-time review authority, exercised through persisted decisions.

Covers red items 11, 12, 34, 35 and reviewer findings D and F.

Earlier probes asserted what authority *would* reject without persisting
the malformed decision and building a snapshot, which proves nothing about
the read path. Every test here writes the decision to the repository first,
then asks ``EvidenceAuthoritySnapshot`` what it authorizes.

Finding D is the subtle one: ``resolved_content`` preferred ``exact_text``,
so an image region carrying both a caption and an accessible description
bound its review subject to the caption. Editing the description — the text
a learner actually reads — left the approval intact.
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
from personal_lms.evidence_review.authority import EvidenceAuthoritySnapshot, subject_digest_for
from personal_lms.evidence_review.service import EvidenceReviewService
from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository

from ..objective_packs._helpers import (
    OTHER_OBJECTIVE_REF,
    PNG_BYTES,
    make_image_region,
    make_manifest,
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


def image_pack():  # type: ignore[no-untyped-def]
    artifact = make_source(source_id="src-png", payload=PNG_BYTES, media_type="image/png")
    return make_pack(sources=(artifact,), regions=(make_image_region(),))


def decision_for(
    pack,  # type: ignore[no-untyped-def]
    region,  # type: ignore[no-untyped-def]
    artifact,  # type: ignore[no-untyped-def]
    *,
    outcome: EvidenceReviewOutcome = EvidenceReviewOutcome.APPROVED,
    kind: EvidenceReviewKind | None = None,
    digest: str | None = None,
    pack_id: str | None = None,
    pack_version: str | None = None,
    objective_ref: str | None = None,
    source_id: str | None = None,
    source_sha256: str | None = None,
    accessible_description: str | None = None,
    decided_at: datetime = REVIEWED_AT,
    supersedes=None,  # type: ignore[no-untyped-def]
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
    resolved_digest = digest if digest is not None else subject_digest_for(pack, region, artifact)
    return EvidenceReviewDecision(
        decision_id=derive_decision_id(
            evidence_id=region.evidence_id,
            subject_digest=resolved_digest,
            reviewer_id="reviewer-1",
            decided_at=decided_at.isoformat(),
        ),
        evidence_id=region.evidence_id,
        source_id=source_id if source_id is not None else artifact.source_id,
        pack_id=pack_id if pack_id is not None else pack.manifest.pack_id,
        pack_version=pack_version if pack_version is not None else pack.manifest.pack_version,
        objective_ref=objective_ref if objective_ref is not None else pack.objective_ref,
        kind=resolved_kind,
        outcome=outcome,
        subject_digest=resolved_digest,
        source_sha256=source_sha256 if source_sha256 is not None else artifact.sha256,
        reviewer=ReviewerIdentity(reviewer_id="reviewer-1", role="content_reviewer"),
        reason="Synthetic draft_for_human_review probe.",
        accessible_description=(
            accessible_description
            if accessible_description is not None
            else (
                region.accessible_description
                if resolved_kind is EvidenceReviewKind.VISUAL
                else None
            )
        ),
        decided_at=decided_at,
        supersedes_decision_id=supersedes,
    )


class TestMalformedPersistedDecisionsAuthorizeNothing:
    """Each decision is written directly to the store, bypassing the service.

    The service already refuses these at write time; the point here is that
    the *read* path refuses them too, so a decision inserted by any other
    means still cannot authorize anything.
    """

    @pytest.mark.parametrize(
        "mutation",
        ["wrong_kind", "wrong_pack", "wrong_version", "wrong_objective", "wrong_source"],
    )
    def test_a_mismatched_persisted_decision_is_not_authority(
        self,
        repository: SQLiteEvidenceReviewRepository,
        service: EvidenceReviewService,
        mutation: str,
    ) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        kwargs: dict[str, object] = {}
        if mutation == "wrong_kind":
            kwargs["kind"] = EvidenceReviewKind.VISUAL
            kwargs["accessible_description"] = "a description for a text region"
        elif mutation == "wrong_pack":
            kwargs["pack_id"] = "some-other-pack"
        elif mutation == "wrong_version":
            kwargs["pack_version"] = "9.9"
        elif mutation == "wrong_objective":
            kwargs["objective_ref"] = OTHER_OBJECTIVE_REF
        else:
            kwargs["source_id"] = "src-other"

        repository.append(decision_for(pack, region, artifact, **kwargs))  # type: ignore[arg-type]
        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.approved_evidence_ids == ()

    @pytest.mark.parametrize(
        "outcome", [EvidenceReviewOutcome.REJECTED, EvidenceReviewOutcome.NEEDS_CHANGES]
    )
    def test_a_non_approval_is_not_authority(
        self,
        repository: SQLiteEvidenceReviewRepository,
        service: EvidenceReviewService,
        outcome: EvidenceReviewOutcome,
    ) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        repository.append(decision_for(pack, region, artifact, outcome=outcome))

        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.approved_evidence_ids == ()

    def test_a_stale_digest_is_not_authority(
        self, repository: SQLiteEvidenceReviewRepository, service: EvidenceReviewService
    ) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        repository.append(decision_for(pack, region, artifact, digest="f" * 64))

        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.approved_evidence_ids == ()
        assert region.evidence_id in snapshot.stale_evidence_ids

    def test_a_superseded_approval_is_not_authority(
        self, repository: SQLiteEvidenceReviewRepository, service: EvidenceReviewService
    ) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        root = repository.append(decision_for(pack, region, artifact))
        repository.append(
            decision_for(
                pack,
                region,
                artifact,
                outcome=EvidenceReviewOutcome.REJECTED,
                decided_at=REVIEWED_AT + timedelta(hours=1),
                supersedes=root.decision_id,
            )
        )

        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.approved_evidence_ids == ()

    def test_a_matching_approval_is_authority(
        self, repository: SQLiteEvidenceReviewRepository, service: EvidenceReviewService
    ) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        repository.append(decision_for(pack, region, artifact))

        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.approved_evidence_ids == (region.evidence_id,)


class TestVisualDescriptionAuthority:
    def test_editing_the_description_revokes_approval_even_with_a_caption(
        self, repository: SQLiteEvidenceReviewRepository, service: EvidenceReviewService
    ) -> None:
        """Reviewer finding D: exact_text must not shadow the description."""
        pack = image_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        captioned = region.model_copy(update={"exact_text": "Figure 1. A caption."})
        captioned_pack = make_pack(sources=(artifact,), regions=(captioned,))
        repository.append(decision_for(captioned_pack, captioned, artifact))

        assert EvidenceAuthoritySnapshot.build(
            pack=captioned_pack, review_service=service
        ).approved_visual_evidence_ids == (captioned.evidence_id,)

        edited = captioned.model_copy(
            update={"accessible_description": "A materially different description."}
        )
        edited_pack = make_pack(sources=(artifact,), regions=(edited,))

        snapshot = EvidenceAuthoritySnapshot.build(pack=edited_pack, review_service=service)

        assert snapshot.approved_visual_evidence_ids == ()

    def test_a_decision_approving_a_different_description_is_not_authority(
        self, repository: SQLiteEvidenceReviewRepository, service: EvidenceReviewService
    ) -> None:
        pack = image_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        repository.append(
            decision_for(pack, region, artifact, accessible_description="Not what the pack ships.")
        )

        snapshot = EvidenceAuthoritySnapshot.build(pack=pack, review_service=service)

        assert snapshot.approved_visual_evidence_ids == ()

    def test_a_pack_with_no_image_region_cannot_satisfy_visual_review(
        self, service: EvidenceReviewService
    ) -> None:
        snapshot = EvidenceAuthoritySnapshot.build(pack=make_pack(), review_service=service)

        assert snapshot.visual_review_satisfied is False


class TestChainScoping:
    def test_history_is_scoped_by_the_full_logical_subject(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        """Two packs may legitimately review the same evidence id.

        Scoping the chain by ``evidence_id`` alone made the second pack's
        root decision collide with the first pack's, so an approval in one
        pack blocked review in another.
        """
        first = make_pack()
        second = make_pack(manifest=make_manifest(pack_id="second-pack"))
        region, artifact = first.evidence_regions[0], first.source_artifacts[0]

        repository.append(decision_for(first, region, artifact))
        repository.append(decision_for(second, region, artifact))

        assert (
            repository.current_for_subject(
                evidence_id=region.evidence_id,
                pack_id=first.manifest.pack_id,
                pack_version=first.manifest.pack_version,
                objective_ref=first.objective_ref,
            )
            is not None
        )
        assert (
            repository.current_for_subject(
                evidence_id=region.evidence_id,
                pack_id=second.manifest.pack_id,
                pack_version=second.manifest.pack_version,
                objective_ref=second.objective_ref,
            )
            is not None
        )

    def test_a_second_root_within_one_subject_is_still_refused(
        self, repository: SQLiteEvidenceReviewRepository
    ) -> None:
        from personal_lms.evidence_review.errors import EvidenceReviewContractError

        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        repository.append(decision_for(pack, region, artifact))

        with pytest.raises(EvidenceReviewContractError, match="root"):
            repository.append(
                decision_for(pack, region, artifact, decided_at=REVIEWED_AT + timedelta(hours=5))
            )


class TestConcurrentIdenticalRetry:
    def test_two_connections_appending_the_same_decision_both_succeed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Reviewer finding C: the race, not the sequential case.

        Sequential idempotency already worked. Two connections racing the
        same byte-identical decision produced a contract error for the
        loser; it must reread the winning row and return it instead.
        """
        database = tmp_path / "review.sqlite3"
        first = SQLiteEvidenceReviewRepository.open(str(database))
        first.initialize_schema()
        second = SQLiteEvidenceReviewRepository.open(str(database))

        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        decision = decision_for(pack, region, artifact)

        try:
            assert first.append(decision) == decision
            assert second.append(decision) == decision
        finally:
            first.close()
            second.close()

    def test_a_conflicting_decision_on_the_same_id_still_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from personal_lms.evidence_review.errors import EvidenceReviewImmutableError

        database = tmp_path / "review.sqlite3"
        repository = SQLiteEvidenceReviewRepository.open(str(database))
        repository.initialize_schema()

        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        decision = decision_for(pack, region, artifact)
        repository.append(decision)

        try:
            with pytest.raises(EvidenceReviewImmutableError):
                repository.append(
                    decision.model_copy(update={"reason": "a different reason entirely"})
                )
        finally:
            repository.close()


class TestTextRegionsStillBindContent:
    def test_editing_text_revokes_a_text_approval(
        self, repository: SQLiteEvidenceReviewRepository, service: EvidenceReviewService
    ) -> None:
        pack = make_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        repository.append(decision_for(pack, region, artifact))

        edited = make_text_region(text="A revised synthetic passage.")
        edited_pack = make_pack(regions=(edited,))

        snapshot = EvidenceAuthoritySnapshot.build(pack=edited_pack, review_service=service)

        assert snapshot.approved_evidence_ids == ()

    def test_the_subject_digest_covers_the_shipped_description_for_images(self) -> None:
        pack = image_pack()
        region, artifact = pack.evidence_regions[0], pack.source_artifacts[0]
        captioned = region.model_copy(update={"exact_text": "caption"})

        with_caption = compute_review_subject_digest(
            pack_id=pack.manifest.pack_id,
            pack_version=pack.manifest.pack_version,
            objective_ref=pack.objective_ref,
            evidence_id=captioned.evidence_id,
            kind=EvidenceReviewKind.VISUAL,
            source_id=artifact.source_id,
            source_sha256=artifact.sha256,
            selector=captioned.selector,
            resolved_content=captioned.review_content_for(EvidenceReviewKind.VISUAL),
        )
        edited = captioned.model_copy(update={"accessible_description": "different"})
        after_edit = compute_review_subject_digest(
            pack_id=pack.manifest.pack_id,
            pack_version=pack.manifest.pack_version,
            objective_ref=pack.objective_ref,
            evidence_id=edited.evidence_id,
            kind=EvidenceReviewKind.VISUAL,
            source_id=artifact.source_id,
            source_sha256=artifact.sha256,
            selector=edited.selector,
            resolved_content=edited.review_content_for(EvidenceReviewKind.VISUAL),
        )

        assert with_caption != after_edit
