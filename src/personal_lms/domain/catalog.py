"""Source catalog contracts: cataloged assets and their relationships.

Pure data shapes only — no filesystem scanning, hashing, OCR, image
processing, PDF generation, SQLite, or Obsidian access happens here. See
``docs/product-specs/OBSIDIAN_SOURCE_ARCHITECTURE.md`` for the raw
archive/candidate/curated-vault storage classes this catalog sits in front
of, and ``domain/reconstruction.py`` for the screenshot-to-document
reconstruction contracts that produce new entries here.
"""

from __future__ import annotations

import re
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, field_validator, model_validator

from personal_lms.domain.base import StrictModel, utcnow
from personal_lms.domain.enums import SourceProcessingStatus, SourceRelationshipType, SourceType
from personal_lms.domain.knowledge_scope import KnowledgeScope
from personal_lms.domain.privacy import PrivacyClassification

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ProvenanceMetadata(StrictModel):
    """Typed context for how a source entered the catalog."""

    imported_at: AwareDatetime = Field(default_factory=utcnow)
    imported_by: str | None = Field(default=None, min_length=1)
    acquisition_note: str | None = Field(default=None, min_length=1)


class SourceRecord(StrictModel):
    """One cataloged asset.

    Either an original raw file (``status=raw``, ``is_generated_artifact``
    must be ``False``) or a generated artifact — e.g. a reconstructed PDF —
    that has itself been cataloged (``is_generated_artifact=True``,
    typically linked back to its originals via ``SourceAssetRelationship``
    and, for reconstruction outputs specifically, via
    ``ReconstructedDocument.source_image_ids``). This schema never modifies
    or deletes the file it describes.
    """

    source_id: str = Field(min_length=1)
    source_type: SourceType
    original_location: str = Field(
        min_length=1,
        description="Original path or URI. Never modified or deleted by this schema.",
    )
    filename: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    sha256_hash: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    status: SourceProcessingStatus = SourceProcessingStatus.RAW
    is_generated_artifact: bool = False
    knowledge_scopes: list[KnowledgeScope] = Field(default_factory=list)
    provenance: ProvenanceMetadata = Field(default_factory=ProvenanceMetadata)

    @field_validator("sha256_hash")
    @classmethod
    def _sha256_is_64_lowercase_hex_chars(cls, value: str) -> str:
        if not _SHA256_HEX_PATTERN.fullmatch(value):
            raise ValueError("sha256_hash must be exactly 64 lowercase hex characters")
        return value

    @model_validator(mode="after")
    def _raw_status_cannot_be_a_generated_artifact(self) -> Self:
        if self.status is SourceProcessingStatus.RAW and self.is_generated_artifact:
            raise ValueError(
                "a source with status=raw (an original) cannot be marked is_generated_artifact"
            )
        return self


class SourceAssetRelationship(StrictModel):
    """A directed link between two cataloged sources.

    Covers originals-to-derived, superseded versions, duplicates,
    attachments, and reconstruction provenance through one
    ``relationship_type`` enum rather than a separate schema per link kind.
    """

    relationship_id: UUID = Field(default_factory=uuid4)
    source_id: str = Field(min_length=1)
    related_source_id: str = Field(min_length=1)
    relationship_type: SourceRelationshipType
    note: str | None = Field(default=None, min_length=1)
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _source_cannot_relate_to_itself(self) -> Self:
        if self.source_id == self.related_source_id:
            raise ValueError("source_id and related_source_id must differ")
        return self
