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

from personal_lms.domain.objective_packs import (
    EvidenceRegion,
    ImageRegionSelector,
    PageTextSelector,
    SourceArtifactRef,
)
from personal_lms.extraction.artifacts import (
    DEFAULT_EXTRACTION_LIMITS,
    SUPPORTED_MEDIA_TYPES,
    ExtractionLimits,
    ExtractionOutcome,
    PdfTextExtractor,
    SourceArtifactExtractionResult,
    inspect_png_dimensions,
    region_fits_image,
    verify_source_bytes,
)
from personal_lms.objective_packs.errors import ObjectivePackError
from personal_lms.objective_packs.loader import PackFileReader

__all__ = ["LOCAL_FIXTURE_EXTRACTOR_ID", "LocalFixtureExtractor"]

LOCAL_FIXTURE_EXTRACTOR_ID = "local-fixture"
_EXTRACTOR_VERSION = "1.0"


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
        limits: ExtractionLimits = DEFAULT_EXTRACTION_LIMITS,
    ) -> None:
        self._reader = reader
        self._pdf_text_extractor = pdf_text_extractor
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
        return self._extract_page_text(region.selector, artifact, payload)

    def _extract_image_region(
        self,
        selector: ImageRegionSelector,
        artifact: SourceArtifactRef,
        payload: bytes,
    ) -> SourceArtifactExtractionResult:
        """Validate a PNG region structurally. Produces no text — this gate has no OCR."""
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

        if not region_fits_image(selector, dimensions):
            return self._failure(
                artifact,
                ExtractionOutcome.REGION_OUT_OF_BOUNDS,
                observed_size_bytes=len(payload),
                detail=(
                    f"region collapses to zero pixels on a "
                    f"{dimensions.width}x{dimensions.height} image"
                ),
            )

        return SourceArtifactExtractionResult(
            outcome=ExtractionOutcome.EXTRACTED,
            source_id=artifact.source_id,
            extractor_id=self.extractor_id,
            extractor_version=self.extractor_version,
            observed_sha256=artifact.sha256,
            observed_media_type=artifact.media_type,
            observed_size_bytes=len(payload),
            image_width=dimensions.width,
            image_height=dimensions.height,
            text=None,
        )

    def _extract_page_text(
        self,
        selector: PageTextSelector,
        artifact: SourceArtifactRef,
        payload: bytes,
    ) -> SourceArtifactExtractionResult:
        """Extract a page-text region through the injected PDF seam."""
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
                    "no PDF text extractor is configured; the parser dependency is an "
                    "unresolved human decision and a sidecar text file is never "
                    "accepted as extraction"
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

        if selector.end_offset > len(page_text):
            return self._failure(
                artifact,
                ExtractionOutcome.REGION_OUT_OF_BOUNDS,
                observed_size_bytes=len(payload),
                detail=(
                    f"selector ends at offset {selector.end_offset} but page "
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
            text=page_text[selector.start_offset : selector.end_offset],
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
