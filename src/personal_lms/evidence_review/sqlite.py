"""SQLite implementation of the append-only evidence review repository.

Python standard library only (``sqlite3``; JSON fidelity comes from
Pydantic's own ``model_dump_json``/``model_validate_json``) — no new
dependency. Every query is parameterized; this module never interpolates
caller input into SQL text.

**Namespaced migrations, deliberately.** The existing source-inventory,
extraction, and promotion repositories each own an unnamespaced
``schema_migrations(version)`` table, which means initializing two of them
against one database file lets the first version-1 row suppress the
other's schema — a known hazard recorded in
``docs/plans/ccna-mastery-micro-lab/ARCHITECTURE_DELTA.md``'s persistence
section. This repository is new, so it uses
``evidence_review_schema_migrations`` instead and can safely share a file
with any of them. It does not "fix" the existing three; that is a
separate, human-authorized migration decision.

**Append-only enforcement.** There is no UPDATE or DELETE statement
anywhere in this module. A repeated ``append`` of a byte-identical
decision is idempotent; a differing one for the same ``decision_id``
raises. Supersession is recorded by a new row naming the prior one.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self
from uuid import UUID

from personal_lms.domain.evidence_review import EvidenceReviewDecision
from personal_lms.evidence_review.errors import (
    EvidenceReviewContractError,
    EvidenceReviewImmutableError,
    EvidenceReviewNotFoundError,
    EvidenceReviewStorageError,
)

__all__ = ["SQLiteEvidenceReviewRepository"]

_SCHEMA_VERSION = 1

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS evidence_review_schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_review_decisions (
        decision_id TEXT PRIMARY KEY,
        evidence_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        pack_id TEXT NOT NULL,
        pack_version TEXT NOT NULL,
        objective_ref TEXT NOT NULL,
        kind TEXT NOT NULL,
        outcome TEXT NOT NULL,
        subject_digest TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        reviewer_id TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        supersedes_decision_id TEXT
            REFERENCES evidence_review_decisions(decision_id),
        record_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_evidence_review_evidence_id "
    "ON evidence_review_decisions(evidence_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_review_outcome ON evidence_review_decisions(outcome)",
    # One child per decision, enforced by the database rather than only by
    # the service: a fork would make "the current decision" ambiguous, and
    # a unique index is the one place a concurrent writer cannot race past.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_review_single_child "
    "ON evidence_review_decisions(supersedes_decision_id) "
    "WHERE supersedes_decision_id IS NOT NULL",
    # One root per region, for the same reason applied to the chain's head.
    # One root per *logical subject*, not per evidence id. Two packs may
    # legitimately review the same evidence id; scoping the chain by the
    # id alone made the second pack's root collide with the first's.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_review_single_root "
    "ON evidence_review_decisions(evidence_id, pack_id, pack_version, objective_ref) "
    "WHERE supersedes_decision_id IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_evidence_review_decided_at "
    "ON evidence_review_decisions(decided_at, decision_id)",
)


class SQLiteEvidenceReviewRepository:
    """SQLite-backed ``EvidenceReviewRepository``. Structurally conforms to the protocol."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def open(cls, database_path: str | Path) -> Self:
        """Open (or create) a database at ``database_path``.

        ``:memory:`` is accepted and is what tests use — no temporary file
        is ever created implicitly.
        """
        connection = sqlite3.connect(str(database_path))
        return cls(connection)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def initialize_schema(self) -> None:
        try:
            with self._connection:
                for statement in _SCHEMA_STATEMENTS:
                    self._connection.execute(statement)
                self._connection.execute(
                    "INSERT OR IGNORE INTO evidence_review_schema_migrations (version, applied_at) "
                    "VALUES (?, ?)",
                    (_SCHEMA_VERSION, datetime.now(UTC).isoformat()),
                )
        except sqlite3.Error as exc:
            raise EvidenceReviewStorageError("failed to initialize the review schema") from exc

    def append(self, decision: EvidenceReviewDecision) -> EvidenceReviewDecision:
        existing = self._find(decision.decision_id)
        if existing is not None:
            if existing == decision:
                return existing
            raise EvidenceReviewImmutableError(
                "a different decision already exists with this decision_id; review "
                "history is append-only and is never overwritten"
            )

        if decision.supersedes_decision_id is not None:
            superseded = self._find(decision.supersedes_decision_id)
            if superseded is None:
                raise EvidenceReviewContractError(
                    "supersedes_decision_id names a decision this repository does not hold"
                )
            if superseded.evidence_id != decision.evidence_id:
                raise EvidenceReviewContractError(
                    "a decision may only supersede another decision about the same evidence region"
                )

        try:
            # One transaction covers the whole append, so the unique
            # indexes on "single child" and "single root" are evaluated
            # against a consistent view: two writers racing to fork a
            # chain cannot both succeed.
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO evidence_review_decisions
                        (decision_id, evidence_id, source_id, pack_id, pack_version,
                         objective_ref, kind, outcome, subject_digest, source_sha256,
                         reviewer_id, decided_at, supersedes_decision_id, record_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(decision.decision_id),
                        decision.evidence_id,
                        decision.source_id,
                        decision.pack_id,
                        decision.pack_version,
                        decision.objective_ref,
                        decision.kind.value,
                        decision.outcome.value,
                        decision.subject_digest,
                        decision.source_sha256,
                        decision.reviewer.reviewer_id,
                        decision.decided_at.isoformat(),
                        str(decision.supersedes_decision_id)
                        if decision.supersedes_decision_id is not None
                        else None,
                        decision.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            # A concurrent writer may have inserted this exact decision
            # between our existence check and our insert. That is an
            # idempotent retry, not a conflict: reread and return the
            # winning row, but only when it is byte-identical to ours.
            # Anything else is a genuine contract violation.
            raced = self._find(decision.decision_id)
            if raced is not None:
                if raced == decision:
                    return raced
                raise EvidenceReviewImmutableError(
                    "a different decision already exists with this decision_id; review "
                    "history is append-only and is never overwritten"
                ) from exc

            # SQLite reports a partial-unique-index violation by *column*
            # ("UNIQUE constraint failed: table.column"), never by index
            # name, so the branches below match on the column.
            message = str(exc)
            if "supersedes_decision_id" in message:
                raise EvidenceReviewContractError(
                    "that decision has already been superseded; review history is linear, "
                    "so a correction must name the current leaf"
                ) from exc
            if "evidence_id" in message:
                raise EvidenceReviewContractError(
                    "this region already has a root decision; a second root would make "
                    "the current decision ambiguous"
                ) from exc
            raise EvidenceReviewImmutableError(
                "a decision with this decision_id already exists"
            ) from exc
        except sqlite3.Error as exc:
            raise EvidenceReviewStorageError("failed to append the review decision") from exc
        return decision

    def get(self, decision_id: UUID) -> EvidenceReviewDecision:
        found = self._find(decision_id)
        if found is None:
            raise EvidenceReviewNotFoundError(f"no review decision with id {decision_id}")
        return found

    def history_for(self, evidence_id: str) -> tuple[EvidenceReviewDecision, ...]:
        rows = self._connection.execute(
            "SELECT record_json FROM evidence_review_decisions WHERE evidence_id = ? "
            "ORDER BY decided_at, decision_id",
            (evidence_id,),
        ).fetchall()
        return tuple(EvidenceReviewDecision.model_validate_json(row["record_json"]) for row in rows)

    def history_for_subject(
        self,
        *,
        evidence_id: str,
        pack_id: str,
        pack_version: str,
        objective_ref: str,
    ) -> tuple[EvidenceReviewDecision, ...]:
        """Every decision about one complete logical subject, oldest first."""
        rows = self._connection.execute(
            "SELECT record_json FROM evidence_review_decisions WHERE evidence_id = ? "
            "AND pack_id = ? AND pack_version = ? AND objective_ref = ? "
            "ORDER BY decided_at, decision_id",
            (evidence_id, pack_id, pack_version, objective_ref),
        ).fetchall()
        return tuple(EvidenceReviewDecision.model_validate_json(row["record_json"]) for row in rows)

    def current_for_subject(
        self,
        *,
        evidence_id: str,
        pack_id: str,
        pack_version: str,
        objective_ref: str,
    ) -> EvidenceReviewDecision | None:
        """The unsuperseded decision for one complete logical subject.

        Scoping by the full subject rather than ``evidence_id`` alone is
        what lets two packs review the same evidence id independently.
        """
        row = self._connection.execute(
            """
            SELECT record_json FROM evidence_review_decisions AS current
            WHERE current.evidence_id = ?
              AND current.pack_id = ?
              AND current.pack_version = ?
              AND current.objective_ref = ?
              AND NOT EXISTS (
                    SELECT 1 FROM evidence_review_decisions AS later
                    WHERE later.supersedes_decision_id = current.decision_id
              )
            ORDER BY current.decided_at DESC, current.decision_id DESC
            LIMIT 1
            """,
            (evidence_id, pack_id, pack_version, objective_ref),
        ).fetchone()
        if row is None:
            return None
        return EvidenceReviewDecision.model_validate_json(row["record_json"])

    def current_for(self, evidence_id: str) -> EvidenceReviewDecision | None:
        """The one decision about ``evidence_id`` that nothing supersedes.

        Implemented as a ``NOT EXISTS`` anti-join rather than "the newest
        row": a superseding decision is authoritative even when its
        timestamp is not the largest, which can happen when decisions are
        backfilled with their real review instants.

        When more than one decision is unsuperseded (a genuinely branched
        history that ``append``'s contract checks do not forbid), the
        latest by ``decided_at`` then ``decision_id`` wins — deterministic
        rather than arbitrary.
        """
        row = self._connection.execute(
            """
            SELECT record_json FROM evidence_review_decisions AS current
            WHERE current.evidence_id = ?
              AND NOT EXISTS (
                    SELECT 1 FROM evidence_review_decisions AS later
                    WHERE later.supersedes_decision_id = current.decision_id
              )
            ORDER BY current.decided_at DESC, current.decision_id DESC
            LIMIT 1
            """,
            (evidence_id,),
        ).fetchone()
        if row is None:
            return None
        return EvidenceReviewDecision.model_validate_json(row["record_json"])

    def _find(self, decision_id: UUID) -> EvidenceReviewDecision | None:
        row = self._connection.execute(
            "SELECT record_json FROM evidence_review_decisions WHERE decision_id = ?",
            (str(decision_id),),
        ).fetchone()
        if row is None:
            return None
        return EvidenceReviewDecision.model_validate_json(row["record_json"])
