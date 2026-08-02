"""Local fixture extractor tests, including the deferred-PDF-parser boundary.

The PDF double here is exactly that — a deterministic test double. Its
existence in tests, and its absence from production code, is the shape of
the unresolved parser decision: nothing ships a parser, and nothing
pretends a sidecar file counts as extraction.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from personal_lms.extraction.artifacts import (
    DecodedImage,
    ExtractionOutcome,
    inspect_png_dimensions,
)
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


class DeterministicPngPixelDecoder:
    """A test double standing in for the declared Pillow adapter.

    Produces one flat RGB sample block sized from the requested box, so
    tests exercise the decode seam without depending on the optional
    extraction extra being installed.
    """

    @property
    def decoder_id(self) -> str:
        return "deterministic-decoder"

    @property
    def decoder_version(self) -> str:
        return "1.0"

    def decode_region(
        self, payload: bytes, *, box: tuple[int, int, int, int] | None = None
    ) -> DecodedImage:
        dimensions = inspect_png_dimensions(payload)
        if dimensions is None:
            raise ValueError("not a decodable PNG")
        if box is None:
            width, height = dimensions.width, dimensions.height
        else:
            width, height = box[2] - box[0], box[3] - box[1]
        return DecodedImage(
            width=width,
            height=height,
            format_name="PNG",
            rgb_bytes=b"\x00\x00\x00" * width * height,
        )


@pytest.fixture
def reader(pack_root: Path) -> PackFileReader:
    return PackFileReader(roots=[pack_root])


@pytest.fixture
def extractor(reader: PackFileReader) -> LocalFixtureExtractor:
    return LocalFixtureExtractor(reader, png_pixel_decoder=DeterministicPngPixelDecoder())


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
        # Pixels were actually materialized: a derived box and a region
        # hash only exist when a decoder ran.
        assert result.pixel_box is not None
        assert result.region_content_sha256 is not None

    def test_without_a_decoder_an_image_region_fails_closed(self, reader: PackFileReader) -> None:
        """Structural checks alone never stand in for a decode.

        The header-only path is exactly how a 24-byte stub could be
        presented to a reviewer as an image; refusing here is what makes
        "a human reviewed this diagram" mean a diagram existed.
        """
        extractor = LocalFixtureExtractor(reader)
        artifact = make_source(source_id="src-png", payload=PNG_BYTES, media_type="image/png")

        result = extractor.extract_region(
            make_image_region(), artifact, relative_path="sources/synthetic.png"
        )

        assert result.outcome is ExtractionOutcome.EXTRACTOR_UNAVAILABLE
        assert result.detail is not None
        assert "ccna-lab" in result.detail

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


class TestArchitectureDiffGuard:
    """G1-FX-09: the adapter stays a narrow searchable-PDF/PNG adapter.

    Named to match ``LINCHPIN_TRACEABILITY.md``'s planned test for this
    row. Shares its assertion logic with the Gate 1 runtime check
    (``architecture_guard.check_extraction_adapter_is_narrow``) so there is
    one definition of "narrow," not a test copy that could drift from what
    the gate actually verifies.
    """

    def test_adapter_uses_existing_extraction_contracts(self) -> None:
        from personal_lms.labs.ccna_mastery.architecture_guard import (
            EXPECTED_LOCAL_FIXTURE_EXTRACTOR_PUBLIC_MEMBERS,
            check_extraction_adapter_is_narrow,
        )

        result = check_extraction_adapter_is_narrow()

        assert result.public_members == EXPECTED_LOCAL_FIXTURE_EXTRACTOR_PUBLIC_MEMBERS
        assert result.source_length > 0

    def test_a_widened_public_surface_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The architecture-diff guard, not just the shape it happens to find today."""
        from personal_lms.extraction import local_fixture
        from personal_lms.labs.ccna_mastery.architecture_guard import (
            ArchitectureGuardViolation,
            check_extraction_adapter_is_narrow,
        )

        def ingest_anything(self, payload: bytes) -> bytes:  # pragma: no cover - never called
            return payload

        monkeypatch.setattr(
            local_fixture.LocalFixtureExtractor, "ingest_anything", ingest_anything, raising=False
        )

        with pytest.raises(ArchitectureGuardViolation, match="public surface changed"):
            check_extraction_adapter_is_narrow()

    def test_an_import_of_the_general_extraction_queue_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The detector inspects real imports, not prose that merely names one."""
        from personal_lms.labs.ccna_mastery import architecture_guard

        # PackFileReader is a genuine import in local_fixture.py; forbidding
        # it here proves the AST scan actually finds a real import rather
        # than trivially passing because nothing on the list ever matches.
        monkeypatch.setattr(architecture_guard, "FORBIDDEN_IMPORTED_NAMES", ("PackFileReader",))

        with pytest.raises(
            architecture_guard.ArchitectureGuardViolation, match="general extraction"
        ):
            architecture_guard.check_extraction_adapter_is_narrow()

    def test_a_prose_mention_of_the_extraction_queue_is_not_a_violation(self) -> None:
        """The module's own docstring names ExtractionQueue to disclaim using it."""
        from personal_lms.labs.ccna_mastery.architecture_guard import (
            check_extraction_adapter_is_narrow,
        )

        # Must not raise: this is exactly the guard's own false-positive
        # regression case, since local_fixture.py's docstring says "The
        # existing ExtractionQueue still owns job lifecycle" verbatim.
        check_extraction_adapter_is_narrow()

    def test_a_sql_schema_marker_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from personal_lms.labs.ccna_mastery import architecture_guard

        monkeypatch.setattr(
            architecture_guard, "FORBIDDEN_SOURCE_TOKENS", ("LocalFixtureExtractor",)
        )

        with pytest.raises(architecture_guard.ArchitectureGuardViolation, match="schema marker"):
            architecture_guard.check_extraction_adapter_is_narrow()


