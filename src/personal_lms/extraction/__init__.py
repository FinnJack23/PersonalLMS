"""Extraction Queue: persistence-neutral protocol, SQLite implementation,
and a test/development-only fake extractor.

See ``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md`` for
the full design. The queue side of this package implements no real
extraction — no PDF, OCR, transcription, or archive handling of any kind.
It only queues, claims, and tracks the lifecycle of extraction *jobs*, and
records *metadata* about their results
(``personal_lms.domain.extraction.ExtractedArtifact``).

``artifacts`` and ``local_fixture`` add the narrow source-artifact seam
alongside that queue: deterministic byte, media-type, size, and PNG-header
verification, plus one bounded local fixture adapter. Still no OCR, and
still no PDF parser choice — text extraction sits behind an injected
``PdfTextExtractor`` protocol whose dependency decision belongs to the
human operator.
"""

from personal_lms.extraction.artifacts import (
    SUPPORTED_MEDIA_TYPES,
    ExtractionLimits,
    ExtractionOutcome,
    ImageDimensions,
    PdfTextExtractor,
    SourceArtifactExtractionResult,
    SourceArtifactExtractor,
    detect_media_type,
    inspect_png_dimensions,
    region_fits_image,
    verify_source_bytes,
)
from personal_lms.extraction.errors import (
    ExtractionArtifactNotFoundError,
    ExtractionJobNotFoundError,
    ExtractionQueueContractError,
    ExtractionQueueError,
    ExtractionQueueStorageError,
    InvalidExtractionJobTransitionError,
)
from personal_lms.extraction.fake import FakeExtractor
from personal_lms.extraction.local_fixture import LocalFixtureExtractor
from personal_lms.extraction.protocol import ExtractionJobFilter, ExtractionQueue
from personal_lms.extraction.sqlite import SQLiteExtractionQueue

__all__ = [
    "SUPPORTED_MEDIA_TYPES",
    "ExtractionArtifactNotFoundError",
    "ExtractionJobFilter",
    "ExtractionJobNotFoundError",
    "ExtractionLimits",
    "ExtractionOutcome",
    "ExtractionQueue",
    "ExtractionQueueContractError",
    "ExtractionQueueError",
    "ExtractionQueueStorageError",
    "FakeExtractor",
    "ImageDimensions",
    "InvalidExtractionJobTransitionError",
    "LocalFixtureExtractor",
    "PdfTextExtractor",
    "SQLiteExtractionQueue",
    "SourceArtifactExtractionResult",
    "SourceArtifactExtractor",
    "detect_media_type",
    "inspect_png_dimensions",
    "region_fits_image",
    "verify_source_bytes",
]
