"""Corpus-document and content-chunk domain contracts.

Pure data shapes only — no filesystem scanning, extraction, OCR,
embedding, vector search, model call, or Obsidian access happens here.
See ``personal_lms.content`` for the persistence-neutral
``ContentRepository`` protocol and the SQLite/FTS5 implementation that
stores and retrieves these objects.

A ``CorpusDocument`` is one promoted, chunkable unit derived from a
``SourceRecord`` (e.g. one PDF, one transcript) — ``document.source_id``
always names the ``SourceRecord`` it came from, never invents provenance.
A ``ContentChunk`` is one retrievable slice of a document's text, carrying
whatever positional provenance (page, section, timestamp range) is
actually known — never a fabricated value for a field that was not
extracted.
"""

from __future__ import annotations

import re
from typing import Self

from pydantic import Field, field_validator, model_validator

from personal_lms.domain.base import StrictModel
from personal_lms.domain.catalog import ProvenanceMetadata
from personal_lms.domain.enums import SourceProcessingStatus
from personal_lms.domain.knowledge_scope import KnowledgeScope
from personal_lms.domain.privacy import PrivacyClassification

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Statuses eligible to back a trusted_for_rag=True chunk — mirrors the
# same three-status set used elsewhere in the catalog/librarian layers
# (see e.g. personal_lms.librarian.grounding._APPROVED_STATUSES):
# RAW/CATALOGED/CANDIDATE/REJECTED/RECONSTRUCTED never represent a
# completed review.
_TRUSTED_ELIGIBLE_STATUSES = frozenset(
    {
        SourceProcessingStatus.APPROVED,
        SourceProcessingStatus.REVIEWED,
        SourceProcessingStatus.TRUSTED_FOR_RAG,
    }
)


def _valid_sha256_hex(value: str) -> str:
    if not _SHA256_HEX_PATTERN.fullmatch(value):
        raise ValueError("must be exactly 64 lowercase hex characters")
    return value


class CorpusDocument(StrictModel):
    """One promoted, chunkable document derived from a cataloged source."""

    document_id: str = Field(min_length=1)
    source_id: str = Field(
        min_length=1, description="Correlates to the originating SourceRecord.source_id."
    )
    title: str = Field(min_length=1)
    language: str | None = Field(default=None, min_length=1)
    version: str | None = Field(default=None, min_length=1)
    status: SourceProcessingStatus = SourceProcessingStatus.RAW
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    knowledge_scopes: list[KnowledgeScope] = Field(default_factory=list)
    content_hash: str = Field(min_length=1)
    provenance: ProvenanceMetadata = Field(default_factory=ProvenanceMetadata)

    @field_validator("content_hash")
    @classmethod
    def _content_hash_is_valid_sha256(cls, value: str) -> str:
        return _valid_sha256_hex(value)


class ContentChunk(StrictModel):
    """One retrievable slice of a ``CorpusDocument``'s text.

    ``trusted_for_rag`` is a second, independent decision from ``status``
    (mirroring ``ReconstructedDocument.trusted_for_rag`` elsewhere in this
    codebase) — it may only be ``True`` when ``status`` itself already
    reflects a completed review (approved, reviewed, or trusted_for_rag);
    see the validator below. A second, repository-level gate additionally
    requires the *parent* ``CorpusDocument.status`` to be one of the same
    three values before a ``trusted_for_rag=True`` chunk may be persisted
    — see ``content.sqlite.SQLiteContentRepository.upsert_chunk`` and
    ``content.errors.ParentDocumentNotApprovedError``. This schema alone
    only knows about its own ``status``, not its parent's.
    """

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(
        min_length=1, description="Correlates to the owning CorpusDocument.document_id."
    )
    source_id: str = Field(
        min_length=1,
        description="Must match the owning CorpusDocument.source_id when persisted.",
    )
    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1)
    text_hash: str = Field(min_length=1)
    page_number: int | None = Field(default=None, gt=0)
    section_title: str | None = Field(default=None, min_length=1)
    timestamp_start_seconds: float | None = Field(
        default=None,
        ge=0,
        description="Must be set together with timestamp_end_seconds, or not at all.",
    )
    timestamp_end_seconds: float | None = Field(
        default=None,
        ge=0,
        description="Must be set together with timestamp_start_seconds, or not at all.",
    )
    knowledge_scopes: list[KnowledgeScope] = Field(default_factory=list)
    status: SourceProcessingStatus = SourceProcessingStatus.RAW
    trusted_for_rag: bool = False
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL

    @field_validator("text_hash")
    @classmethod
    def _text_hash_is_valid_sha256(cls, value: str) -> str:
        return _valid_sha256_hex(value)

    @model_validator(mode="after")
    def _timestamp_range_is_complete_and_ordered(self) -> Self:
        start, end = self.timestamp_start_seconds, self.timestamp_end_seconds
        if (start is None) != (end is None):
            raise ValueError(
                "timestamp_start_seconds and timestamp_end_seconds must both be set, "
                "or both be unset — a partial timestamp range is not allowed"
            )
        if start is not None and end is not None and start > end:
            raise ValueError("timestamp_start_seconds must be <= timestamp_end_seconds")
        return self

    @model_validator(mode="after")
    def _trusted_requires_a_reviewed_status(self) -> Self:
        if self.trusted_for_rag and self.status not in _TRUSTED_ELIGIBLE_STATUSES:
            raise ValueError(
                "trusted_for_rag=True requires status to be approved, reviewed, or trusted_for_rag"
            )
        return self
