"""Source-artifact extraction protocol and its deterministic verification seams.

Extends the existing extraction package rather than paralleling it. The
queue in ``extraction.protocol`` schedules *jobs*; this module defines
what an extractor actually does with the bytes a job points at, and the
checks every extractor must pass before it is allowed to look at them.

Everything here is deterministic and local: byte hashing, magic-number
sniffing, size ceilings, and PNG header parsing. There is no network
access, no subprocess, no OCR, and no model call. Running any of it twice
over the same bytes gives the same answer.

The PDF decision is deliberately open
-------------------------------------

``PdfTextExtractor`` is an *injected protocol*, not an implementation.
Choosing a PDF parsing dependency is a human decision (see AD-01 in
``docs/plans/ccna-mastery-micro-lab/ARCHITECTURE_DELTA.md``), and this
pass does not make it. Production code depends only on the protocol;
tests supply a deterministic double. When a parser is approved, it becomes
one new adapter class implementing this protocol — no caller changes.

Nothing here decides eligibility. A successful extraction says the bytes
were readable, never that the content may be taught, indexed, or shown to
a model. That decision belongs to
``objective_packs.eligibility.EvidenceEligibility`` and, for approval, to
``evidence_review``.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field

from personal_lms.domain.base import StrictModel
from personal_lms.domain.objective_packs import ImageRegionSelector

__all__ = [
    "DEFAULT_EXTRACTION_LIMITS",
    "SUPPORTED_MEDIA_TYPES",
    "ExtractionLimits",
    "ExtractionOutcome",
    "ImageDimensions",
    "PdfTextExtractor",
    "SourceArtifactExtractionResult",
    "SourceArtifactExtractor",
    "detect_media_type",
    "inspect_png_dimensions",
    "region_fits_image",
    "verify_source_bytes",
]

#: Media types this gate's extractors accept. Anything else yields an
#: explicit ``UNSUPPORTED_MEDIA_TYPE`` outcome rather than a best-effort
#: attempt — a silently mis-parsed file is worse than a refusal.
SUPPORTED_MEDIA_TYPES: frozenset[str] = frozenset({"application/pdf", "image/png"})

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PDF_SIGNATURE = b"%PDF-"

# A PNG's IHDR chunk always begins at byte 8 and carries width and height
# as the first two big-endian 32-bit fields of its data. Reading exactly
# those eight bytes needs no image library and cannot execute anything
# embedded in the file.
_IHDR_WIDTH_OFFSET = 16
_IHDR_HEIGHT_OFFSET = 20
_MINIMUM_PNG_HEADER_BYTES = 24


class ExtractionOutcome(StrEnum):
    """Why an extraction attempt ended the way it did.

    Every non-success outcome is explicit and stable: a caller can
    distinguish "this file type is out of scope" from "these bytes are
    not what the manifest pinned" without parsing a message. Gate reports
    cite these values, so they are append-only.
    """

    EXTRACTED = "extracted"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    MEDIA_TYPE_MISMATCH = "media_type_mismatch"
    HASH_MISMATCH = "hash_mismatch"
    SIZE_LIMIT_EXCEEDED = "size_limit_exceeded"
    MALFORMED_SOURCE = "malformed_source"
    REGION_OUT_OF_BOUNDS = "region_out_of_bounds"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    EXTRACTOR_UNAVAILABLE = "extractor_unavailable"


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    """Ceilings applied before any parsing work begins.

    ``maximum_source_bytes`` guards memory; ``maximum_image_pixels``
    guards the decompression-bomb shape where a small file declares an
    enormous canvas. Both are checked from headers, never by decoding.
    """

    maximum_source_bytes: int = 32 * 1024 * 1024
    maximum_image_pixels: int = 50_000_000
    maximum_text_characters: int = 2_000_000


DEFAULT_EXTRACTION_LIMITS = ExtractionLimits()


@dataclass(frozen=True, slots=True)
class ImageDimensions:
    """A raster image's declared pixel dimensions."""

    width: int
    height: int

    @property
    def pixels(self) -> int:
        return self.width * self.height


class SourceArtifactExtractionResult(StrictModel):
    """One extraction attempt's complete, stable outcome.

    Always constructed — success and failure alike — so a caller never
    has to catch an exception to learn that a file was the wrong type.
    ``text`` is populated only on success, and is never a partial or
    best-effort value: an extractor that could not read the whole region
    reports ``MALFORMED_SOURCE`` instead.
    """

    outcome: ExtractionOutcome
    source_id: str = Field(min_length=1)
    extractor_id: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    observed_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    observed_media_type: str | None = Field(default=None, min_length=1)
    observed_size_bytes: int | None = Field(default=None, ge=0)
    image_width: int | None = Field(default=None, gt=0)
    image_height: int | None = Field(default=None, gt=0)
    text: str | None = None
    detail: str | None = Field(default=None, min_length=1)

    @property
    def succeeded(self) -> bool:
        return self.outcome is ExtractionOutcome.EXTRACTED


