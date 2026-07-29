"""Scoring independence, coverage, and extraction integrity.

Covers red items 18–26 and reviewer findings B and E.

Two reproduced defects:

- ``independence_group`` was an arbitrary author label, so relabelling one
  evidence record into three groups multiplied its contribution by 1.20.
  With five factors of 0.94 that lifts a claim from 7339 (excluded) to
  8806 (cleared for answer keys) without adding a single new source.
- ``recompute_coverage`` counted ``claim.is_answer_bearing`` while the gate
  used the *derived* set, so a pack could report zero answer-bearing claims
  while the validator gated one.

Reviewer finding B is **rejected** and pinned as safe behavior: the earlier
probe called ``verify_source_bytes``, which only admits bytes. PDF *text
extraction* was already, and remains, honestly blocked.
"""

from __future__ import annotations

import zlib

import pytest

from personal_lms.extraction.artifacts import ExtractionOutcome, inspect_png_dimensions
from personal_lms.extraction.local_fixture import LocalFixtureExtractor
from personal_lms.objective_packs.loader import PackFileReader
from personal_lms.objective_packs.scoring import ClaimEvidencePolicy
from personal_lms.objective_packs.validation import ObjectivePackValidator

from ..objective_packs._helpers import (
    PDF_BYTES,
    PNG_BYTES,
    make_claim,
    make_image_region,
    make_pack,
    make_source,
    make_support,
    make_text_region,
    write_pack_directory,
)


def png_chunk(chunk_type: bytes, payload: bytes, *, crc: int | None = None) -> bytes:
    checksum = zlib.crc32(chunk_type + payload) if crc is None else crc
    return len(payload).to_bytes(4, "big") + chunk_type + payload + checksum.to_bytes(4, "big")


IHDR = (64).to_bytes(4, "big") + (48).to_bytes(4, "big") + bytes((8, 0, 0, 0, 0))
RAW_SCANLINES = b"".join(b"\x00" + bytes(64) for _ in range(48))
SIGNATURE = b"\x89PNG\r\n\x1a\n"


class TestIndependenceIsNotAnAuthorLabel:
    def test_relabelling_one_record_into_three_groups_does_not_inflate(self) -> None:
        """The reproduced exploit: 7339 -> 8806 across the 8500 floor."""
        policy = ClaimEvidencePolicy()
        honest = make_claim(support=(make_support(evidence_id="ev-1", strength=9_400),))
        relabelled = make_claim(
            support=tuple(
                make_support(
                    support_id=f"sup-{index}",
                    evidence_id="ev-1",
                    strength=9_400,
                    independence_group=f"invented-{index}",
                )
                for index in range(3)
            )
        )

        assert (
            policy.recompute_score(relabelled).score_basis_points
            == policy.recompute_score(honest).score_basis_points
        )

    def test_genuinely_distinct_evidence_still_corroborates(self) -> None:
        policy = ClaimEvidencePolicy()
        alone = make_claim(support=(make_support(evidence_id="ev-1", strength=9_400),))
        corroborated = make_claim(
            support=(
                make_support(support_id="s1", evidence_id="ev-1", strength=9_400),
                make_support(support_id="s2", evidence_id="ev-2", strength=9_400),
            )
        )

        assert (
            policy.recompute_score(corroborated).score_basis_points
            > policy.recompute_score(alone).score_basis_points
        )

    def test_independence_is_derived_from_evidence_identity(self) -> None:
        """Two labels over one evidence record collapse; the label is advisory."""
        policy = ClaimEvidencePolicy()
        claim = make_claim(
            support=(
                make_support(support_id="s1", evidence_id="ev-1", independence_group="a"),
                make_support(support_id="s2", evidence_id="ev-1", independence_group="b"),
            )
        )

        assert policy.recompute_score(claim).contributing_groups == ("ev-1",)

    def test_a_declared_label_cannot_split_one_evidence_record(self) -> None:
        policy = ClaimEvidencePolicy()
        claim = make_claim(
            support=tuple(
                make_support(support_id=f"s{i}", evidence_id="ev-1", independence_group=f"g{i}")
                for i in range(6)
            )
        )

        assert len(policy.recompute_score(claim).contributing_groups) == 1


