"""Persistence-neutral evidence review repository protocol.

Structural contract for appending and reading reviewer decisions. No
implementation lives here — see ``evidence_review/sqlite.py`` for the only
concrete one in this codebase.

Append-only by contract: there is deliberately no ``update`` and no
``delete`` method. A repository that offered one could not honour the
audit guarantee the review boundary depends on. Corrections happen through
``append`` with ``supersedes_decision_id`` set.

Synchronous throughout, local disk or in-memory I/O only. Every method
that records a decision takes the decision whole — including its
``decided_at`` — rather than reading the system clock, matching the
explicit-clock convention used across ``source_inventory``,
``extraction``, and ``promotion``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from personal_lms.domain.evidence_review import EvidenceReviewDecision

__all__ = ["EvidenceReviewRepository"]


@runtime_checkable
class EvidenceReviewRepository(Protocol):
    """Structural contract for append-only evidence-review persistence."""

    def initialize_schema(self) -> None:
        """Create the repository's schema if it does not already exist.

        Must be safe to call more than once against the same store.
        """
        ...

    def append(self, decision: EvidenceReviewDecision) -> EvidenceReviewDecision:
        """Record one decision.

        Idempotent for a byte-identical repeat of the same
        ``decision_id`` — the stored decision is returned unchanged.
        Raises ``EvidenceReviewImmutableError`` when the same
        ``decision_id`` already exists with *different* content, and
        ``EvidenceReviewContractError`` when ``supersedes_decision_id``
        names an unknown decision or one about a different evidence
        region.
        """
        ...

    def get(self, decision_id: UUID) -> EvidenceReviewDecision:
        """Raises ``EvidenceReviewNotFoundError`` if unknown."""
        ...

    def history_for(self, evidence_id: str) -> tuple[EvidenceReviewDecision, ...]:
        """Every decision about ``evidence_id``, oldest first.

        Deterministic order: ``decided_at`` then ``decision_id``, so two
        decisions sharing an instant still order stably.
        """
        ...

    def history_for_subject(
        self,
        *,
        evidence_id: str,
        pack_id: str,
        pack_version: str,
        objective_ref: str,
    ) -> tuple[EvidenceReviewDecision, ...]:
        """Every decision about one complete logical subject, oldest first.

        Scoping by the full subject rather than ``evidence_id`` alone is
        what lets two packs review the same evidence id independently
        without their chains colliding.
        """
        ...

    def current_for_subject(
        self,
        *,
        evidence_id: str,
        pack_id: str,
        pack_version: str,
        objective_ref: str,
    ) -> EvidenceReviewDecision | None:
        """The unsuperseded decision for one complete logical subject."""
        ...

    def current_for(self, evidence_id: str) -> EvidenceReviewDecision | None:
        """The latest non-superseded decision about ``evidence_id``, or ``None``.

        "Latest" is the decision no other decision supersedes. A region
        with no decisions at all returns ``None`` — which the service
        layer treats as *not approved*, never as an implicit approval.
        """
        ...

    def close(self) -> None: ...
