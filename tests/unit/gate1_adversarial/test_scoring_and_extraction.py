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
