"""Persistence-neutral Promotion Repository protocol.

Structural contract for storing and retrieving ``PromotionCandidate``,
``PromotionDecision``, ``PromotionMapping``, ``PromotionExecution``, and
``PromotionEvent`` objects. No implementation lives here — see
``promotion/sqlite.py`` for the only concrete implementation in this
codebase.

Synchronous throughout, local disk or in-memory I/O only (SQLite today).
Every state-mutating method accepts an explicit ``now: AwareDatetime``
parameter rather than reading the system clock itself — see
``personal_lms.extraction.protocol``'s identical convention.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from personal_lms.domain.promotion import (
    PromotionCandidate,
    PromotionDecision,
    PromotionEvent,
    PromotionExecution,
    PromotionExecutionState,
    PromotionMapping,
)


@runtime_checkable
class PromotionRepository(Protocol):
    """Structural contract for promotion-bridge persistence."""

    def initialize_schema(self) -> None:
        """Create the repository's schema if it does not already exist.

        Must be safe to call more than once against the same store.
        """
        ...

    def create_candidate(self, candidate: PromotionCandidate) -> PromotionCandidate:
        """Insert a new candidate, or return the existing one sharing its
        (deterministic) ``candidate_id`` — never a duplicate logical
        candidate. Raises ``PromotionRepositoryContractError`` if the
        existing candidate's identity fields differ from ``candidate``."""
        ...

    def get_candidate(self, candidate_id: UUID) -> PromotionCandidate:
        """Raises ``PromotionCandidateNotFoundError`` if unknown."""
        ...

    def list_candidates_for_source(
        self, inventory_source_id: UUID
    ) -> tuple[PromotionCandidate, ...]: ...

    def record_decision(self, decision: PromotionDecision) -> PromotionDecision:
        """Append-only: a decision is never updated once recorded. Raises
        ``PromotionCandidateNotFoundError`` if ``decision.candidate_id`` is
        unknown."""
        ...

    def get_latest_decision(self, candidate_id: UUID) -> PromotionDecision | None:
        """The most recently recorded decision for ``candidate_id``
        (ordered by ``created_at`` then ``decision_id`` for a deterministic
        tie-break), or ``None`` if no decision has been recorded."""
        ...

    def list_decisions(self, candidate_id: UUID) -> tuple[PromotionDecision, ...]:
        """The complete, immutable decision history for ``candidate_id``,
        oldest first."""
        ...

    def get_mapping(self, inventory_source_id: UUID) -> PromotionMapping | None: ...

    def create_mapping(self, mapping: PromotionMapping) -> PromotionMapping:
        """Insert a new mapping, or return the existing one for
        ``mapping.inventory_source_id`` — idempotent. Raises
        ``PromotionMappingConflictError`` if an existing mapping's
        ``catalog_source_id`` differs from ``mapping.catalog_source_id``
        (which should never happen given a stable, deterministic
        derivation — see ``domain.promotion.derive_catalog_source_id``)."""
        ...

    def get_execution(self, candidate_id: UUID) -> PromotionExecution | None: ...

    def save_execution(self, execution: PromotionExecution) -> PromotionExecution:
        """Insert or update the single execution row for
        ``execution.candidate_id``."""
        ...

    def has_completed_promotion_for_source_version(
        self,
        inventory_source_id: UUID,
        source_version_id: UUID,
        *,
        exclude_candidate_id: UUID | None = None,
    ) -> bool:
        """Whether any candidate for this exact
        ``(inventory_source_id, source_version_id)`` pair, other than
        ``exclude_candidate_id``, has a ``PromotionExecution`` in
        ``PromotionExecutionState.COMPLETED``.

        Deliberately scoped to one source *version*, not the whole
        inventory source: under the Strategy B identity policy (see
        ``domain.promotion.derive_catalog_source_id``), promoting a later
        version of the same already-promoted source is the intended,
        supported workflow (it updates the same curated record) — only a
        second, different candidate for the *same* version is a genuine
        conflict."""
        ...

    def record_event(self, event: PromotionEvent) -> PromotionEvent:
        """Append-only."""
        ...

    def list_events(self, candidate_id: UUID) -> tuple[PromotionEvent, ...]:
        """Oldest first."""
        ...

    def close(self) -> None: ...


__all__ = ["PromotionExecutionState", "PromotionRepository"]
