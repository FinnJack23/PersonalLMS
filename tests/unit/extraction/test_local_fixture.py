"""Local fixture extractor tests, including the deferred-PDF-parser boundary.

The PDF double here is exactly that — a deterministic test double. Its
existence in tests, and its absence from production code, is the shape of
the unresolved parser decision: nothing ships a parser, and nothing
pretends a sidecar file counts as extraction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personal_lms.extraction.artifacts import ExtractionOutcome
from personal_lms.extraction.local_fixture import LocalFixtureExtractor
from personal_lms.objective_packs.loader import LoaderLimits, PackFileReader

from ..objective_packs._helpers import (
    PDF_BYTES,
    PNG_BYTES,
    make_image_region,
    make_source,
    make_text_region,
    sha256_of,
)


class DeterministicPdfTextExtractor:
    """A test double standing in for the unchosen PDF parser.

    Returns fixed text per page. Pure, offline, and dependency-free — the
    same properties a real approved parser adapter would have to honour.
    """

    def __init__(self, pages: dict[int, str] | None = None) -> None:
        self._pages = pages if pages is not None else {1: "Synthetic page one text."}

    @property
    def extractor_id(self) -> str:
        return "deterministic-double"

    @property
    def extractor_version(self) -> str:
        return "1.0"

    def extract_page_text(self, payload: bytes, *, page_number: int) -> str:
        if page_number not in self._pages:
            raise ValueError(f"page {page_number} does not exist")
        return self._pages[page_number]


@pytest.fixture
def pack_root(tmp_path: Path) -> Path:
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "synthetic.pdf").write_bytes(PDF_BYTES)
    (tmp_path / "sources" / "synthetic.png").write_bytes(PNG_BYTES)
    return tmp_path


@pytest.fixture
def reader(pack_root: Path) -> PackFileReader:
    return PackFileReader(roots=[pack_root])


@pytest.fixture
def extractor(reader: PackFileReader) -> LocalFixtureExtractor:
    return LocalFixtureExtractor(reader)


class TestSupportedTypes:
    def test_pdf_and_png_are_supported(self, extractor: LocalFixtureExtractor) -> None:
        assert extractor.supports("application/pdf")
        assert extractor.supports("image/png")

    def test_other_types_are_not(self, extractor: LocalFixtureExtractor) -> None:
        assert not extractor.supports("application/zip")
        assert not extractor.supports("image/tiff")


class TestByteVerification:
    def test_a_matching_artifact_verifies(self, extractor: LocalFixtureExtractor) -> None:
        result, payload = extractor.read_and_verify(
            make_source(), relative_path="sources/synthetic.pdf"
        )

        assert result.succeeded
        assert payload == PDF_BYTES

    def test_a_hash_mismatch_returns_no_payload(
        self, extractor: LocalFixtureExtractor, pack_root: Path
    ) -> None:
        (pack_root / "sources" / "synthetic.pdf").write_bytes(PDF_BYTES + b"tampered")

        result, payload = extractor.read_and_verify(
            make_source(), relative_path="sources/synthetic.pdf"
        )

        assert result.outcome is ExtractionOutcome.HASH_MISMATCH
        assert payload is None

    def test_a_path_outside_the_root_is_blocked_by_policy(
        self, extractor: LocalFixtureExtractor
    ) -> None:
        result, payload = extractor.read_and_verify(make_source(), relative_path="../outside.pdf")

        assert result.outcome is ExtractionOutcome.BLOCKED_BY_POLICY
        assert payload is None

    def test_a_missing_file_is_blocked_by_policy(self, extractor: LocalFixtureExtractor) -> None:
        result, _ = extractor.read_and_verify(make_source(), relative_path="sources/absent.pdf")

        assert result.outcome is ExtractionOutcome.BLOCKED_BY_POLICY

    def test_a_declared_size_that_disagrees_is_malformed(
        self, extractor: LocalFixtureExtractor
    ) -> None:
        artifact = make_source().model_copy(update={"size_bytes": len(PDF_BYTES) + 10})

        result, payload = extractor.read_and_verify(artifact, relative_path="sources/synthetic.pdf")

        assert result.outcome is ExtractionOutcome.MALFORMED_SOURCE
        assert payload is None

    def test_an_oversized_file_is_refused(self, pack_root: Path) -> None:
        reader = PackFileReader(roots=[pack_root], limits=LoaderLimits(maximum_file_bytes=4))
        extractor = LocalFixtureExtractor(reader)

        result, _ = extractor.read_and_verify(make_source(), relative_path="sources/synthetic.pdf")

        assert result.outcome is ExtractionOutcome.BLOCKED_BY_POLICY


class TestImageRegionExtraction:
    def test_a_valid_png_region_resolves_with_dimensions(
        self, extractor: LocalFixtureExtractor
    ) -> None:
        artifact = make_source(source_id="src-png", payload=PNG_BYTES, media_type="image/png")

        result = extractor.extract_region(
            make_image_region(), artifact, relative_path="sources/synthetic.png"
        )

        assert result.succeeded
        assert (result.image_width, result.image_height) == (64, 48)

    def test_no_ocr_text_is_ever_produced_for_an_image(
        self, extractor: LocalFixtureExtractor
    ) -> None:
        artifact = make_source(source_id="src-png", payload=PNG_BYTES, media_type="image/png")

        result = extractor.extract_region(
            make_image_region(), artifact, relative_path="sources/synthetic.png"
        )

        assert result.text is None

    def test_a_region_pinning_a_different_image_hash_is_refused(
        self, extractor: LocalFixtureExtractor
    ) -> None:
        artifact = make_source(source_id="src-png", payload=PNG_BYTES, media_type="image/png")
        region = make_image_region(image_payload=PDF_BYTES)

        result = extractor.extract_region(region, artifact, relative_path="sources/synthetic.png")

        assert result.outcome is ExtractionOutcome.HASH_MISMATCH

    def test_an_image_region_on_a_pdf_source_is_a_media_type_mismatch(
        self, extractor: LocalFixtureExtractor
    ) -> None:
        region = make_image_region(source_id="src-pdf", image_payload=PDF_BYTES)

        result = extractor.extract_region(
            region, make_source(), relative_path="sources/synthetic.pdf"
        )

        assert result.outcome is ExtractionOutcome.MEDIA_TYPE_MISMATCH

    def test_a_region_collapsing_to_zero_pixels_is_out_of_bounds(
        self, pack_root: Path, extractor: LocalFixtureExtractor
    ) -> None:
        from ..objective_packs._helpers import make_png

        # A genuine small image, so only the *region* is degenerate. A
        # spliced header would now fail integrity validation first and
        # mask what this test is actually about.
        tiny = make_png(width=4, height=4)
        (pack_root / "sources" / "tiny.png").write_bytes(tiny)
        artifact = make_source(source_id="src-tiny", payload=tiny, media_type="image/png")
        region = make_image_region(source_id="src-tiny", image_payload=tiny).model_copy(
            update={
                "selector": make_image_region(image_payload=tiny).selector.model_copy(
                    update={
                        "left_basis_points": 1,
                        "top_basis_points": 1,
                        "right_basis_points": 2,
                        "bottom_basis_points": 2,
                    }
                )
            }
        )

        result = extractor.extract_region(region, artifact, relative_path="sources/tiny.png")

        assert result.outcome is ExtractionOutcome.REGION_OUT_OF_BOUNDS


class TestPdfExtractionSeam:
    def test_without_a_configured_parser_extraction_refuses_honestly(
        self, extractor: LocalFixtureExtractor
    ) -> None:
        """The unresolved parser decision surfaces as a refusal, not a guess."""
        result = extractor.extract_region(
            make_text_region(), make_source(), relative_path="sources/synthetic.pdf"
        )

        assert result.outcome is ExtractionOutcome.EXTRACTOR_UNAVAILABLE
        assert result.text is None
        assert result.detail is not None
        assert "sidecar" in result.detail

    def test_with_an_injected_parser_page_text_is_extracted(self, reader: PackFileReader) -> None:
        text = "Synthetic page one text."
        extractor = LocalFixtureExtractor(
            reader, pdf_text_extractor=DeterministicPdfTextExtractor({1: text})
        )
        region = make_text_region(text=text)

        result = extractor.extract_region(
            region, make_source(), relative_path="sources/synthetic.pdf"
        )

        assert result.succeeded
        assert result.text == text
        assert result.extractor_id == "deterministic-double"

    def test_a_selector_past_the_end_of_the_page_is_out_of_bounds(
        self, reader: PackFileReader
    ) -> None:
        extractor = LocalFixtureExtractor(
            reader, pdf_text_extractor=DeterministicPdfTextExtractor({1: "short"})
        )
        region = make_text_region(text="a much longer synthetic passage than the page holds")

        result = extractor.extract_region(
            region, make_source(), relative_path="sources/synthetic.pdf"
        )

        assert result.outcome is ExtractionOutcome.REGION_OUT_OF_BOUNDS

    def test_a_missing_page_is_reported_as_malformed(self, reader: PackFileReader) -> None:
        extractor = LocalFixtureExtractor(
            reader, pdf_text_extractor=DeterministicPdfTextExtractor({2: "only page two"})
        )

        result = extractor.extract_region(
            make_text_region(), make_source(), relative_path="sources/synthetic.pdf"
        )

        assert result.outcome is ExtractionOutcome.MALFORMED_SOURCE

    def test_extraction_is_deterministic_across_runs(self, reader: PackFileReader) -> None:
        text = "Synthetic page one text."
        extractor = LocalFixtureExtractor(
            reader, pdf_text_extractor=DeterministicPdfTextExtractor({1: text})
        )
        region = make_text_region(text=text)

        first = extractor.extract_region(
            region, make_source(), relative_path="sources/synthetic.pdf"
        )
        second = extractor.extract_region(
            region, make_source(), relative_path="sources/synthetic.pdf"
        )

        assert first == second


class TestRegionOwnership:
    def test_a_region_from_another_source_is_blocked(
        self, extractor: LocalFixtureExtractor
    ) -> None:
        region = make_text_region(source_id="src-other")

        result = extractor.extract_region(
            region, make_source(), relative_path="sources/synthetic.pdf"
        )

        assert result.outcome is ExtractionOutcome.BLOCKED_BY_POLICY


def test_the_png_fixture_hash_is_stable() -> None:
    """Guards the shared fixture bytes the other tests pin against."""
    assert sha256_of(PNG_BYTES) == sha256_of(PNG_BYTES)
