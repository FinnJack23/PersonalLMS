"""Deterministic extraction-check tests: bytes, media type, size, PNG headers.

These checks are the ones that stand between a manifest's claims and what
is actually on disk, so the negative cases carry the weight: a renamed
file, a mutated byte, an oversized payload, and a truncated header each
get an explicit refusal.
"""

from __future__ import annotations

import pytest

from personal_lms.domain.objective_packs import ImageRegionSelector
from personal_lms.extraction.artifacts import (
    ExtractionLimits,
    ExtractionOutcome,
    ImageDimensions,
    detect_media_type,
    inspect_png_dimensions,
    region_fits_image,
    verify_source_bytes,
)

from ..objective_packs._helpers import PDF_BYTES, PNG_BYTES, sha256_of


class TestMediaTypeSniffing:
    def test_png_bytes_are_recognized(self) -> None:
        assert detect_media_type(PNG_BYTES) == "image/png"

    def test_pdf_bytes_are_recognized(self) -> None:
        assert detect_media_type(PDF_BYTES) == "application/pdf"

    @pytest.mark.parametrize("payload", [b"", b"not a known format", b"\x00\x01\x02"])
    def test_unknown_bytes_return_none(self, payload: bytes) -> None:
        assert detect_media_type(payload) is None


class TestVerifySourceBytes:
    def test_matching_bytes_pass_every_check(self) -> None:
        outcome = verify_source_bytes(
            PNG_BYTES,
            expected_sha256=sha256_of(PNG_BYTES),
            expected_media_type="image/png",
        )

        assert outcome is ExtractionOutcome.EXTRACTED

    def test_a_mutated_byte_fails_the_hash(self) -> None:
        mutated = PNG_BYTES[:-1] + b"\xff"

        outcome = verify_source_bytes(
            mutated,
            expected_sha256=sha256_of(PNG_BYTES),
            expected_media_type="image/png",
        )

        assert outcome is ExtractionOutcome.HASH_MISMATCH

    def test_a_pdf_declared_as_a_png_is_caught_before_parsing(self) -> None:
        outcome = verify_source_bytes(
            PDF_BYTES,
            expected_sha256=sha256_of(PDF_BYTES),
            expected_media_type="image/png",
        )

        assert outcome is ExtractionOutcome.MEDIA_TYPE_MISMATCH

    def test_an_unsupported_media_type_is_refused(self) -> None:
        outcome = verify_source_bytes(
            PDF_BYTES,
            expected_sha256=sha256_of(PDF_BYTES),
            expected_media_type="application/zip",
        )

        assert outcome is ExtractionOutcome.UNSUPPORTED_MEDIA_TYPE

    def test_an_oversized_payload_is_refused_before_anything_else(self) -> None:
        outcome = verify_source_bytes(
            PNG_BYTES,
            expected_sha256="0" * 64,
            expected_media_type="application/zip",
            limits=ExtractionLimits(maximum_source_bytes=4),
        )

        assert outcome is ExtractionOutcome.SIZE_LIMIT_EXCEEDED

    def test_unrecognizable_bytes_are_a_media_type_mismatch(self) -> None:
        payload = b"neither a pdf nor a png"

        outcome = verify_source_bytes(
            payload,
            expected_sha256=sha256_of(payload),
            expected_media_type="application/pdf",
        )

        assert outcome is ExtractionOutcome.MEDIA_TYPE_MISMATCH


class TestPngDimensionInspection:
    def test_dimensions_are_read_from_the_ihdr_header(self) -> None:
        assert inspect_png_dimensions(PNG_BYTES) == ImageDimensions(width=64, height=48)

    def test_a_truncated_header_is_unreadable(self) -> None:
        assert inspect_png_dimensions(PNG_BYTES[:16]) is None

    def test_non_png_bytes_are_unreadable(self) -> None:
        assert inspect_png_dimensions(PDF_BYTES + b"\x00" * 32) is None

    def test_a_missing_ihdr_chunk_is_unreadable(self) -> None:
        corrupted = PNG_BYTES[:12] + b"IDAT" + PNG_BYTES[16:]

        assert inspect_png_dimensions(corrupted) is None

    def test_a_zero_dimension_is_rejected(self) -> None:
        zero_width = PNG_BYTES[:16] + (0).to_bytes(4, "big") + PNG_BYTES[20:]

        assert inspect_png_dimensions(zero_width) is None

    def test_an_implausible_canvas_is_rejected_without_allocating(self) -> None:
        """A decompression-bomb shape is refused from the header alone."""
        huge = (
            PNG_BYTES[:16]
            + (100_000).to_bytes(4, "big")
            + (100_000).to_bytes(4, "big")
            + PNG_BYTES[24:]
        )

        assert inspect_png_dimensions(huge) is None

    def test_a_raised_pixel_ceiling_admits_a_large_canvas(self) -> None:
        """The ceiling is only one of several checks a real image must pass.

        Splicing a larger canvas into the header no longer suffices: the
        validator now also verifies CRCs and that the inflated data
        actually covers the declared rows, so this must be a genuine
        image.
        """
        from ..objective_packs._helpers import make_png

        large = make_png(width=600, height=400)

        dimensions = inspect_png_dimensions(
            large, limits=ExtractionLimits(maximum_image_pixels=200_000_000)
        )

        assert dimensions == ImageDimensions(width=600, height=400)

    def test_a_header_only_stub_with_a_large_canvas_is_still_refused(self) -> None:
        spliced = (
            PNG_BYTES[:16]
            + (10_000).to_bytes(4, "big")
            + (10_000).to_bytes(4, "big")
            + PNG_BYTES[24:]
        )

        assert (
            inspect_png_dimensions(
                spliced, limits=ExtractionLimits(maximum_image_pixels=200_000_000)
            )
            is None
        )


class TestRegionBounds:
    def test_a_normal_region_fits(self) -> None:
        selector = ImageRegionSelector(
            image_sha256=sha256_of(PNG_BYTES),
            left_basis_points=1_000,
            top_basis_points=1_000,
            right_basis_points=9_000,
            bottom_basis_points=9_000,
        )

        assert region_fits_image(selector, ImageDimensions(width=64, height=48)) is True

    def test_a_region_thinner_than_one_pixel_does_not_fit(self) -> None:
        selector = ImageRegionSelector(
            image_sha256=sha256_of(PNG_BYTES),
            left_basis_points=1,
            top_basis_points=1,
            right_basis_points=2,
            bottom_basis_points=2,
        )

        assert region_fits_image(selector, ImageDimensions(width=10, height=10)) is False

    def test_an_inverted_box_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="left_basis_points"):
            ImageRegionSelector(
                image_sha256=sha256_of(PNG_BYTES),
                left_basis_points=9_000,
                top_basis_points=1_000,
                right_basis_points=1_000,
                bottom_basis_points=9_000,
            )

    def test_an_out_of_range_coordinate_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError):
            ImageRegionSelector(
                image_sha256=sha256_of(PNG_BYTES),
                left_basis_points=0,
                top_basis_points=0,
                right_basis_points=10_001,
                bottom_basis_points=9_000,
            )
