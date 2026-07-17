"""In-memory ``ObsidianVault`` for deterministic tests only.

Never touches a real filesystem or a real Obsidian vault. Committed notes
live in a plain dict for the lifetime of this object and vanish on
``close()`` — there is no persistence here, deliberately: this is a test
double, not a storage engine.
"""

from __future__ import annotations

from personal_lms.domain.enums import ObsidianWriteIntent
from personal_lms.domain.obsidian import (
    ObsidianAttachmentAssociationRequest,
    ObsidianAttachmentAssociationResult,
    ObsidianNoteListRequest,
    ObsidianNoteListResult,
    ObsidianNoteReadRequest,
    ObsidianNoteReadResult,
    ObsidianNoteSummary,
    ObsidianNoteWriteRequest,
    ObsidianWriteApproval,
    ObsidianWritePlan,
    ObsidianWriteRejection,
    ObsidianWriteResult,
)
from personal_lms.domain.reconstruction import ObsidianArtifactLink
from personal_lms.vault.errors import (
    InvalidApprovalError,
    NotePathConflictError,
    OverwriteNotConfirmedError,
)
from personal_lms.vault.protocol import compute_approval_digest, compute_content_hash


class FakeObsidianVault:
    """Deterministic in-memory test double. Structurally conforms to ``ObsidianVault``."""

    def __init__(self) -> None:
        self._notes: dict[str, ObsidianWriteResult] = {}

    def close(self) -> None:
        self._notes.clear()

    # --- read --------------------------------------------------------------

    def read_note(self, request: ObsidianNoteReadRequest) -> ObsidianNoteReadResult:
        committed = self._notes.get(request.relative_path)
        if committed is None:
            return ObsidianNoteReadResult(relative_path=request.relative_path, exists=False)
        return ObsidianNoteReadResult(
            relative_path=request.relative_path,
            exists=True,
            note=committed.plan.note,
            content_hash=compute_content_hash(committed.plan.note),
        )

    def list_notes(self, request: ObsidianNoteListRequest | None = None) -> ObsidianNoteListResult:
        request = request or ObsidianNoteListRequest()
        summaries = [
            ObsidianNoteSummary(
                relative_path=result.plan.note.relative_path,
                title=result.plan.note.title,
                approved_for_rag=result.plan.approved_for_rag,
                content_hash=compute_content_hash(result.plan.note),
                source_ids=result.plan.source_ids,
            )
            for result in self._notes.values()
            if (
                request.path_prefix is None
                or result.plan.note.relative_path.startswith(request.path_prefix)
            )
            and (not request.approved_for_rag_only or result.plan.approved_for_rag)
        ]
        summaries.sort(key=lambda summary: summary.relative_path)
        return ObsidianNoteListResult(notes=summaries)

    # --- prepare (no side effects) ------------------------------------------

    def prepare_note_write(self, request: ObsidianNoteWriteRequest) -> ObsidianWritePlan:
        write_intent = (
            ObsidianWriteIntent.UPDATE
            if request.note.relative_path in self._notes
            else ObsidianWriteIntent.CREATE
        )
        approval_digest = compute_approval_digest(
            note=request.note,
            attachment_link=request.attachment_link,
            source_ids=request.source_ids,
            privacy_classification=request.privacy_classification,
            approved_for_rag=request.approved_for_rag,
            write_intent=write_intent,
            overwrite=request.overwrite,
        )
        return ObsidianWritePlan(
            note=request.note,
            attachment_link=request.attachment_link,
            source_ids=request.source_ids,
            privacy_classification=request.privacy_classification,
            approved_for_rag=request.approved_for_rag,
            approval_digest=approval_digest,
            write_intent=write_intent,
            overwrite=request.overwrite,
        )

    def prepare_attachment_association(
        self, request: ObsidianAttachmentAssociationRequest
    ) -> ObsidianAttachmentAssociationResult:
        link = ObsidianArtifactLink(
            markdown_note_path=request.markdown_note_path,
            associated_pdf_path=request.associated_pdf_path,
            attachment_reference=request.attachment_reference,
        )
        return ObsidianAttachmentAssociationResult(link=link)

    # --- commit / reject -----------------------------------------------------

    def commit_write(
        self, plan: ObsidianWritePlan, approval: ObsidianWriteApproval | None
    ) -> ObsidianWriteResult:
        if approval is None:
            raise InvalidApprovalError(plan.plan_id, "no approval was provided")
        if approval.plan_id != plan.plan_id:
            raise InvalidApprovalError(plan.plan_id, "approval.plan_id does not match plan_id")

        # Recompute from the plan's live fields — never trust plan.approval_digest
        # itself as authoritative. This is what makes tampering with any
        # approval-relevant field detectable even if plan_id is reused and
        # the Markdown body is unchanged.
        expected_digest = compute_approval_digest(
            note=plan.note,
            attachment_link=plan.attachment_link,
            source_ids=plan.source_ids,
            privacy_classification=plan.privacy_classification,
            approved_for_rag=plan.approved_for_rag,
            write_intent=plan.write_intent,
            overwrite=plan.overwrite,
        )
        if approval.approval_digest != expected_digest:
            raise InvalidApprovalError(
                plan.plan_id,
                "approval.approval_digest does not match the plan's recomputed approval digest",
            )
        if plan.overwrite and not approval.overwrite_confirmed:
            raise OverwriteNotConfirmedError(plan.plan_id)

        existing = self._notes.get(plan.note.relative_path)
        if existing is not None and not plan.overwrite:
            raise NotePathConflictError(plan.note.relative_path)

        result = ObsidianWriteResult(plan=plan, approval_id=approval.approval_id)
        self._notes[plan.note.relative_path] = result
        return result

    def reject_write(
        self,
        plan: ObsidianWritePlan,
        *,
        reason: str,
        rejected_by: str | None = None,
    ) -> ObsidianWriteRejection:
        return ObsidianWriteRejection(plan=plan, reason=reason, rejected_by=rejected_by)