class TestDesignArithmeticIsPreserved:
    def test_five_factors_of_8500_produce_4437(self) -> None:
        assert ClaimEvidencePolicy().score_support(make_support(strength=8_500)) == 4_437

    def test_three_independent_8500_groups_produce_5324(self) -> None:
        claim = make_claim(
            support=tuple(
                make_support(support_id=f"s{i}", evidence_id=f"ev-{i}", strength=8_500)
                for i in range(3)
            )
        )

        assert ClaimEvidencePolicy().recompute_score(claim).score_basis_points == 5_324

    def test_a_material_conflict_remains_an_explicit_block(self) -> None:
        result = ClaimEvidencePolicy().recompute_score(
            make_claim(support=(make_support(strength=10_000),), conflict_status="material")
        )

        assert result.blocked is True
        assert result.meets == 0


class TestSupportCarriesPolicyVersion:
    def test_claim_support_declares_its_calculation_policy_version(self) -> None:
        """``07-DATA-CONTRACTS.md`` puts it on ``ClaimSupport`` explicitly."""
        from personal_lms.domain.objective_packs import ClaimSupport

        assert "calculation_policy_version" in ClaimSupport.model_fields

    def test_support_scored_under_a_foreign_policy_version_is_reported(self) -> None:
        pack = make_pack(
            claims=(
                make_claim(support=(make_support(calculation_policy_version="some-other-policy"),)),
            )
        )

        from personal_lms.domain.objective_packs import ValidationReasonCode

        report = ObjectivePackValidator().validate(pack)

        assert ValidationReasonCode.CALCULATION_POLICY_MISMATCH.value in report.reason_codes


class TestDerivedAnswerBearingCoverage:
    def test_coverage_counts_the_derived_set_not_the_authored_flag(self) -> None:
        """Reviewer finding E: the counter disagreed with the gate."""
        pack = make_pack(claims=(make_claim(claim_id="claim-1", is_answer_bearing=False),))

        report = ObjectivePackValidator().validate(pack)

        assert report.answer_bearing_claim_ids == ("claim-1",)
        assert report.recomputed_coverage["answer_bearing_claims"] == 1

    def test_a_pack_declaring_the_authored_count_is_reported(self) -> None:
        from personal_lms.domain.objective_packs import ValidationReasonCode

        pack = make_pack(
            claims=(make_claim(claim_id="claim-1", is_answer_bearing=False),),
            declared_coverage={"answer_bearing_claims": 0},
        )

        report = ObjectivePackValidator().validate(pack)

        assert ValidationReasonCode.DECLARED_COVERAGE_MISMATCH.value in report.reason_codes


class TestPngIntegrity:
    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            (
                "invalid IHDR CRC",
                SIGNATURE
                + png_chunk(b"IHDR", IHDR, crc=0)
                + png_chunk(b"IDAT", zlib.compress(RAW_SCANLINES, 9))
                + png_chunk(b"IEND", b""),
            ),
            (
                "invalid IDAT CRC",
                SIGNATURE
                + png_chunk(b"IHDR", IHDR)
                + png_chunk(b"IDAT", zlib.compress(RAW_SCANLINES, 9), crc=0)
                + png_chunk(b"IEND", b""),
            ),
            (
                "empty IDAT",
                SIGNATURE
                + png_chunk(b"IHDR", IHDR)
                + png_chunk(b"IDAT", b"")
                + png_chunk(b"IEND", b""),
            ),
            (
                "undecodable IDAT",
                SIGNATURE
                + png_chunk(b"IHDR", IHDR)
                + png_chunk(b"IDAT", b"not a zlib stream")
                + png_chunk(b"IEND", b""),
            ),
            (
                "truncated final chunk",
                (
                    SIGNATURE
                    + png_chunk(b"IHDR", IHDR)
                    + png_chunk(b"IDAT", zlib.compress(RAW_SCANLINES, 9))
                    + png_chunk(b"IEND", b"")
                )[:-3],
            ),
            (
                "IDAT shorter than the declared canvas",
                SIGNATURE
                + png_chunk(b"IHDR", IHDR)
                + png_chunk(b"IDAT", zlib.compress(b"\x00" + bytes(64), 9))
                + png_chunk(b"IEND", b""),
            ),
        ],
    )
    def test_a_malformed_png_is_never_readable(self, label: str, payload: bytes) -> None:
        assert inspect_png_dimensions(payload) is None, f"{label} was accepted"

    def test_a_valid_png_is_readable(self) -> None:
        dimensions = inspect_png_dimensions(PNG_BYTES)

        assert dimensions is not None
        assert (dimensions.width, dimensions.height) == (64, 48)

    def test_a_malformed_png_never_reports_extracted(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        broken = (
            SIGNATURE
            + png_chunk(b"IHDR", IHDR)
            + png_chunk(b"IDAT", b"not a zlib stream")
            + png_chunk(b"IEND", b"")
        )
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "broken.png").write_bytes(broken)
        artifact = make_source(source_id="src-png", payload=broken, media_type="image/png")

        result = LocalFixtureExtractor(PackFileReader(roots=[tmp_path])).extract_region(
            make_image_region(image_payload=broken),
            artifact,
            relative_path="sources/broken.png",
        )

        assert result.outcome is ExtractionOutcome.MALFORMED_SOURCE


