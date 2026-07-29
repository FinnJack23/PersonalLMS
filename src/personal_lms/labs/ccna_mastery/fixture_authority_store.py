"""Persisted fixture authority: whether a human approved an entire frozen tree.

This is a different concept from ``evidence_review``: that package records a
decision about one evidence region (a passage, an image crop). This module
records a decision about an entire frozen fixture manifest, identified only
by its self-hash — the "is this whole reviewed tree ready for a gate run"
decision that ``FixtureAuthority.from_reviewer_decision`` (see ``gates.py``)
was always able to represent but that no production path ever supplied,
because nothing persisted one.

The binding is deliberately narrow: a decision names a ``manifest_hash`` and
nothing else. There is no pack id, version, or objective scoping, because a
frozen fixture's self-hash already covers every byte in the tree — a single
changed byte anywhere changes the hash, which means a prior decision simply
will not be found under the new hash. Nothing has to remember to check for
staleness; the lookup key *is* the staleness check.

Nothing in this module manufactures an approval. ``current_fixture_authority``
fails closed (``draft_for_human_review``) for a missing decision, a decision
whose outcome is not an approval, or — structurally, since lookups are keyed
by the exact current hash — a decision made against a since-changed fixture.
A pack's own authored ``fixture_status`` is never read here; see
``FixtureAuthority.from_manifest_claim`` for why that claim can never become
authority.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import AwareDatetime, Field, field_validator

from personal_lms.domain.base import StrictModel
from personal_lms.domain.evidence_review import EvidenceReviewOutcome, ReviewerIdentity
from personal_lms.labs.ccna_mastery.gates import FixtureAuthority

__all__ = [
    "FixtureAuthorityDecision",
    "SQLiteFixtureAuthorityRepository",
    "current_fixture_authority",
]

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _valid_manifest_hash(value: str) -> str:
    if not _SHA256_HEX_PATTERN.fullmatch(value):
        raise ValueError("manifest_hash must be exactly 64 lowercase hex characters")
    return value


class FixtureAuthorityDecision(StrictModel):
    """One immutable, reviewer-authored decision about one exact manifest hash.

    Append-only by convention, matching ``evidence_review``: a reviewer
    changing their mind appends a new decision rather than editing this
    one. The repository never issues an ``UPDATE`` or ``DELETE``.
    """

    manifest_hash: str
    reviewer: ReviewerIdentity
    outcome: EvidenceReviewOutcome
    reason: str = Field(min_length=1, max_length=2_000)
    decided_at: AwareDatetime

    @field_validator("manifest_hash")
    @classmethod
    def _hash_is_valid(cls, value: str) -> str:
        return _valid_manifest_hash(value)

    @field_validator("decided_at")
    @classmethod
    def _decided_at_is_canonical_utc(cls, value: datetime) -> datetime:
        """Convert an aware timestamp to UTC, preserving the instant.

        ``AwareDatetime`` only guarantees *an* offset, not a common one, and
        ``current_for`` orders these timestamps as ISO-8601 text in SQLite.
        Lexical order over mixed offsets is not chronological:
        ``2026-07-27T09:00:00-05:00`` is two hours *later* than
        ``2026-07-27T12:00:00+00:00`` but sorts before it, which would let a
        stale approval outrank the rejection that revoked it. Normalizing here
        rather than at the call site means every stored column, the persisted
        ``record_json``, and the value handed to ``FixtureAuthority`` all carry
        the same canonical instant, so text order is chronological order.
        """
        return value.astimezone(UTC)

    @property
    def is_approval(self) -> bool:
        return self.outcome is EvidenceReviewOutcome.APPROVED


_SCHEMA_STATEMENTS = (
    # Namespaced migrations table, matching evidence_review's reasoning:
    # this repository may share a database file with
    # SQLiteEvidenceReviewRepository, and an unnamespaced
    # schema_migrations(version) table would let the first repository to
    # initialize suppress the other's schema.
    """
    CREATE TABLE IF NOT EXISTS fixture_authority_schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fixture_authority_decisions (
        manifest_hash TEXT NOT NULL,
        reviewer_id TEXT NOT NULL,
        outcome TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        record_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fixture_authority_manifest_hash "
    "ON fixture_authority_decisions(manifest_hash)",
)


class SQLiteFixtureAuthorityRepository:
    """Append-only SQLite store for fixture-authority decisions.

    Python standard library only, parameterized queries throughout — the
    same discipline ``evidence_review.sqlite`` documents and for the same
    reason: this module never interpolates caller input into SQL text.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    @classmethod
    def open(cls, database_path: str | Path) -> Self:
        """Open (or create) a database at ``database_path``.

        ``:memory:`` is accepted for isolated tests; no temporary file is
        ever created implicitly.
        """
        return cls(sqlite3.connect(str(database_path)))

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
        with self._connection:
            for statement in _SCHEMA_STATEMENTS:
                self._connection.execute(statement)
            self._connection.execute(
                "INSERT OR IGNORE INTO fixture_authority_schema_migrations "
                "(version, applied_at) VALUES (1, ?)",
                (datetime.now(UTC).isoformat(),),
            )

    def append(self, decision: FixtureAuthorityDecision) -> FixtureAuthorityDecision:
        """Append one decision. Never updates or deletes an existing row."""
        with self._connection:
            self._connection.execute(
                "INSERT INTO fixture_authority_decisions "
                "(manifest_hash, reviewer_id, outcome, decided_at, record_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    decision.manifest_hash,
                    decision.reviewer.reviewer_id,
                    decision.outcome.value,
                    decision.decided_at.isoformat(),
                    decision.model_dump_json(),
                ),
            )
        return decision

    def current_for(self, manifest_hash: str) -> FixtureAuthorityDecision | None:
        """The most recently recorded decision for this exact hash, if any.

        Deliberately "most recent by decided_at", not "the first
        approval": a later rejection must be able to revoke an earlier
        approval of the same never-changed manifest hash.

        Ordering the ``decided_at`` text is only chronological because
        ``FixtureAuthorityDecision`` normalizes every timestamp to canonical
        UTC before it is written; ``rowid DESC`` breaks an exact-instant tie
        in favour of the row appended last.
        """
        row = self._connection.execute(
            "SELECT record_json FROM fixture_authority_decisions "
            "WHERE manifest_hash = ? ORDER BY decided_at DESC, rowid DESC LIMIT 1",
            (manifest_hash,),
        ).fetchone()
        if row is None:
            return None
        return FixtureAuthorityDecision.model_validate_json(row["record_json"])


def current_fixture_authority(
    repository: SQLiteFixtureAuthorityRepository, *, manifest_hash: str
) -> FixtureAuthority:
    """The gate-facing ``FixtureAuthority`` for ``manifest_hash``.

    Built only from a persisted decision, never from a pack's own
    authored claim. Fails closed to an unauthoritative ``FixtureAuthority``
    (which always resolves to ``draft_for_human_review``) when nothing is
    persisted for this exact hash or the current decision is not an
    approval — including the "stale" and "mismatched" cases, which need
    no separate check because a decision made against a different
    manifest hash is simply never found under this one.
    """
    _valid_manifest_hash(manifest_hash)
    decision = repository.current_for(manifest_hash)
    if decision is None or not decision.is_approval:
        return FixtureAuthority(manifest_hash=manifest_hash)
    return FixtureAuthority.from_reviewer_decision(
        manifest_hash=manifest_hash,
        reviewer_id=decision.reviewer.reviewer_id,
        decided_at=decision.decided_at,
    )
