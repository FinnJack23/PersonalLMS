"""Extraction Queue: persistence-neutral protocol, SQLite implementation,
and a test/development-only fake extractor.

See ``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md`` for
the full design. This package implements no real extraction — no PDF,
OCR, transcription, or archive handling of any kind. It only queues,
claims, and tracks the lifecycle of extraction *jobs*, and records
*metadata* about their results
(``personal_lms.domain.extraction.ExtractedArtifact``).
"""

from personal_lms.extraction.errors import (
    ExtractionArtifactNotFoundError,
    ExtractionJobNotFoundError,
    ExtractionQueueContractError,
    ExtractionQueueError,
    ExtractionQueueStorageError,
    InvalidExtractionJobTransitionError,
)
from personal_lms.extraction.fake import FakeExtractor
from personal_lms.extraction.protocol import ExtractionJobFilter, ExtractionQueue
from personal_lms.extraction.sqlite import SQLiteExtractionQueue

__all__ = [
    "ExtractionArtifactNotFoundError",
    "ExtractionJobFilter",
    "ExtractionJobNotFoundError",
    "ExtractionQueue",
    "ExtractionQueueContractError",
    "ExtractionQueueError",
    "ExtractionQueueStorageError",
    "FakeExtractor",
    "InvalidExtractionJobTransitionError",
    "SQLiteExtractionQueue",
]
