"""Screenshot-to-document reconstruction contracts.

Pure data shapes only — no OCR, vision calls, image processing, PDF
generation, or filesystem writes happen here. These schemas describe the
proposal/approval/output pipeline for turning a batch of raw screenshots
(or other page images) into a reviewed, image-faithful document:

- ``ReconstructionCandidate`` — one proposed document group, awaiting
  human approval (``approval_status``, reusing the existing
  ``ApprovalStatus`` enum — this is a bounded human decision, structurally
  identical to any other approval in this codebase);
- ``ReconstructionManifest`` — every candidate proposed for one input
  batch, plus every source id the batch touched but could not resolve into
  a candidate (``unresolved_source_ids``) — never silently dropped;
- ``ReconstructedDocument`` — the approved output: an image-faithful
  document, its provenance back to every original image, and separately
  its own trusted-RAG eligibility (``trusted_for_rag``, always defaulted
  ``False``). Approving a candidate's grouping never implies the resulting
  document is trusted RAG corpus material — those stay two independent
  decisions, tracked on two different fields on two different schemas;
- ``ObsidianArtifactLink`` — a reference to where the reconstructed
  document lives in Obsidian. No write method, no filesystem or vault
  access (mirrors ``domain/vault.py``'s ``VaultNoteDraft``).
"""

from __future__ import annotations

from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, field_validator, model_validator

from personal_lms.domain.base import StrictModel, utcnow
from personal_lms.domain.enums import ApprovalStatus, SearchableTextStatus, SourceType
from personal_lms.domain.knowledge_scope import KnowledgeScope


def _reject_duplicate_ids(value: list[str]) -> list[str]:
    if len(value) != len(set(value)):
        raise ValueError("ordered source ids must not contain duplicates")
    return value


def _no_absolute_or_traversal_path(value: str) -> str:
    """Mirrors ``VaultNoteDraft.relative_path``'s safety check (domain/vault.py)."""
    if value.startswith("/") or value.startswith("~"):
        raise ValueError("path must be relative, not absolute")
    if ".." in value.split("/"):
        raise ValueError("path must not contain '..' segments")
    return value


class ObsidianArtifactLink(StrictModel):
    """A reference to where a reconstructed document lives in Obsidian."""

    markdown_note_path: str = Field(min_length=1)
    associated_pdf_path: str | None = Field(default=None, min_length=1)
    attachment_reference: str | None = Field(default=None, min_length=1)

    @field_validator("markdown_note_path")
    @classmethod
    def _markdown_note_path_is_relative(cls, value: str) -> str:
        return _no_absolute_or_traversal_path(value)

    @field_validator("associated_pdf_path")
    @classmethod
    def _associated_pdf_path_is_relative(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _no_absolute_or_traversal_path(value)


class ReconstructedDocument(StrictModel):
    """The approved output of one ``ReconstructionCandidate``."""

    document_id: UUID = Field(default_factory=uuid4)
    candidate_id: UUID = Field(
        description="Correlates to the originating ReconstructionCandidate.candidate_id."
    )
    source_image_ids: list[str] = Field(
        min_length=1,
        description="Approved page order; also the provenance chain to every original image.",
    )
    pdf_reference: str | None = Field(
        default=None,
        min_length=1,
        description="Opaque reference to the generated PDF (e.g. a SourceRecord.source_id).",
    )
    obsidian_link: ObsidianArtifactLink | None = None
    graphics_preserved: bool = False
    searchable_text_status: SearchableTextStatus = SearchableTextStatus.NONE
    trusted_for_rag: bool = False
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @field_validator("source_image_ids")
    @classmethod
    def _unique_source_image_ids(cls, value: list[str]) -> list[str]:
        return _reject_duplicate_ids(value)

    @model_validator(mode="after")
    def _pdf_reference_requires_graphics_preserved(self) -> Self:
        if self.pdf_reference is not None and not self.graphics_preserved:
            raise ValueError(
                "pdf_reference requires graphics_preserved=True: only an "
                "image-faithful reconstruction may reference a generated PDF"
            )
        return self


class ReconstructionCandidate(StrictModel):
    """One proposed document group awaiting human approval."""

    candidate_id: UUID = Field(default_factory=uuid4)
    proposed_title: str | None = Field(default=None, min_length=1)
    ordered_source_ids: list[str] = Field(min_length=1)
    proposed_document_type: SourceType
    confidence: float = Field(ge=0, le=1)
    grouping_rationale: str = Field(min_length=1)
    page_order_rationale: str | None = Field(default=None, min_length=1)
    warnings: list[str] = Field(default_factory=list)
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    knowledge_scope: KnowledgeScope | None = None
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @field_validator("ordered_source_ids")
    @classmethod
    def _unique_ordered_source_ids(cls, value: list[str]) -> list[str]:
        return _reject_duplicate_ids(value)


class ReconstructionManifest(StrictModel):
    """All reconstruction candidates proposed for one input batch, plus
    every source id that batch touched but could not be grouped.
    """

    manifest_id: UUID = Field(default_factory=uuid4)
    batch_label: str | None = Field(default=None, min_length=1)
    candidates: list[ReconstructionCandidate] = Field(default_factory=list)
    unresolved_source_ids: list[str] = Field(default_factory=list)
    duplicate_source_ids: list[str] = Field(default_factory=list)
    requires_human_review: bool = True
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @field_validator("requires_human_review")
    @classmethod
    def _human_review_is_always_required(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "requires_human_review must be True: proposed groups and page "
                "order always require human approval"
            )
        return value
