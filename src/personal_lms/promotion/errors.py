"""Typed Promotion errors.

Safe context only: candidate/decision identifiers, blocker codes, and a
machine-readable reason. Never raw SQLite error text, proposed metadata,
or any source content.
"""

from __future__ import annotations

from uuid import UUID

from personal_lms.domain.promotion import PromotionBlocker


class PromotionError(Exception):
    """Base class for all Promotion errors."""


class PromotionCandidateNotFoundError(PromotionError):
    """Raised when a referenced ``candidate_id`` has no recorded candidate."""

    def __init__(self, candidate_id: UUID) -> None:
        super().__init__(f"No promotion candidate found with id {candidate_id}")
        self.candidate_id = candidate_id


class PromotionSourceVersionNotFoundError(PromotionError):
    """Raised when a candidate's ``source_version_id`` has no matching
    ``SourceVersion`` recorded for its ``inventory_source_id``."""

    def __init__(self, inventory_source_id: UUID, source_version_id: UUID) -> None:
        super().__init__(
            f"No source version {source_version_id} found for inventory source "
            f"{inventory_source_id}"
        )
        self.inventory_source_id = inventory_source_id
        self.source_version_id = source_version_id


class PromotionDecisionRequiredError(PromotionError):
    """Raised by ``SourcePromotionService.promote`` when no
    ``PromotionDecisionOutcome.APPROVE`` decision exists for the candidate."""

    def __init__(self, candidate_id: UUID) -> None:
        super().__init__(
            f"Promotion candidate {candidate_id} has no approved decision; "
            "promotion is never automatic"
        )
        self.candidate_id = candidate_id


class PromotionBlockedError(PromotionError):
    """Raised when eligibility re-evaluated at execution time fails."""

    def __init__(self, candidate_id: UUID, blockers: tuple[PromotionBlocker, ...]) -> None:
        blocker_text = ", ".join(blocker.value for blocker in blockers)
        super().__init__(
            f"Promotion candidate {candidate_id} is not eligible for promotion: {blocker_text}"
        )
        self.candidate_id = candidate_id
        self.blockers = blockers


class PromotionMappingConflictError(PromotionError):
    """Raised when a newly derived mapping would conflict with an already
    recorded ``PromotionMapping`` for the same inventory source."""

    def __init__(self, inventory_source_id: UUID, reason: str) -> None:
        super().__init__(f"Promotion mapping conflict for source {inventory_source_id}: {reason}")
        self.inventory_source_id = inventory_source_id
        self.reason = reason


class PromotionRepositoryContractError(PromotionError):
    """Raised for an internal contract violation (e.g. an unsupported
    schema version, or an attempt to mutate an immutable decision)."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Promotion repository contract violated: {reason}")
        self.reason = reason


class PromotionRepositoryStorageError(PromotionError):
    """Raised for a sanitized, underlying storage failure.

    Never carries raw SQLite error text — only a fixed, machine-readable
    reason category.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"Promotion repository storage failure: {reason}")
        self.reason = reason