@runtime_checkable
class PdfTextExtractor(Protocol):
    """Injected seam for reading text out of PDF bytes.

    Intentionally unimplemented in production code in this pass. The
    parser dependency is a human decision; until it is made, callers
    receive ``ExtractionOutcome.EXTRACTOR_UNAVAILABLE`` when no
    implementation is supplied, and tests inject a deterministic double.

    An implementation must be pure with respect to its input: the same
    bytes and page number always produce the same text, with no network
    access, no subprocess, and no ambient configuration.
    """

    @property
    def extractor_id(self) -> str: ...

    @property
    def extractor_version(self) -> str: ...

    def extract_page_text(self, payload: bytes, *, page_number: int) -> str:
        """Text of one 1-indexed page.

        Raises ``ValueError`` for a page that does not exist or bytes that
        are not a readable PDF — never returns a partial or guessed
        result.
        """
        ...


@runtime_checkable
class SourceArtifactExtractor(Protocol):
    """Structural contract for reading content out of one frozen source artifact."""

    @property
    def extractor_id(self) -> str: ...

    @property
    def extractor_version(self) -> str: ...

    def supports(self, media_type: str) -> bool:
        """Whether this extractor handles ``media_type`` at all."""
        ...


def detect_media_type(payload: bytes) -> str | None:
    """The media type implied by ``payload``'s magic number, or ``None``.

    Sniffing the bytes rather than trusting a declared type is what makes
    ``MEDIA_TYPE_MISMATCH`` detectable: a PDF renamed to ``.png`` is
    caught here, before any parser sees it.
    """
    if payload.startswith(_PNG_SIGNATURE):
        return "image/png"
    if payload.startswith(_PDF_SIGNATURE):
        return "application/pdf"
    return None


def verify_source_bytes(
    payload: bytes,
    *,
    expected_sha256: str,
    expected_media_type: str,
    limits: ExtractionLimits = DEFAULT_EXTRACTION_LIMITS,
) -> ExtractionOutcome:
    """Every byte-level check, in a fixed order, returning the first failure.

    Order is size, then media type support, then sniffed-type agreement,
    then hash. Size comes first because it is the only check that bounds
    the work the others do; hash comes last because it is the most
    expensive and the least informative about *why* a file is wrong.

    Returns ``ExtractionOutcome.EXTRACTED`` when every check passes —
    meaning "these bytes are admissible", not "content was produced".
    """
    if len(payload) > limits.maximum_source_bytes:
        return ExtractionOutcome.SIZE_LIMIT_EXCEEDED
    if expected_media_type not in SUPPORTED_MEDIA_TYPES:
        return ExtractionOutcome.UNSUPPORTED_MEDIA_TYPE

    sniffed = detect_media_type(payload)
    if sniffed is None or sniffed != expected_media_type:
        return ExtractionOutcome.MEDIA_TYPE_MISMATCH

    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        return ExtractionOutcome.HASH_MISMATCH

    return ExtractionOutcome.EXTRACTED


def inspect_png_dimensions(
    payload: bytes, *, limits: ExtractionLimits = DEFAULT_EXTRACTION_LIMITS
) -> ImageDimensions | None:
    """Width and height of a structurally complete PNG, or ``None``.

    Reads the eight IHDR bytes that carry the dimensions, then walks the
    chunk structure to confirm the file is actually an image rather than a
    plausible-looking header. No image library is involved and nothing is
    decoded, so a file declaring an implausible canvas is rejected from
    ``limits.maximum_image_pixels`` rather than being allocated.

    Structural completeness matters because a signature plus an IHDR is
    trivially forgeable and carries no pixels: accepting one would let a
    24-byte stub stand in for the infographic a human is supposed to
    review.
    """
    if len(payload) < _MINIMUM_PNG_HEADER_BYTES:
        return None
    if not payload.startswith(_PNG_SIGNATURE):
        return None
    if payload[12:16] != b"IHDR":
        return None

    width, height = struct.unpack(">II", payload[_IHDR_WIDTH_OFFSET : _IHDR_HEIGHT_OFFSET + 4])
    if width == 0 or height == 0:
        return None
    if width * height > limits.maximum_image_pixels:
        return None
    if not _png_chunks_are_complete(payload):
        return None
    return ImageDimensions(width=int(width), height=int(height))


