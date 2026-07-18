from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    ObsidianArtifactLink,
    ObsidianAttachmentAssociationRequest,
    ObsidianAttachmentAssociationResult,
    ObsidianNoteListRequest,
    ObsidianNoteReadRequest,
    ObsidianNoteReadResult,
    ObsidianNoteSummary,
    ObsidianNoteWriteRequest,
    ObsidianWriteApproval,
    ObsidianWriteIntent,
    ObsidianWritePlan,
    ObsidianWriteRejection,
    ObsidianWriteResult,
    PrivacyClassification,
    VaultNoteDraft,
)

_VALID_SHA256 = "a" * 64


def _note(**overrides: object) -> VaultNoteDraft:
    defaults: dict[str, object] = {
        "title": "Routing Concepts",
        "relative_path": "02-Sources/routing-concepts.md",
        "body_markdown": "# Routing Concepts\n\nContent here.",
    }
    defaults.update(overrides)
    return VaultNoteDraft.model_validate(defaults)


def _plan(**overrides: object) -> ObsidianWritePlan:
    defaults: dict[str, object] = {
        "note": _note(),
        "source_ids": ["src-1"],
        "approval_digest": _VALID_SHA256,
        "write_intent": ObsidianWriteIntent.CREATE,
    }
    defaults.update(overrides)
    return ObsidianWritePlan.model_validate(defaults)


# --- ObsidianNoteReadRequest / path safety ---------------------------------


def test_read_request_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        ObsidianNoteReadRequest(relative_path="/etc/passwd")


def test_read_request_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        ObsidianNoteReadRequest(relative_path="02-Sources/../../etc/passwd")


def test_read_request_accepts_relative_path() -> None:
    request = ObsidianNoteReadRequest(relative_path="02-Sources/note.md")
    assert request.relative_path == "02-Sources/note.md"


# --- ObsidianNoteReadResult -------------------------------------------------


def test_read_result_requires_note_when_exists_true() -> None:
    with pytest.raises(ValidationError):
        ObsidianNoteReadResult(relative_path="02-Sources/note.md", exists=True)


def test_read_result_rejects_note_when_exists_false() -> None:
    with pytest.raises(ValidationError):
        ObsidianNoteReadResult(relative_path="02-Sources/note.md", exists=False, note=_note())


def test_read_result_valid_when_absent() -> None:
    result = ObsidianNoteReadResult(relative_path="02-Sources/note.md", exists=False)
    assert result.note is None
    assert result.content_hash is None


def test_read_result_valid_when_present() -> None:
    result = ObsidianNoteReadResult(
        relative_path="02-Sources/note.md",
        exists=True,
        note=_note(),
        content_hash=_VALID_SHA256,
    )
    assert result.note is not None


# --- ObsidianNoteSummary / ObsidianNoteListRequest --------------------------


def test_note_summary_rejects_malformed_content_hash() -> None:
    with pytest.raises(ValidationError):
        ObsidianNoteSummary(
            relative_path="02-Sources/note.md",
            title="Note",
            approved_for_rag=False,
            content_hash="not-a-hash",
            source_ids=["src-1"],
        )


def test_note_summary_requires_at_least_one_source_id() -> None:
    with pytest.raises(ValidationError):
        ObsidianNoteSummary(
            relative_path="02-Sources/note.md",
            title="Note",
            approved_for_rag=False,
            content_hash=_VALID_SHA256,
            source_ids=[],
        )


def test_list_request_rejects_absolute_path_prefix() -> None:
    with pytest.raises(ValidationError):
        ObsidianNoteListRequest(path_prefix="/etc")


def test_list_request_defaults_are_permissive() -> None:
    request = ObsidianNoteListRequest()
    assert request.path_prefix is None
    assert request.approved_for_rag_only is False


# --- ObsidianNoteWriteRequest ------------------------------------------------


def test_write_request_requires_at_least_one_source_id() -> None:
    with pytest.raises(ValidationError):
        ObsidianNoteWriteRequest(note=_note(), source_ids=[])


def test_write_request_defaults() -> None:
    request = ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])
    assert request.privacy_classification == PrivacyClassification.INTERNAL
    assert request.approved_for_rag is False
    assert request.overwrite is False
    assert request.attachment_link is None


# --- ObsidianWritePlan: frozen, format validation, provenance ---------------


def test_write_plan_is_frozen() -> None:
    plan = _plan()
    with pytest.raises(ValidationError):
        plan.overwrite = True  # type: ignore[misc]