class TestPdfRemainsHonestlyBlocked:
    """Reviewer finding B, pinned as *correct* behavior rather than repaired."""

    def test_extract_region_reports_extractor_unavailable(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "a.pdf").write_bytes(PDF_BYTES)

        result = LocalFixtureExtractor(PackFileReader(roots=[tmp_path])).extract_region(
            make_text_region(), make_source(), relative_path="sources/a.pdf"
        )

        assert result.outcome is ExtractionOutcome.EXTRACTOR_UNAVAILABLE
        assert result.text is None

    def test_admitting_pdf_bytes_is_not_a_claim_that_text_was_extracted(self) -> None:
        """``verify_source_bytes`` admits bytes; it says nothing about text.

        Conflating the two is what produced the incorrect review finding.
        """
        from personal_lms.extraction.artifacts import verify_source_bytes

        from ..objective_packs._helpers import sha256_of

        admitted = verify_source_bytes(
            PDF_BYTES,
            expected_sha256=sha256_of(PDF_BYTES),
            expected_media_type="application/pdf",
        )

        assert admitted is ExtractionOutcome.EXTRACTED


class TestGroundingResultPreservesThePositionalApi:
    """``algorithm_provenance`` must not shift any field after it.

    It was inserted between ``calculation_policy_version`` and
    ``contributing_groups`` on the exported ``ClaimGroundingResult``
    dataclass. A positional caller built against the earlier eight-field
    signature would otherwise silently bind its ``contributing_groups``
    tuple to this ``str`` parameter instead — a type mismatch dataclasses
    do not check at runtime, so the corruption would be silent.
    """

    def test_the_original_eight_positional_fields_still_bind_by_position(self) -> None:
        from personal_lms.objective_packs.scoring import ClaimGroundingResult

        result = ClaimGroundingResult(
            "claim-1",  # claim_id
            8_500,  # score_basis_points
            "ccna-grounding-v1",  # calculation_policy_version
            ("ev-1",),  # contributing_groups
            (8_500,),  # group_scores
            False,  # blocked
            None,  # block_reason
            {"ev-1": 8_500},  # edge_scores
        )

        assert result.contributing_groups == ("ev-1",)
        assert result.group_scores == (8_500,)
        assert result.blocked is False
        assert result.edge_scores == {"ev-1": 8_500}

    def test_algorithm_provenance_is_keyword_only(self) -> None:
        from personal_lms.objective_packs.scoring import (
            CLAIM_SCORE_ALGORITHM_PROVENANCE,
            ClaimGroundingResult,
        )

        result = ClaimGroundingResult("claim-1", 8_500, "ccna-grounding-v1")
        assert result.algorithm_provenance == CLAIM_SCORE_ALGORITHM_PROVENANCE

        # algorithm_provenance is excluded from the positional sequence
        # entirely, so a 4th positional argument binds to the *next* field
        # (contributing_groups) exactly as it would have before this field
        # existed -- proof the original eight-field positional order is
        # undisturbed rather than merely "not erroring."
        fourth_positional = ClaimGroundingResult("claim-1", 8_500, "ccna-grounding-v1", ("ev-1",))
        assert fourth_positional.contributing_groups == ("ev-1",)
        assert fourth_positional.algorithm_provenance == CLAIM_SCORE_ALGORITHM_PROVENANCE

        explicit = ClaimGroundingResult(
            "claim-1", 8_500, "ccna-grounding-v1", algorithm_provenance="custom-provenance"
        )
        assert explicit.algorithm_provenance == "custom-provenance"