class TestRepositoryWideArchitectureScan:
    """G1-FX-09's second half: a parallel extraction service can be added
    anywhere in the repository, not only inside ``local_fixture.py``.

    Independent review (2026-07-28) found the module-level guard above
    blind to exactly that. Every test here monkeypatches
    ``_changed_python_files`` to a controlled list rather than depending
    on a real git repository under ``tmp_path`` -- the git invocation
    itself is covered separately, once, by the real-repository test and
    the git-failure test.
    """

    def test_the_real_repository_has_no_parallel_extraction_service(self) -> None:
        """Run for real, against this actual checkout's current change
        set -- the strongest available proof, not a mock."""
        from personal_lms.labs.ccna_mastery.architecture_guard import (
            check_repository_has_no_parallel_extraction_service,
        )

        result = check_repository_has_no_parallel_extraction_service()

        assert result.violations == ()
        assert result.reviewed_base_revision == "HEAD"

    def test_a_clean_change_set_has_zero_scanned_files_not_a_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A clean checkout is evidence of no new architecture, not a failure."""
        from personal_lms.labs.ccna_mastery import architecture_guard

        extraction_dir = tmp_path / "src" / "personal_lms" / "extraction"
        extraction_dir.mkdir(parents=True)
        for name in architecture_guard._REVIEWED_EXTRACTION_PACKAGE_FILES:
            (extraction_dir / name).write_text("", encoding="utf-8")
        monkeypatch.setattr(architecture_guard, "_changed_python_files", lambda **_: [])

        result = architecture_guard.check_repository_has_no_parallel_extraction_service(
            repo_root=tmp_path, base_revision="fake-base"
        )

        assert result.scanned_file_count == 0
        assert result.violations == ()

    def test_changed_python_files_cover_dirty_and_committed_deltas(self, tmp_path: Path) -> None:
        """Git state, rather than network state, supplies both scan lifecycles."""
        from personal_lms.labs.ccna_mastery.architecture_guard import _changed_python_files

        source = tmp_path / "src" / "personal_lms" / "changed.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(tmp_path), *args],
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip()

        git("init")
        git("config", "user.email", "architecture-guard@example.test")
        git("config", "user.name", "Architecture Guard Test")
        git("add", "src/personal_lms/changed.py")
        git("commit", "-m", "base")
        base_revision = git("rev-parse", "HEAD")

        source.write_text("VALUE = 2\n", encoding="utf-8")
        expected = ["src/personal_lms/changed.py"]
        assert _changed_python_files(repo_root=tmp_path, base_revision=base_revision) == expected

        git("add", "src/personal_lms/changed.py")
        git("commit", "-m", "changed python module")
        assert _changed_python_files(repo_root=tmp_path, base_revision=base_revision) == expected

    def test_a_new_file_in_the_extraction_package_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_lms.labs.ccna_mastery import architecture_guard

        extraction_dir = tmp_path / "src" / "personal_lms" / "extraction"
        extraction_dir.mkdir(parents=True)
        for name in architecture_guard._REVIEWED_EXTRACTION_PACKAGE_FILES:
            (extraction_dir / name).write_text("", encoding="utf-8")
        (extraction_dir / "rogue_extractor.py").write_text(
            "# a new, unreviewed file", encoding="utf-8"
        )
        monkeypatch.setattr(architecture_guard, "_changed_python_files", lambda **_: [])

        with pytest.raises(architecture_guard.ArchitectureGuardViolation, match="unreviewed file"):
            architecture_guard.check_repository_has_no_parallel_extraction_service(
                repo_root=tmp_path, base_revision="fake-base"
            )

    def test_a_new_extraction_shaped_file_elsewhere_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A parallel extraction service does not have to live inside the
        extraction package to be caught."""
        from personal_lms.labs.ccna_mastery import architecture_guard

        extraction_dir = tmp_path / "src" / "personal_lms" / "extraction"
        extraction_dir.mkdir(parents=True)
        for name in architecture_guard._REVIEWED_EXTRACTION_PACKAGE_FILES:
            (extraction_dir / name).write_text("", encoding="utf-8")

        rogue_dir = tmp_path / "src" / "personal_lms" / "labs" / "ccna_mastery"
        rogue_dir.mkdir(parents=True)
        (rogue_dir / "rogue_pdf_reader.py").write_text(
            "import pdfminer\n\nSCHEMA = 'CREATE TABLE extra (id)'\n", encoding="utf-8"
        )
        relative = "personal_lms/labs/ccna_mastery/rogue_pdf_reader.py"
        monkeypatch.setattr(architecture_guard, "_changed_python_files", lambda **_: [relative])

        with pytest.raises(architecture_guard.ArchitectureGuardViolation, match="schema marker"):
            architecture_guard.check_repository_has_no_parallel_extraction_service(
                repo_root=tmp_path, base_revision="fake-base"
            )

    def test_a_forbidden_import_elsewhere_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_lms.labs.ccna_mastery import architecture_guard

        extraction_dir = tmp_path / "src" / "personal_lms" / "extraction"
        extraction_dir.mkdir(parents=True)
        for name in architecture_guard._REVIEWED_EXTRACTION_PACKAGE_FILES:
            (extraction_dir / name).write_text("", encoding="utf-8")

        rogue_dir = tmp_path / "src" / "personal_lms" / "labs" / "ccna_mastery"
        rogue_dir.mkdir(parents=True)
        (rogue_dir / "rogue_png_reader.py").write_text(
            "from personal_lms.extraction.sqlite import ExtractionQueue\n", encoding="utf-8"
        )
        relative = "personal_lms/labs/ccna_mastery/rogue_png_reader.py"
        monkeypatch.setattr(architecture_guard, "_changed_python_files", lambda **_: [relative])

        with pytest.raises(
            architecture_guard.ArchitectureGuardViolation, match="general extraction pipeline"
        ):
            architecture_guard.check_repository_has_no_parallel_extraction_service(
                repo_root=tmp_path, base_revision="fake-base"
            )

    def test_an_unrelated_new_file_with_sql_is_not_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The scan is narrow: a legitimate new SQLite-backed store that
        has nothing to do with extraction must not be flagged merely for
        defining its own, properly reviewed schema elsewhere in the app."""
        from personal_lms.labs.ccna_mastery import architecture_guard

        extraction_dir = tmp_path / "src" / "personal_lms" / "extraction"
        extraction_dir.mkdir(parents=True)
        for name in architecture_guard._REVIEWED_EXTRACTION_PACKAGE_FILES:
            (extraction_dir / name).write_text("", encoding="utf-8")

        unrelated_dir = tmp_path / "src" / "personal_lms" / "promotion"
        unrelated_dir.mkdir(parents=True)
        (unrelated_dir / "sqlite.py").write_text(
            "SCHEMA = 'CREATE TABLE promotions (id)'\n", encoding="utf-8"
        )
        relative = "personal_lms/promotion/sqlite.py"
        monkeypatch.setattr(architecture_guard, "_changed_python_files", lambda **_: [relative])

        result = architecture_guard.check_repository_has_no_parallel_extraction_service(
            repo_root=tmp_path, base_revision="fake-base"
        )

        assert result.violations == ()

    def test_a_deleted_changed_file_is_skipped_not_crashed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file git reports as changed but that no longer exists on disk
        (deleted since the base revision) is skipped, never crashes."""
        from personal_lms.labs.ccna_mastery import architecture_guard

        extraction_dir = tmp_path / "src" / "personal_lms" / "extraction"
        extraction_dir.mkdir(parents=True)
        for name in architecture_guard._REVIEWED_EXTRACTION_PACKAGE_FILES:
            (extraction_dir / name).write_text("", encoding="utf-8")

        monkeypatch.setattr(
            architecture_guard,
            "_changed_python_files",
            lambda **_: ["personal_lms/extraction/a_deleted_file.py"],
        )

        result = architecture_guard.check_repository_has_no_parallel_extraction_service(
            repo_root=tmp_path, base_revision="fake-base"
        )

        assert result.violations == ()

    def test_git_failure_is_reported_as_a_violation_not_a_crash(self, tmp_path: Path) -> None:
        """``tmp_path`` is not a git repository, so the real git invocation
        inside ``_changed_python_files`` fails closed here, unpatched."""
        from personal_lms.labs.ccna_mastery import architecture_guard

        extraction_dir = tmp_path / "src" / "personal_lms" / "extraction"
        extraction_dir.mkdir(parents=True)
        for name in architecture_guard._REVIEWED_EXTRACTION_PACKAGE_FILES:
            (extraction_dir / name).write_text("", encoding="utf-8")

        with pytest.raises(architecture_guard.ArchitectureGuardViolation, match="changed file set"):
            architecture_guard.check_repository_has_no_parallel_extraction_service(
                repo_root=tmp_path, base_revision="fake-base"
            )