def _png_chunks_are_complete(payload: bytes) -> bool:
    """Whether ``payload`` is a structurally and cryptographically sound PNG.

    Walks the length-prefixed chunk sequence — never searches for byte
    patterns, so a stray ``IDAT`` inside another chunk's payload cannot
    fake completeness — and verifies four things a plausible-looking stub
    cannot satisfy:

    1. every chunk's CRC-32 matches its type and data;
    2. at least one ``IDAT`` chunk exists and carries data;
    3. the concatenated ``IDAT`` stream actually inflates;
    4. the inflated stream is long enough for the canvas ``IHDR``
       declares.

    Together these mean "this file contains the image it claims to". The
    earlier version checked only that ``IDAT`` and ``IEND`` chunk *types*
    appeared, so a 60-byte file with a corrupt CRC and fifteen bytes of
    non-zlib garbage reported a readable 64x48 image — and a human
    reviewer would have been asked to approve nothing at all.

    Standard library only: ``zlib`` supplies both CRC-32 and inflate, so
    this needs no image dependency and decodes no pixels.
    """
    if len(payload) < len(_PNG_SIGNATURE) + 12:
        return False

    offset = len(_PNG_SIGNATURE)
    data_segments: list[bytes] = []
    saw_iend = False
    header: bytes | None = None

    while offset + 8 <= len(payload):
        (length,) = struct.unpack(">I", payload[offset : offset + 4])
        chunk_type = payload[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if length > len(payload) or crc_end > len(payload):
            return False

        data = payload[data_start:data_end]
        (declared_crc,) = struct.unpack(">I", payload[data_end:crc_end])
        if zlib.crc32(chunk_type + data) != declared_crc:
            return False

        if chunk_type == b"IHDR":
            header = data
        elif chunk_type == b"IDAT":
            data_segments.append(data)
        elif chunk_type == b"IEND":
            saw_iend = True
            if crc_end != len(payload):
                return False
            break

        offset = crc_end

    if not saw_iend or header is None or not data_segments:
        return False

    compressed = b"".join(data_segments)
    if not compressed:
        return False

    try:
        inflated = zlib.decompress(compressed)
    except zlib.error:
        return False

    return _inflated_covers_canvas(inflated, header)


def _inflated_covers_canvas(inflated: bytes, header: bytes) -> bool:
    """Whether the decompressed stream holds a full canvas of scanlines.

    A PNG scanline is one filter byte plus ``ceil(width * bits_per_pixel /
    8)`` data bytes, and there is one scanline per row. A stream shorter
    than that describes fewer rows than ``IHDR`` claims, so the file is
    truncated whatever its chunk structure says.

    Interlaced images (``interlace == 1``) lay their data out in seven
    reduced passes whose total is smaller; rather than reimplement Adam7
    here, they are accepted on chunk integrity alone — this gate's
    fixtures are non-interlaced, and guessing at a layout would be worse
    than declining to check it.
    """
    if len(header) < 13:
        return False

    width, height = struct.unpack(">II", header[0:8])
    bit_depth = header[8]
    colour_type = header[9]
    interlace = header[12]

    if interlace == 1:
        return True

    samples = _SAMPLES_PER_PIXEL.get(colour_type)
    if samples is None or bit_depth == 0:
        return False

    bits_per_pixel = samples * bit_depth
    row_bytes = (width * bits_per_pixel + 7) // 8
    required_bytes: int = (row_bytes + 1) * height
    return len(inflated) >= required_bytes


#: Samples per pixel for each PNG colour type, from the PNG specification.
#: Any other value is not a colour type this validator will vouch for.
_SAMPLES_PER_PIXEL: dict[int, int] = {
    0: 1,  # greyscale
    2: 3,  # truecolour
    3: 1,  # indexed
    4: 2,  # greyscale + alpha
    6: 4,  # truecolour + alpha
}


def region_fits_image(selector: ImageRegionSelector, dimensions: ImageDimensions) -> bool:
    """Whether a normalized region resolves to at least one pixel.

    The selector's basis-point box is already range- and order-checked by
    its own validators, so the only question left is whether it survives
    conversion to this image's actual pixel grid: a box thinner than one
    pixel of a small image collapses to nothing and cannot be a real
    region.
    """
    left = selector.left_basis_points * dimensions.width // 10_000
    right = selector.right_basis_points * dimensions.width // 10_000
    top = selector.top_basis_points * dimensions.height // 10_000
    bottom = selector.bottom_basis_points * dimensions.height // 10_000
    return right > left and bottom > top
