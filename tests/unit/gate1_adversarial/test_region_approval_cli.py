"""Acceptance coverage for G1-FX-08's approval-CLI contract.

``LINCHPIN_TRACEABILITY.md`` names this row's tests at
``tests/linchpin/test_region_approval_cli.py``. That exact path is not
usable: ``FrozenFixtureAssembler._verify_tree`` (the same exact-tree check
G1-FX-06 depends on) admits only files the manifest's
``fixture_path_hash_inventory`` pins, plus the manifest itself -- any other
file placed directly under ``tests/linchpin/``, including a ``.py`` test
module and its ``__pycache__`` bytecode, makes every frozen-fixture load in
this repository fail with "the fixture tree differs from its frozen
inventory." Pinning a new path there is a fixture re-freeze, which requires
Alan's explicit approval and was not sought for this bounded repair. This
file lives here instead, and
``docs/plans/ccna-mastery-micro-lab/PROPOSED_G1-FX-07_SPEC_DIFF.md``'s
sibling traceability note (see the landing report) proposes updating the
documented test identity to this path rather than silently leaving the
named one both unfulfilled and structurally impossible.

Neither named test existed before this repair (independent review,
2026-07-28). Gate 1's own G1-FX-08 self-test now calls the exact same
``record_region_approval`` function these tests exercise (see
``personal_lms.evidence_review.authority``), so there is one code path
proven by both the gate and this acceptance suite, never two copies that
could silently drift.

These tests load the real frozen fixture tree read-only and approve
against a temporary, isolated SQLite store created for the test and
destroyed when it ends. Nothing here is a fixture re-freeze: no byte under
``tests/linchpin/sources``, ``tests/linchpin/packs``,
``tests/linchpin/queries``, ``tests/linchpin/learners``,
``tests/linchpin/expected``, ``tests/linchpin/schemas``, or
``fixture-manifest.yaml`` is written; each is only read for its
already-frozen, already-reviewed content, exactly as ``ccna-lab gate
evidence`` reads it.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_lms.domain.evidence_review import EvidenceReviewDecision, EvidenceReviewOutcome
from personal_lms.domain.objective_packs import EvidenceRegion, ObjectivePack, SourceArtifactRef
from personal_lms.evidence_review.authority import (
    record_region_approval,
    review_kind_for,
    verify_decision,
)
from personal_lms.evidence_review.service import EvidenceReviewService
from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
from personal_lms.objective_packs.linchpin_fixture import load_frozen_fixture
from personal_lms.objective_packs.loader import PackFileReader

# These tests load the real frozen fixture, whose manifest is YAML, so they
# need the optional ccna-lab extra's safe decoder. The lightweight core
# install has no decoder and therefore nothing to prove here.
pytest.importorskip("yaml", reason="requires the ccna-lab extra (uv sync --extra ccna-lab)")

PROJECT_ROOT = Path(__file__).parents[3]
FIXTURE_DIRECTORY = "tests/linchpin"

#: The reviewer identity these acceptance tests sign with. Not Alan, and
#: never persisted anywhere but the temporary store this file creates and
#: destroys -- this is a mechanism proof, not a technical-content approval.
_TEST_REVIEWER_ID = "linchpin-acceptance-test"


def _load_pack() -> ObjectivePack:
    reader = PackFileReader(roots=[PROJECT_ROOT])
    return load_frozen_fixture(reader, fixture_directory=FIXTURE_DIRECTORY).pack


def _candidate_region_and_artifact(
    pack: ObjectivePack,
) -> tuple[EvidenceRegion, SourceArtifactRef]:
    for region in sorted(pack.evidence_regions, key=lambda item: item.evidence_id):
        artifact = pack.sources_by_id.get(region.source_id)
        if artifact is not None:
            return region, artifact
    raise AssertionError("the frozen fixture defines no region bound to a source artifact")


def _persist_and_reread(
    *,
    pack: ObjectivePack,
    region: EvidenceRegion,
    artifact: SourceArtifactRef,
    decided_at: datetime,
) -> tuple[EvidenceReviewDecision, EvidenceReviewDecision | None]:
    """Approve through the real CLI path in an isolated temp store, then
    read it back through a fresh connection -- never the object just built."""
    with tempfile.TemporaryDirectory(prefix="linchpin-fx08-") as tmp:
        database_path = str(Path(tmp) / "approval.sqlite3")
        write_repository = SQLiteEvidenceReviewRepository.open(database_path)
        write_repository.initialize_schema()
        try:
            recorded = record_region_approval(
                pack=pack,
                region=region,
                artifact=artifact,
                review_service=EvidenceReviewService(write_repository),
                reviewer_id=_TEST_REVIEWER_ID,
                reviewer_role="content_reviewer",
                outcome=EvidenceReviewOutcome.APPROVED,
                reason="linchpin acceptance proof; isolated temporary store, never persisted",
                accessible_description=region.accessible_description,
                decided_at=decided_at,
            )
        finally:
            write_repository.close()

        read_repository = SQLiteEvidenceReviewRepository.open(database_path)
        try:
            reread = EvidenceReviewService(read_repository).current_decision_for_subject(
                pack=pack, region=region
            )
        finally:
            read_repository.close()

    return recorded, reread


def test_approval_cli_binds_decision_to_exact_region() -> None:
    """G1-FX-08: the bounded approval CLI binds reviewer, decision, exact
    source/region hash, correction/accessibility text, and timestamp -- and
    the persisted, freshly re-read record actually authorizes that exact
    subject through the governed read path."""
    pack = _load_pack()
    region, artifact = _candidate_region_and_artifact(pack)
    decided_at = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

    recorded, reread = _persist_and_reread(
        pack=pack, region=region, artifact=artifact, decided_at=decided_at
    )

    assert reread is not None
    assert reread.decision_id == recorded.decision_id
    assert reread.evidence_id == region.evidence_id
    assert reread.source_id == artifact.source_id
    assert reread.source_sha256 == artifact.sha256
    assert reread.kind == review_kind_for(region)
    assert reread.reviewer.reviewer_id == _TEST_REVIEWER_ID
    assert reread.decided_at == decided_at
    assert reread.accessible_description == region.accessible_description

    verdict = verify_decision(reread, pack=pack, region=region, artifact=artifact)
    assert verdict.authorized
    assert verdict.reason == "approved"


def test_stale_region_approval_fails() -> None:
    """G1-FX-08: a decision that no longer matches its region's current
    content is refused at read time, even though it was validly recorded
    and persisted against what the region held at approval time."""
    pack = _load_pack()
    region, artifact = _candidate_region_and_artifact(pack)
    decided_at = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

    _, reread = _persist_and_reread(
        pack=pack, region=region, artifact=artifact, decided_at=decided_at
    )
    assert reread is not None

    is_visual = review_kind_for(region).value == "visual"
    stale_region = region.model_copy(
        update=(
            {"accessible_description": (region.accessible_description or "") + " (edited)"}
            if is_visual
            else {"exact_text": (region.exact_text or "") + " (edited)"}
        )
    )

    verdict = verify_decision(reread, pack=pack, region=stale_region, artifact=artifact)
    assert not verdict.authorized
