"""Provider-neutral Obsidian vault access protocol.

Structural contract for reading and writing an Obsidian vault. Read and
write operations are clearly separated: reads never touch write state,
and every write goes through a two-step prepare-then-commit process —
``prepare_note_write()`` performs no filesystem write and returns a
deterministic, immutable ``ObsidianWritePlan``; ``commit_write()``
requires a matching, explicit ``ObsidianWriteApproval``.

No implementation in this codebase touches a real filesystem or a real
Obsidian vault — see ``personal_lms.vault.fake`` for the only
implementation, an in-memory fake for deterministic tests. A future real
adapter (not part of this milestone) would live alongside it, behind this
same protocol.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from personal_lms.domain.enums import ObsidianWriteIntent
from personal_lms.domain.obsidian import (
    ObsidianAttachmentAssociationRequest,
    ObsidianAttachmentAssociationResult,
    ObsidianNoteListRequest,
    ObsidianNoteListResult,
    ObsidianNoteReadRequest,
    ObsidianNoteReadResult,
    ObsidianNoteWriteRequest,
    ObsidianWriteApproval,
    ObsidianWritePlan,
    ObsidianWriteRejection,
    ObsidianWriteResult,
)
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.reconstruction import ObsidianArtifactLink
from personal_lms.domain.vault import VaultNoteDraft


def compute_content_hash(note: VaultNoteDraft) -> str:
    """Deterministic SHA-256 over a note's canonical semantic content.

    Covers the Markdown side only (path, title, frontmatter, body,
    citations) — see ``compute_approval_digest`` for the broader digest
    that actually gates approval. ``created_at`` is deliberately excluded:
    it timestamps when the in-memory draft object was constructed, not the
    note's semantic content, and including it would make the hash change
    on every call even for identical content.
    """
    canonical = json.dumps(
        {
            "relative_path": note.relative_path,
            "title": note.title,
            "frontmatter": note.frontmatter,
            "body_markdown": note.body_markdown,
            "citations": [citation.model_dump(mode="json") for citation in note.citations],
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_approval_digest(
    *,
    note: VaultNoteDraft,
    attachment_link: ObsidianArtifactLink | None,
    source_ids: Sequence[str],
    privacy_classification: PrivacyClassification,
    approved_for_rag: bool,
    write_intent: ObsidianWriteIntent,
    overwrite: bool,
) -> str:
    """The canonical deterministic digest that gates an Obsidian write approval.

    Binds every approval-relevant field — not just the Markdown body — so
    an approval issued for one plan can never be reused for another plan
    that differs in any of these, even if ``plan_id`` is reused and the
    Markdown content is byte-for-byte identical. Takes plain field values
    (not an ``ObsidianWritePlan``) so it can be called both before a plan
    exists (``prepare_note_write``) and by re-reading an existing plan's
    live fields at commit time (``commit_write`` must recompute this, never
    trust a plan's own stored ``approval_digest``).

    Excludes only non-semantic/runtime values: ``plan_id`` (an identifier,
    not content) and ``created_at`` (a timestamp) — every field that
    changes what would actually be written, or under what authorization,
    is included:

    - target relative path and Markdown semantic content (via
      ``compute_content_hash``);
    - attachment/PDF association (``attachment_link``);
    - source/provenance ids (``source_ids``);
    - privacy classification;
    - the RAG-approval flag (``approved_for_rag``);
    - write intent (create/update);
    - overwrite intent.
    """
    canonical = json.dumps(
        {
            "note_content_hash": compute_content_hash(note),
            "attachment_link": (
                attachment_link.model_dump(mode="json") if attachment_link is not None else None
            ),
            "source_ids": list(source_ids),
            "privacy_classification": privacy_classification.value,
            "approved_for_rag": approved_for_rag,
            "write_intent": write_intent.value,
            "overwrite": overwrite,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@runtime_checkable
class ObsidianVault(Protocol):
    """Structural contract for Obsidian vault access.

    Performs no OCR, PDF processing, provider/model call, RAG retrieval
    or generation, or CrewAI orchestration — this protocol only reads and
    writes already-constructed domain objects, behind an explicit
    two-step write-approval boundary.
    """

    # --- read --------------------------------------------------------------

    def read_note(self, request: ObsidianNoteReadRequest) -> ObsidianNoteReadResult:
        """Read exactly one Markdown note by its vault-relative path."""
        ...

    def list_notes(self, request: ObsidianNoteListRequest | None = None) -> ObsidianNoteListResult:
        """List already-committed notes, optionally filtered."""
        ...

    # --- prepare (no side effects) ------------------------------------------

    def prepare_note_write(self, request: ObsidianNoteWriteRequest) -> ObsidianWritePlan:
        """Build a deterministic write plan. Performs no filesystem write."""
        ...

    def prepare_attachment_association(
        self, request: ObsidianAttachmentAssociationRequest
    ) -> ObsidianAttachmentAssociationResult:
        """Build an ``ObsidianArtifactLink``. Performs no filesystem write."""
        ...

    # --- commit / reject -----------------------------------------------------

    def commit_write(
        self, plan: ObsidianWritePlan, approval: ObsidianWriteApproval
    ) -> ObsidianWriteResult:
        """Commit ``plan``. Requires an ``approval`` matching its exact
        ``plan_id`` and a freshly recomputed ``approval_digest``
        (``compute_approval_digest``, not the plan's stored value);
        raises otherwise."""
        ...

    def reject_write(
        self,
        plan: ObsidianWritePlan,
        *,
        reason: str,
        rejected_by: str | None = None,
    ) -> ObsidianWriteRejection:
        """Record that ``plan`` was rejected or cancelled. Never mutates the vault."""
        ...

    def close(self) -> None: ...