class TestPolicyAndProvenanceIdentifiersAreRequired:
    def test_an_empty_policy_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="nonempty calculation-policy identifier"):
            ClaimEvidencePolicy(policy_version="")

    def test_an_empty_algorithm_provenance_is_refused(self) -> None:
        with pytest.raises(ValueError, match="nonempty specification reference"):
            ClaimEvidencePolicy(algorithm_provenance="")

    def test_a_custom_policy_cannot_reuse_its_own_provenance_as_its_identity(self) -> None:
        """Not only the known default: any self-supplied provenance too."""
        with pytest.raises(ValueError, match="provenance, not a policy identifier"):
            ClaimEvidencePolicy(
                policy_version="custom-policy-x",
                algorithm_provenance="custom-policy-x",
                minor_conflict_penalty_basis_points=100,
            )

    def test_a_custom_policy_with_genuinely_distinct_provenance_is_accepted(self) -> None:
        policy = ClaimEvidencePolicy(
            policy_version="custom-policy-x",
            algorithm_provenance="custom-spec-y",
            minor_conflict_penalty_basis_points=100,
        )

        assert policy.policy_version == "custom-policy-x"
        assert policy.algorithm_provenance == "custom-spec-y"


class TestGroundingCheckReachesThePersistedReport:
    """G1-GO-06 must fail end to end on a policy mismatch, not only inside
    the transient ``ObjectivePackValidator`` result — and the persisted
    check must carry deterministic observed evidence.
    """

    @staticmethod
    def _run(tmp_path, *, declared_policy_version: str):  # type: ignore[no-untyped-def]
        import uuid

        from personal_lms.evidence_review.service import EvidenceReviewService
        from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
        from personal_lms.labs.ccna_mastery.wiring import build_ccna_mastery_use_case
        from personal_lms.objective_packs.loader import ObjectivePackLoader, PackFileReader

        root = tmp_path / "packs"
        root.mkdir(exist_ok=True)
        pack = make_pack(
            claims=(
                make_claim(
                    support=(make_support(calculation_policy_version=declared_policy_version),)
                ),
            )
        )
        directory_name, _ = write_pack_directory(
            root, pack=pack, directory_name=f"pack-{uuid.uuid4().hex}"
        )

        repository = SQLiteEvidenceReviewRepository.open(":memory:")
        repository.initialize_schema()
        try:
            runner = build_ccna_mastery_use_case(
                loader=ObjectivePackLoader(PackFileReader(roots=[root])),
                review_service=EvidenceReviewService(repository),
                code_revision="test-revision",
            )
            return runner.run(pack_directory=directory_name, run_id="r1")
        finally:
            repository.close()

    def test_a_policy_mismatch_fails_g1_go_06_with_observed_evidence(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from personal_lms.labs.ccna_mastery.gates import GateCheckStatus

        result = self._run(tmp_path, declared_policy_version="some-other-policy")

        checks = {check.check_id: check for check in result.report.checks}
        grounding_check = checks["G1-GO-06"]

        assert grounding_check.status is GateCheckStatus.FAILED
        assert grounding_check.reason_code == "calculation_policy_mismatch"
        assert grounding_check.observed_hash is not None
        assert grounding_check.detail is not None

    def test_the_observed_evidence_is_deterministic_across_runs(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        first = self._run(tmp_path, declared_policy_version="some-other-policy")
        second = self._run(tmp_path, declared_policy_version="some-other-policy")

        first_hash = {c.check_id: c.observed_hash for c in first.report.checks}["G1-GO-06"]
        second_hash = {c.check_id: c.observed_hash for c in second.report.checks}["G1-GO-06"]

        assert first_hash == second_hash

    def test_a_matching_policy_still_passes_with_observed_evidence(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from personal_lms.labs.ccna_mastery.gates import GateCheckStatus
        from personal_lms.objective_packs.scoring import CLAIM_SCORE_POLICY_VERSION

        result = self._run(tmp_path, declared_policy_version=CLAIM_SCORE_POLICY_VERSION)

        checks = {check.check_id: check for check in result.report.checks}
        grounding_check = checks["G1-GO-06"]

        assert grounding_check.status is GateCheckStatus.PASSED
        assert grounding_check.reason_code == "grounding_meets_threshold"
        assert grounding_check.observed_hash is not None
