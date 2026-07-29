"""Local fixture extractor: a narrow, deterministic PDF/PNG source adapter.

Reads frozen source bytes through a configured-root file reader, runs
every deterministic check in ``extraction.artifacts``, and produces a
stable result. It is intentionally narrow: it introduces no ingestion
schema, no queue, no parallel extraction service, and no new persistence.
The existing ``ExtractionQueue`` still owns job lifecycle; this adapter
only answers "what do these particular bytes contain?".

What it does **not** do:

- **No OCR.** A PNG's readable content is a human-authored
  ``accessible_description`` approved through ``evidence_review``, never
  pixels interpreted by this module.
- **No PDF parser choice.** Text extraction is delegated to an injected
  ``PdfTextExtractor``. With none supplied, PDF extraction reports
  ``EXTRACTOR_UNAVAILABLE`` — an honest refusal rather than a sidecar
  substitution. A trusted sidecar text file is explicitly *not* accepted
  as extraction: the bytes must be read from the source itself.
- **No eligibility decision.** Successful extraction never implies the
  content may be taught, indexed, or sent to a model.

Every path it touches goes through ``PackFileReader``, so a source
outside the configured roots is unreadable by construction rather than by
convention.
"""

from __future__ import annotations

from io import BytesIO

from personal_lms.domain.objective_packs import (
    EvidenceRegion,
    ImageRegionSelector,
    PageTextSelector,
    SourceArtifactRef,
)
from personal_lms.extraction.artifacts import (
    DEFAULT_EXTRACTION_LIMITS,
    SUPPORTED_MEDIA_TYPES,
    DecodedImage,
    ExtractionLimits,
    ExtractionOutcome,
    PdfTextExtractor,
    PngPixelDecoder,
    SourceArtifactExtractionResult,
    image_region_content_sha256,
    inspect_png_dimensions,
    locate_passage,
    normalized_bbox_to_pixel_box,
    verify_source_bytes,
)
from personal_lms.objective_packs.errors import ObjectivePackError
from personal_lms.objective_packs.loader import PackFileReader

__all__ = [
    "LOCAL_FIXTURE_EXTRACTOR_ID",
    "OPTIONAL_EXTRA_HINT",
    "LocalFixtureExtractor",
    "PdfMinerTextExtractor",
    "PillowPngDecoder",
]

LOCAL_FIXTURE_EXTRACTOR_ID = "local-fixture"
_EXTRACTOR_VERSION = "1.0"

#: Installation guidance carried by every typed unavailable-parser result,
#: so a fail-closed run tells an operator what to do instead of only that
#: something is missing.
OPTIONAL_EXTRA_HINT = "install the optional extraction dependencies: uv sync --extra ccna-lab"


