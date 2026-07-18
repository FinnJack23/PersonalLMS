"""Promotion Bridge: the explicit boundary between raw-archive source
inventory and the existing, unmodified curated
``personal_lms.catalog.SourceCatalog``.

See ``docs/product-specs/SOURCE_PROMOTION_AND_EXTRACTION_QUEUE.md`` for
the full design. No extraction completion, candidate creation, or
approved decision ever promotes anything automatically — promotion is
always an explicit ``SourcePromotionService.promote()`` call.
"""

from personal_lms.promotion.eligibility import evaluate_promotion_eligibility
from personal_lms.promotion.errors import (
    PromotionBlockedError,
    PromotionCandidateNotFoundError,
    PromotionDecisionRequiredError,
    PromotionError,
    PromotionMappingConflictError,
    PromotionRepositoryContractError,
    PromotionRepositoryStorageError,
    PromotionSourceVersionNotFoundError,
)
from personal_lms.promotion.protocol import PromotionRepository
from personal_lms.promotion.service import SourcePromotionService
from personal_lms.promotion.sqlite import SQLitePromotionRepository

__all__ = [
    "PromotionBlockedError",
    "PromotionCandidateNotFoundError",
    "PromotionDecisionRequiredError",
    "PromotionError",
    "PromotionMappingConflictError",
    "PromotionRepository",
    "PromotionRepositoryContractError",
    "PromotionRepositoryStorageError",
    "PromotionSourceVersionNotFoundError",
    "SQLitePromotionRepository",
    "SourcePromotionService",
    "evaluate_promotion_eligibility",
]
