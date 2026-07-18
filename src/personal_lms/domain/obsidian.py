"""Obsidian access contracts: read results and the two-step write-approval boundary.

Pure data shapes only — no filesystem access, no real Obsidian vault, no
provider or model call happens in this module. See ``personal_lms.vault``
for the provider-neutral ``ObsidianVault`` protocol and the in-memory
``FakeObsidianVault`` that consumes these contracts.

Every write goes through two steps, modeled as two separate objects:

1. ``ObsidianWritePlan`` — a deterministic proposal with no side effects.
   Frozen (unlike most schemas in this codebase, which follow
   ``StrictModel``'s mutable-but-validated convention): a plan cannot be
   edited in place. Changing anything about a proposed write means
   building a new plan, which gets a new ``approval_digest`` and therefore
   cannot be committed with an approval issued for the old one — "editing"
   and "invalidating prior approval" are the same operation by
   construction, not a rule enforced by extra bookkeeping. The digest
   (``compute_approval_digest`` in ``personal_lms.vault.protocol``) binds
   every approval-relevant field — target path, Markdown semantic content,
   attachment/PDF association, provenance ids, privacy classification,
   RAG-approval flag, write intent, and overwrite intent — not just the
   Markdown body, so changing any one of them, even while reusing the same
   ``plan_id``, invalidates a prior approval.
2. ``ObsidianWriteApproval`` — likewise frozen, explicit human authorization
   binding to one exact ``plan_id`` and ``approval_digest``. Committing
   without one, or with one that does not match, must be impossible — see
   ``ObsidianVault.commit_write()``, which recomputes the digest from the
   plan's live fields rather than trusting the plan's own stored value.

Reconstruction approval (``ReconstructionCandidate.approval_status``),
Obsidian-write approval (``ObsidianWriteApproval``), and trusted-RAG
approval (``approved_for_rag`` below) are three independent decisions,
tracked on three different objects/fields — none of the three implies
either of the others.
"""

from __future__ import annotations

from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from personal_lms.domain.base import StrictModel, utcnow
from personal_lms.domain.enums import ObsidianWriteIntent
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.reconstruction import ObsidianArtifactLink
from personal_lms.domain.vault import VaultNoteDraft

_SHA256_HEX_LENGTH = 64
_SHA256_HEX_ALPHABET = frozenset("0123456789abcdef")


def _no_absolute_or_traversal_path(value: str) -> str:
    """Mirrors ``VaultNoteDraft.relative_path``'s safety check (domain/vault.py)."""
    if value.startswith("/") or value.startswith("~"):
        raise ValueError("path must be relative, not absolute")
    if ".." in value.split("/"):
        raise ValueError("path must not contain '..' segments")
    return value


def _valid_sha256_hex(value: str) -> str:
    if len(value) != _SHA256_HEX_LENGTH or not set(value) <= _SHA256_HEX_ALPHABET:
        raise ValueError("must be exactly 64 lowercase hex characters")
    return value


# --- read ------------------------------------------------------------------


class ObsidianNoteReadRequest(StrictModel):
    """A request to read exactly one Markdown note by its vault-relative path."""

    relative_path: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def _relative_path_is_safe(cls, value: str) -> str:
        return _no_absolute_or_traversal_path(value)


class ObsidianNoteReadResult(StrictModel):
    """The outcome of reading one note. ``note`` is set iff ``exists`` is ``True``."""

    relative_path: str = Field(min_length=1)
    exists: bool
    note: VaultNoteDraft | None = None
    content_hash: str | None = Field(default=None, min_length=1)

    @field_validator("content_hash")
    @classmethod
    def _content_hash_is_valid_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _valid_sha256_hex(value)

    @model_validator(mode="after")
    def _note_present_iff_exists(self) -> Self:
        if self.exists and self.note is None:
            raise ValueError("note is required when exists is True")
        if not self.exists and self.note is not None:
            raise ValueError("note must be unset when exists is False")
        return self


class ObsidianNoteSummary(StrictModel):
    """A lightweight listing entry — no full Markdown body."""

    relative_path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    approved_for_rag: bool
    content_hash: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)

    @field_validator("content_hash")
    @classmethod
    def _content_hash_is_valid_sha256(cls, value: str) -> str:
        return _valid_sha256_hex(value)


