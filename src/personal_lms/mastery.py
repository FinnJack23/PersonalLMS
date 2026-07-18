"""Minimal local review-result persistence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from personal_lms.domain.base import StrictModel


class ReviewStatus(StrEnum):
    MASTERED = "MASTERED"
    REVIEW_SOON = "REVIEW_SOON"
    RETEACH_REQUIRED = "RETEACH_REQUIRED"
    EVIDENCE_GAP = "EVIDENCE_GAP"


class MasteryRecord(StrictModel):
    record_id: UUID = Field(default_factory=uuid4)
    learning_session_id: UUID
    learning_objective: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    selected_answer: str = Field(min_length=1)
    correct: bool
    review_status: ReviewStatus
    weak_area_tags: tuple[str, ...] = ()
    created_at: datetime


class SQLiteMasteryStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS mastery_records ("
            "record_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self._db.commit()

    @classmethod
    def open(cls, path: str) -> SQLiteMasteryStore:
        return cls(sqlite3.connect(path))

    def save(self, record: MasteryRecord) -> MasteryRecord:
        self._db.execute(
            "INSERT OR REPLACE INTO mastery_records VALUES (?, ?)",
            (str(record.record_id), record.model_dump_json()),
        )
        self._db.commit()
        return record

    def list(self) -> tuple[MasteryRecord, ...]:
        rows = self._db.execute("SELECT payload FROM mastery_records ORDER BY rowid").fetchall()
        return tuple(MasteryRecord.model_validate_json(row[0]) for row in rows)


def new_session_id() -> UUID:
    return uuid4()


def utc_now() -> datetime:
    return datetime.now(UTC)