def test_write_plan_rejects_malformed_approval_digest() -> None:
    with pytest.raises(ValidationError):
        _plan(approval_digest="short")


def test_write_plan_requires_at_least_one_source_id() -> None:
    with pytest.raises(ValidationError):
        _plan(source_ids=[])


def test_write_plan_approved_for_rag_defaults_false() -> None:
    plan = _plan()
    assert plan.approved_for_rag is False


def test_write_plan_records_provenance_source_ids() -> None:
    plan = _plan(source_ids=["src-original-1", "src-original-2", "src-reconstructed-pdf"])
    assert plan.source_ids == ["src-original-1", "src-original-2", "src-reconstructed-pdf"]


def test_write_plan_accepts_attachment_link() -> None:
    link = ObsidianArtifactLink(
        markdown_note_path="02-Sources/note.md",
        associated_pdf_path="02-Sources/attachments/note.pdf",
    )
    plan = _plan(attachment_link=link)
    assert plan.attachment_link is not None
    assert plan.attachment_link.associated_pdf_path == "02-Sources/attachments/note.pdf"


def test_write_plan_json_round_trip() -> None:
    plan = _plan(
        attachment_link=ObsidianArtifactLink(markdown_note_path="02-Sources/note.md"),
        approved_for_rag=True,
        privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
    )
    restored = ObsidianWritePlan.model_validate_json(plan.model_dump_json())
    assert restored == plan


# --- ObsidianWriteApproval: frozen, format validation -----------------------


def test_write_approval_is_frozen() -> None:
    approval = ObsidianWriteApproval(
        plan_id=uuid4(), approval_digest=_VALID_SHA256, approved_by="alan"
    )
    with pytest.raises(ValidationError):
        approval.overwrite_confirmed = True  # type: ignore[misc]


def test_write_approval_rejects_malformed_approval_digest() -> None:
    with pytest.raises(ValidationError):
        ObsidianWriteApproval(plan_id=uuid4(), approval_digest="short", approved_by="alan")


def test_write_approval_requires_non_empty_approved_by() -> None:
    with pytest.raises(ValidationError):
        ObsidianWriteApproval(plan_id=uuid4(), approval_digest=_VALID_SHA256, approved_by="")


def test_write_approval_overwrite_confirmed_defaults_false() -> None:
    approval = ObsidianWriteApproval(
        plan_id=uuid4(), approval_digest=_VALID_SHA256, approved_by="alan"
    )
    assert approval.overwrite_confirmed is False


def test_write_approval_json_round_trip() -> None:
    approval = ObsidianWriteApproval(
        plan_id=uuid4(),
        approval_digest=_VALID_SHA256,
        approved_by="alan",
        overwrite_confirmed=True,
    )
    restored = ObsidianWriteApproval.model_validate_json(approval.model_dump_json())
    assert restored == approval


# --- ObsidianWriteResult / ObsidianWriteRejection ---------------------------


def test_write_result_embeds_the_full_plan() -> None:
    plan = _plan()
    result = ObsidianWriteResult(plan=plan, approval_id=uuid4())
    assert result.plan == plan
    assert result.plan.approval_digest == plan.approval_digest


def test_write_result_json_round_trip() -> None:
    result = ObsidianWriteResult(plan=_plan(), approval_id=uuid4())
    restored = ObsidianWriteResult.model_validate_json(result.model_dump_json())
    assert restored == result


def test_write_rejection_requires_reason() -> None:
    with pytest.raises(ValidationError):
        ObsidianWriteRejection(plan=_plan(), reason="")


def test_write_rejection_json_round_trip() -> None:
    rejection = ObsidianWriteRejection(plan=_plan(), reason="duplicate content", rejected_by="alan")
    restored = ObsidianWriteRejection.model_validate_json(rejection.model_dump_json())
    assert restored == rejection


# --- ObsidianAttachmentAssociationRequest / Result --------------------------


def test_attachment_association_result_wraps_artifact_link() -> None:
    result = ObsidianAttachmentAssociationResult(
        link=ObsidianArtifactLink(markdown_note_path="02-Sources/note.md")
    )
    assert result.link.markdown_note_path == "02-Sources/note.md"


def test_attachment_association_request_defaults() -> None:
    request = ObsidianAttachmentAssociationRequest(markdown_note_path="02-Sources/note.md")
    assert request.associated_pdf_path is None
    assert request.attachment_reference is None