class ObsidianNoteListRequest(StrictModel):
    """Criteria narrowing a ``list_notes`` call. Every field is optional."""

    path_prefix: str | None = Field(default=None, min_length=1)
    approved_for_rag_only: bool = False

    @field_validator("path_prefix")
    @classmethod
    def _path_prefix_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _no_absolute_or_traversal_path(value)


class ObsidianNoteListResult(StrictModel):
    notes: list[ObsidianNoteSummary] = Field(default_factory=list)


# --- attachment association (pure, no side effects) -------------------------


class ObsidianAttachmentAssociationRequest(StrictModel):
    """A request to associate a note with an optional PDF and/or attachment
    reference. Produces an ``ObsidianArtifactLink`` — no filesystem write."""

    markdown_note_path: str = Field(min_length=1)
    associated_pdf_path: str | None = Field(default=None, min_length=1)
    attachment_reference: str | None = Field(default=None, min_length=1)


class ObsidianAttachmentAssociationResult(StrictModel):
    link: ObsidianArtifactLink


# --- write: prepare (no side effects) ---------------------------------------


class ObsidianNoteWriteRequest(StrictModel):
    """A caller's request to prepare a note write.

    Turned into an ``ObsidianWritePlan`` by
    ``ObsidianVault.prepare_note_write()`` — preparing performs no
    filesystem write.
    """

    note: VaultNoteDraft
    source_ids: list[str] = Field(min_length=1)
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    approved_for_rag: bool = False
    overwrite: bool = False
    attachment_link: ObsidianArtifactLink | None = None


class ObsidianWritePlan(StrictModel):
    """A deterministic, side-effect-free proposal to write one Obsidian note.

    Frozen — see the module docstring for why. ``approval_digest`` is
    computed by the adapter preparing the plan (``compute_approval_digest``
    in ``personal_lms.vault.protocol``) over every approval-relevant field
    below, never supplied by the caller directly; this schema only
    validates its format, mirroring ``SourceRecord.sha256_hash``.
    """

    model_config = ConfigDict(frozen=True)

    plan_id: UUID = Field(default_factory=uuid4)
    note: VaultNoteDraft
    attachment_link: ObsidianArtifactLink | None = None
    source_ids: list[str] = Field(min_length=1)
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    approved_for_rag: bool = False
    approval_digest: str = Field(min_length=1)
    write_intent: ObsidianWriteIntent
    overwrite: bool = False
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @field_validator("approval_digest")
    @classmethod
    def _approval_digest_is_valid_sha256(cls, value: str) -> str:
        return _valid_sha256_hex(value)


# --- write: commit / reject ------------------------------------------------


class ObsidianWriteApproval(StrictModel):
    """Explicit human approval for exactly one ``ObsidianWritePlan``.

    Frozen — see the module docstring. Binds to ``plan_id`` and
    ``approval_digest`` together; a commit must reject any approval whose
    pair does not match the plan being committed exactly, verified by
    recomputing the digest fresh from the plan's fields — see
    ``ObsidianVault.commit_write()``.
    """

    model_config = ConfigDict(frozen=True)

    approval_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    approval_digest: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    overwrite_confirmed: bool = False
    approved_at: AwareDatetime = Field(default_factory=utcnow)

    @field_validator("approval_digest")
    @classmethod
    def _approval_digest_is_valid_sha256(cls, value: str) -> str:
        return _valid_sha256_hex(value)


class ObsidianWriteResult(StrictModel):
    """The outcome of a successfully committed write.

    Embeds the full (frozen) ``plan`` rather than re-declaring its fields
    — the plan's approval_digest, provenance, privacy classification, and
    RAG-approval flag are already exactly what was committed.
    """

    plan: ObsidianWritePlan
    approval_id: UUID
    committed_at: AwareDatetime = Field(default_factory=utcnow)


class ObsidianWriteRejection(StrictModel):
    """A recorded rejection or cancellation of a proposed write.

    The vault is never mutated by a rejection — the plan was never
    committed — this exists purely for the audit trail.
    """

    plan: ObsidianWritePlan
    reason: str = Field(min_length=1)
    rejected_by: str | None = Field(default=None, min_length=1)
    rejected_at: AwareDatetime = Field(default_factory=utcnow)