class PdfMinerTextExtractor:
    """Searchable-PDF text through the declared ``pdfminer.six`` dependency.

    Declared, not ambient: the import is resolved once at construction and
    a missing package raises immediately with installation guidance,
    rather than surfacing later as a mysterious extraction failure. There
    is no sidecar fallback and no second parser — this adapter either
    reads the PDF's own text layer or refuses.
    """

    def __init__(self, *, maximum_pages: int = 512) -> None:
        try:
            from pdfminer import __version__ as pdfminer_version
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised by the extra-less env
            raise ModuleNotFoundError(
                f"pdfminer.six is unavailable; {OPTIONAL_EXTRA_HINT}"
            ) from exc
        if maximum_pages < 1:
            raise ValueError("maximum_pages must be at least one")
        self._version = str(pdfminer_version)
        self._maximum_pages = maximum_pages

    @property
    def extractor_id(self) -> str:
        return "pdfminer-six"

    @property
    def extractor_version(self) -> str:
        return self._version

    def extract_page_text(self, payload: bytes, *, page_number: int) -> str:
        """Text of one 1-indexed page, or ``ValueError``.

        The page number is bounded twice: below by the one-based contract,
        above by ``maximum_pages``, so a selector naming page 10**9 is
        refused before the parser is asked to walk that far.

        ``pdfminer`` raises a wide range of its own exception types for
        malformed input; they are all funnelled into ``ValueError`` because
        the caller's only correct response is the same either way, and a
        parser exception escaping this seam would bypass the typed
        extraction outcome the gate reports.
        """
        if page_number < 1:
            raise ValueError("page_number must be one-based")
        if page_number > self._maximum_pages:
            raise ValueError(
                f"page {page_number} exceeds the configured {self._maximum_pages}-page ceiling"
            )

        from pdfminer.high_level import extract_text

        try:
            # Annotated because in core-only mode pdfminer resolves to Any, and
            # strict mode rejects returning that from a str-declared function.
            text: str = extract_text(BytesIO(payload), page_numbers=[page_number - 1])
        except Exception as exc:  # noqa: BLE001 - narrowed to a typed ValueError below
            raise ValueError(
                f"pdfminer.six could not read the PDF bytes: {type(exc).__name__}"
            ) from exc
        # A page-break form feed is a page delimiter, not content.
        text = text.replace("\x0c", "")
        if not text.strip():
            raise ValueError(f"PDF page {page_number} carries no searchable text")
        return text


class PillowPngDecoder:
    """Full-pixel PNG decode through the declared ``Pillow`` dependency.

    This is what makes "a human reviewed this image" mean something. The
    structural checks in ``extraction.artifacts`` verify chunk CRCs and
    that the compressed stream inflates to a full canvas; they still
    cannot prove the file decodes. Materializing RGB samples can, and a
    header-only pseudo-image raises here instead of being handed onward.

    Pillow's own decompression-bomb ceiling is set from the caller's
    ``maximum_pixels`` rather than left at the library default, so the
    limit a gate reports is the limit that was actually enforced.
    """

    def __init__(self, *, maximum_pixels: int = DEFAULT_EXTRACTION_LIMITS.maximum_image_pixels):
        try:
            from PIL import __version__ as pillow_version
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised by the extra-less env
            raise ModuleNotFoundError(f"Pillow is unavailable; {OPTIONAL_EXTRA_HINT}") from exc
        if maximum_pixels < 1:
            raise ValueError("maximum_pixels must be at least one")
        self._version = str(pillow_version)
        self._maximum_pixels = maximum_pixels

    @property
    def decoder_id(self) -> str:
        return "pillow"

    @property
    def decoder_version(self) -> str:
        return self._version

    def decode_region(
        self, payload: bytes, *, box: tuple[int, int, int, int] | None = None
    ) -> DecodedImage:
        """Decode ``payload`` (optionally cropped) into RGB samples."""
        from PIL import Image, UnidentifiedImageError

        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = self._maximum_pixels
        try:
            with Image.open(BytesIO(payload)) as image:
                if image.format != "PNG":
                    raise ValueError(f"expected a PNG, decoded {image.format!r}")
                width, height = image.size
                if width * height > self._maximum_pixels:
                    raise ValueError(
                        f"{width}x{height} exceeds the {self._maximum_pixels}-pixel ceiling"
                    )
                # load() forces the full decode; without it Pillow is lazy
                # and a truncated stream would go unnoticed until later.
                image.load()
                region = image if box is None else image.crop(box)
                rgb = region.convert("RGB")
                crop_width, crop_height = rgb.size
                samples = rgb.tobytes()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError(
                f"Pillow could not fully decode the PNG: {type(exc).__name__}"
            ) from exc
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit

        if not samples:
            raise ValueError("the decoded region carries no pixels")
        return DecodedImage(
            width=crop_width, height=crop_height, format_name="PNG", rgb_bytes=samples
        )


