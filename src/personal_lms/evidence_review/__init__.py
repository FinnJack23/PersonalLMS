"""Evidence review: the reviewer-only boundary that authorizes evidence.

Nothing in this package approves anything. It records decisions a human
made, binds each one to the exact bytes reviewed, and refuses a decision
whose content has since changed. Approval is always an input, never an
output.

Layout:

- ``protocol`` — append-only repository contract (no update, no delete).
- ``service``  — freshness and supersession rules over that repository.
- ``sqlite``   — the only concrete repository, with namespaced migrations.
- ``errors``   — typed, reason-coded failures.

The domain contracts live in ``personal_lms.domain.evidence_review``.
"""

from __future__ import annotations

from personal_lms.evidence_review.authority import (
    EvidenceAuthoritySnapshot,
    subject_digest_for,
)
from personal_lms.evidence_review.errors import (
    EvidenceReviewContractError,
    EvidenceReviewError,
    EvidenceReviewImmutableError,
    EvidenceReviewNotFoundError,
    EvidenceReviewStorageError,
    StaleEvidenceReviewError,
)
from personal_lms.evidence_review.protocol import EvidenceReviewRepository
from personal_lms.evidence_review.service import EvidenceReviewService
from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository

__all__ = [
    "EvidenceAuthoritySnapshot",
    "EvidenceReviewContractError",
    "EvidenceReviewError",
    "EvidenceReviewImmutableError",
    "EvidenceReviewNotFoundError",
    "EvidenceReviewRepository",
    "EvidenceReviewService",
    "EvidenceReviewStorageError",
    "SQLiteEvidenceReviewRepository",
    "StaleEvidenceReviewError",
    "subject_digest_for",
]
