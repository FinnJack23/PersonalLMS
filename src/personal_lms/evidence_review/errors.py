"""Typed errors for the evidence review boundary.

Every error names a violated rule rather than a storage failure. The
review boundary exists to make unauthorized approval structurally
impossible, so its errors are part of the security contract, not
incidental plumbing.
"""

from __future__ import annotations


class EvidenceReviewError(Exception):
    """Base class for every evidence-review failure."""

    reason_code = "evidence_review_error"


class EvidenceReviewNotFoundError(EvidenceReviewError):
    """No decision exists for the requested identity."""

    reason_code = "evidence_review_not_found"


class EvidenceReviewImmutableError(EvidenceReviewError):
    """An attempt to overwrite or delete an existing decision.

    Review history is append-only: a reviewer changing their mind appends
    a superseding decision. Nothing rewrites the record of what was
    decided before.
    """

    reason_code = "evidence_review_immutable"


class StaleEvidenceReviewError(EvidenceReviewError):
    """A decision was offered for content that has since changed.

    Raised when the region's current content hash or its source's hash
    differs from the ones a decision was made against. Approving a region
    whose bytes moved underneath the reviewer would authorize content no
    human ever saw.
    """

    reason_code = "evidence_review_stale"


class EvidenceReviewContractError(EvidenceReviewError):
    """A decision violates the repository's structural contract.

    Covers a supersession chain naming an unknown decision, a decision
    superseding one about a different region, and similar
    self-inconsistencies.
    """

    reason_code = "evidence_review_contract"


class EvidenceReviewStorageError(EvidenceReviewError):
    """The underlying store failed. Never carries store-internal detail."""

    reason_code = "evidence_review_storage"


__all__ = [
    "EvidenceReviewContractError",
    "EvidenceReviewError",
    "EvidenceReviewImmutableError",
    "EvidenceReviewNotFoundError",
    "EvidenceReviewStorageError",
    "StaleEvidenceReviewError",
]