class LocalFixtureExtractor:
    """Reads and verifies one frozen source artifact from a configured root.

    Structurally conforms to ``extraction.artifacts.SourceArtifactExtractor``.

    ``pdf_text_extractor`` is optional and defaults to ``None``: with no
    approved parser, PDF text extraction refuses rather than guessing.
    Supplying a deterministic double is how tests exercise the PDF path
    without committing the project to a dependency.
    """

    def __init__(
        self,
        reader: PackFileReader,
        *,
        pdf_text_extractor: PdfTextExtractor | None = None,
        png_pixel_decoder: PngPixelDecoder | None = None,
        limits: ExtractionLimits = DEFAULT_EXTRACTION_LIMITS,
    ) -> None:
        self._reader = reader
        self._pdf_text_extractor = pdf_text_extractor
        self._png_pixel_decoder = png_pixel_decoder
        self._limits = limits

    @property
    def extractor_id(self) -> str:
        return LOCAL_FIXTURE_EXTRACTOR_ID

    @property
    def extractor_version(self) -> str:
        return _EXTRACTOR_VERSION

    @property
    def limits(self) -> ExtractionLimits:
        return self._limits

    def supports(self, media_type: str) -> bool:
        return media_type in SUPPORTED_MEDIA_TYPES

    def read_and_verify(
        self, artifact: SourceArtifactRef, *, relative_path: str
    ) -> tuple[SourceArtifactExtractionResult, bytes | None]:
        """Admit, read, and byte-verify one artifact.

        Returns the result *and* the verified bytes, so a caller that
        needs both does not read the file twice. Bytes are ``None`` for
        every non-success outcome — there is no partial payload to act on.
        """
        try:
            payload = self._reader.read_bytes(relative_path)
        except ObjectivePackError as exc:
            return (
                self._failure(
                    artifact,
                    ExtractionOutcome.BLOCKED_BY_POLICY,
                    detail=f"source bytes were not admissible: {exc.reason_code}",
                ),
                None,
            )

        outcome = verify_source_bytes(
            payload,
            expected_sha256=artifact.sha256,
            expected_media_type=artifact.media_type,
            limits=self._limits,
        )
        if outcome is not ExtractionOutcome.EXTRACTED:
            return self._failure(artifact, outcome, observed_size_bytes=len(payload)), None

        if len(payload) != artifact.size_bytes:
            return (
                self._failure(
                    artifact,
                    ExtractionOutcome.MALFORMED_SOURCE,
                    observed_size_bytes=len(payload),
                    detail=(
                        f"artifact declares {artifact.size_bytes} bytes but the file "
                        f"holds {len(payload)}"
                    ),
                ),
                None,
            )

        return (
            SourceArtifactExtractionResult(
                outcome=ExtractionOutcome.EXTRACTED,
                source_id=artifact.source_id,
                extractor_id=self.extractor_id,
                extractor_version=self.extractor_version,
                observed_sha256=artifact.sha256,
                observed_media_type=artifact.media_type,
                observed_size_bytes=len(payload),
            ),
            payload,
        )

    def extract_region(
        self,
        region: EvidenceRegion,
        artifact: SourceArtifactRef,
        *,
        relative_path: str,
    ) -> SourceArtifactExtractionResult:
        """Extract one evidence region's content from its own source bytes.

        Byte verification always runs first: a region is never resolved
        against bytes that do not match the artifact it claims to come
        from.
        """
        if region.source_id != artifact.source_id:
            return self._failure(
                artifact,
                ExtractionOutcome.BLOCKED_BY_POLICY,
                detail="the region does not belong to the supplied source artifact",
            )

        verification, payload = self.read_and_verify(artifact, relative_path=relative_path)
        if payload is None:
            return verification

        if isinstance(region.selector, ImageRegionSelector):
            return self._extract_image_region(region.selector, artifact, payload)
        return self._extract_page_text(region, region.selector, artifact, payload)

    def _extract_image_region(
        self,
        selector: ImageRegionSelector,
        artifact: SourceArtifactRef,
        payload: bytes,
    ) -> SourceArtifactExtractionResult:
        """Decode a PNG region's pixels. Produces no text — this gate has no OCR.

        Order is deliberate: the cheap structural checks bound the work
        the expensive one does. Headers and chunk integrity are verified
        first (so a declared-enormous canvas is refused before anything is
        allocated), and only then are pixels materialized through the
        declared decoder. The region content hash covers those decoded
        samples, so passing this path is proof the image really renders.
        """
        if artifact.media_type != "image/png":
            return self._failure(
                artifact,
                ExtractionOutcome.MEDIA_TYPE_MISMATCH,
                detail="an image region requires a PNG source",
            )

        if selector.image_sha256 != artifact.sha256:
            return self._failure(
                artifact,
                ExtractionOutcome.HASH_MISMATCH,
                detail=(
                    "the region pins a different image hash than the source artifact; "
                    "a region can never be reinterpreted against different bytes"
                ),
            )

        dimensions = inspect_png_dimensions(payload, limits=self._limits)
        if dimensions is None:
            return self._failure(
                artifact,
                ExtractionOutcome.MALFORMED_SOURCE,
                observed_size_bytes=len(payload),
                detail="PNG header is unreadable or declares an implausible canvas",
            )

        box = normalized_bbox_to_pixel_box(selector, dimensions)
        left, top, right, bottom = box
        if right <= left or bottom <= top:
            return self._failure(
                artifact,
                ExtractionOutcome.REGION_OUT_OF_BOUNDS,
                observed_size_bytes=len(payload),
                detail=(
                    f"region collapses to zero pixels on a "
                    f"{dimensions.width}x{dimensions.height} image"
                ),
            )

        if self._png_pixel_decoder is None:
            return self._failure(
                artifact,
                ExtractionOutcome.EXTRACTOR_UNAVAILABLE,
                observed_size_bytes=len(payload),
                detail=(
                    "no PNG pixel decoder is configured; structural checks alone cannot "
                    f"prove an image decodes, and this gate does not accept a header-only "
                    f"stub as a reviewable image. {OPTIONAL_EXTRA_HINT}"
                ),
            )

        try:
            decoded_full = self._png_pixel_decoder.decode_region(payload)
            decoded_region = self._png_pixel_decoder.decode_region(payload, box=box)
        except ValueError as exc:
            return self._failure(
                artifact,
                ExtractionOutcome.MALFORMED_SOURCE,
                observed_size_bytes=len(payload),
                detail=f"PNG pixel decode failed: {exc}",
            )

        # The header said one thing; the decoder is what actually read the
        # image. A disagreement means one of them is being lied to.
        if (decoded_full.width, decoded_full.height) != (dimensions.width, dimensions.height):
            return self._failure(
                artifact,
                ExtractionOutcome.MALFORMED_SOURCE,
                observed_size_bytes=len(payload),
                detail=(
                    f"PNG header declares {dimensions.width}x{dimensions.height} but the "
                    f"decoder produced {decoded_full.width}x{decoded_full.height}"
                ),
            )

        return SourceArtifactExtractionResult(
            outcome=ExtractionOutcome.EXTRACTED,
            source_id=artifact.source_id,
            extractor_id=self._png_pixel_decoder.decoder_id,
            extractor_version=self._png_pixel_decoder.decoder_version,
            observed_sha256=artifact.sha256,
            observed_media_type=artifact.media_type,
            observed_size_bytes=len(payload),
            image_width=dimensions.width,
            image_height=dimensions.height,
            text=None,
            pixel_box=box,
            region_content_sha256=image_region_content_sha256(
                width=decoded_region.width,
                height=decoded_region.height,
                rgb_bytes=decoded_region.rgb_bytes,
            ),
        )

    def _extract_page_text(
        self,
        region: EvidenceRegion,
        selector: PageTextSelector,
        artifact: SourceArtifactRef,
        payload: bytes,
    ) -> SourceArtifactExtractionResult:
        """Extract a page-text region through the injected PDF seam.

        Two selector modes resolve to the same guarantee: whatever comes
        back is a slice of the text this adapter extracted from these
        exact bytes. A character range is sliced directly; a page-scoped
        selector is *located* — the region's reviewed passage is used only
        to find where on the page it occurs, and the characters returned
        are the extractor's, not the passage's. A passage that does not
        occur on the page fails; nothing falls back to the authored text.
        """
        if artifact.media_type != "application/pdf":
            return self._failure(
                artifact,
                ExtractionOutcome.MEDIA_TYPE_MISMATCH,
                detail="a page-text region requires a PDF source",
            )

        if self._pdf_text_extractor is None:
            return self._failure(
                artifact,
                ExtractionOutcome.EXTRACTOR_UNAVAILABLE,
                observed_size_bytes=len(payload),
                detail=(
                    "no PDF text extractor is configured, and a sidecar text file is "
                    f"never accepted as extraction. {OPTIONAL_EXTRA_HINT}"
                ),
            )

        try:
            page_text = self._pdf_text_extractor.extract_page_text(
                payload, page_number=selector.page_number
            )
        except ValueError as exc:
            return self._failure(
                artifact,
                ExtractionOutcome.MALFORMED_SOURCE,
                observed_size_bytes=len(payload),
                detail=f"PDF text extraction failed: {exc}",
            )

        if len(page_text) > self._limits.maximum_text_characters:
            return self._failure(
                artifact,
                ExtractionOutcome.SIZE_LIMIT_EXCEEDED,
                observed_size_bytes=len(payload),
                detail="extracted page text exceeds the configured character ceiling",
            )

        if selector.is_page_scoped:
            passage = region.exact_text
            if passage is None:
                return self._failure(
                    artifact,
                    ExtractionOutcome.BLOCKED_BY_POLICY,
                    observed_size_bytes=len(payload),
                    detail=(
                        "a page-scoped text region carries no passage to locate; there is "
                        "nothing to resolve against the extracted page"
                    ),
                )
            located = locate_passage(page_text, passage)
            if located is None:
                return self._failure(
                    artifact,
                    ExtractionOutcome.REGION_OUT_OF_BOUNDS,
                    observed_size_bytes=len(payload),
                    detail=(
                        f"the region's passage does not occur exactly once on page "
                        f"{selector.page_number} of the extracted text"
                    ),
                )
            start, end = located
        else:
            # Both offsets are present together; the selector's own
            # validator guarantees it.
            assert selector.start_offset is not None and selector.end_offset is not None
            start, end = selector.start_offset, selector.end_offset
            if end > len(page_text):
                return self._failure(
                    artifact,
                    ExtractionOutcome.REGION_OUT_OF_BOUNDS,
                    observed_size_bytes=len(payload),
                    detail=(
                        f"selector ends at offset {end} but page "
                        f"{selector.page_number} holds {len(page_text)} characters"
                    ),
                )

        return SourceArtifactExtractionResult(
            outcome=ExtractionOutcome.EXTRACTED,
            source_id=artifact.source_id,
            extractor_id=self._pdf_text_extractor.extractor_id,
            extractor_version=self._pdf_text_extractor.extractor_version,
            observed_sha256=artifact.sha256,
            observed_media_type=artifact.media_type,
            observed_size_bytes=len(payload),
            text=page_text[start:end],
        )

    def _failure(
        self,
        artifact: SourceArtifactRef,
        outcome: ExtractionOutcome,
        *,
        observed_size_bytes: int | None = None,
        detail: str | None = None,
    ) -> SourceArtifactExtractionResult:
        return SourceArtifactExtractionResult(
            outcome=outcome,
            source_id=artifact.source_id,
            extractor_id=self.extractor_id,
            extractor_version=self.extractor_version,
            observed_size_bytes=observed_size_bytes,
            detail=detail,
        )
