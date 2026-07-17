from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    ApprovalStatus,
    ObsidianArtifactLink,
    ObsidianWriteIntent,
    PrivacyClassification,
    ReconstructionCandidate,
    SourceType,
    VaultNoteDraft,
)
from personal_lms.domain.obsidian import (
    ObsidianAttachmentAssociationRequest,
    ObsidianNoteListRequest,
    ObsidianNoteReadRequest,
    ObsidianNoteWriteRequest,
    ObsidianWriteApproval,
)
from personal_lms.vault import FakeObsidianVault, ObsidianVault, compute_content_hash
from personal_lms.vault.errors import (
    InvalidApprovalError,
    NotePathConflictError,
    OverwriteNotConfirmedError,
)


def _note(**overrides: object) -> VaultNoteDraft:
    defaults: dict[str, object] = {
        "title": "Routing Concepts",
        "relative_path": "02-Sources/routing-concepts.md",
        "body_markdown": "# Routing Concepts\n\nOriginal content.",
    }
    defaults.update(overrides)
    return VaultNoteDraft.model_validate(defaults)


def _approve(vault: FakeObsidianVault, plan: object, **overrides: object) -> ObsidianWriteApproval:
    defaults: dict[str, object] = {
        "plan_id": plan.plan_id,  # type: ignore[attr-defined]
        "approval_digest": plan.approval_digest,  # type: ignore[attr-defined]
        "approved_by": "alan",
    }
    defaults.update(overrides)
    return ObsidianWriteApproval.model_validate(defaults)


@pytest.fixture
def vault() -> FakeObsidianVault:
    return FakeObsidianVault()


# --- safe path validation ------------------------------------------------


def test_note_draft_construction_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        _note(relative_path="/etc/passwd")


def test_note_draft_construction_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        _note(relative_path="02-Sources/../../etc/passwd")


def test_read_note_rejects_absolute_path_before_touching_the_vault(
    vault: FakeObsidianVault,
) -> None:
    with pytest.raises(ValidationError):
        ObsidianNoteReadRequest(relative_path="/etc/passwd")


# --- prepare performs no side effects ---------------------------------------


def test_prepare_note_write_does_not_touch_the_vault(vault: FakeObsidianVault) -> None:
    request = ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])

    plan = vault.prepare_note_write(request)

    assert plan is not None
    result = vault.read_note(ObsidianNoteReadRequest(relative_path=_note().relative_path))
    assert result.exists is False
    assert vault.list_notes().notes == []


def test_preparing_twice_does_not_accumulate_state(vault: FakeObsidianVault) -> None:
    request = ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])

    vault.prepare_note_write(request)
    vault.prepare_note_write(request)

    assert vault.list_notes().notes == []


# --- approved commit ------------------------------------------------------


def test_approved_commit_persists_the_note(vault: FakeObsidianVault) -> None:
    note = _note()
    plan = vault.prepare_note_write(ObsidianNoteWriteRequest(note=note, source_ids=["src-1"]))
    approval = _approve(vault, plan)

    result = vault.commit_write(plan, approval)

    assert result.plan == plan
    read_back = vault.read_note(ObsidianNoteReadRequest(relative_path=note.relative_path))
    assert read_back.exists is True
    assert read_back.note == note
    assert read_back.content_hash == compute_content_hash(note)


# --- rejected commit --------------------------------------------------------


def test_rejected_write_never_touches_the_vault(vault: FakeObsidianVault) -> None:
    plan = vault.prepare_note_write(ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"]))

    rejection = vault.reject_write(plan, reason="duplicate of an existing note", rejected_by="alan")

    assert rejection.plan == plan
    assert rejection.reason == "duplicate of an existing note"
    read_back = vault.read_note(ObsidianNoteReadRequest(relative_path=_note().relative_path))
    assert read_back.exists is False


# --- missing / wrong / stale approval ---------------------------------------


def test_commit_without_approval_is_impossible(vault: FakeObsidianVault) -> None:
    plan = vault.prepare_note_write(ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"]))

    with pytest.raises(InvalidApprovalError):
        vault.commit_write(plan, None)  # type: ignore[arg-type]


def test_commit_rejects_approval_for_a_different_plan_id(vault: FakeObsidianVault) -> None:
    plan = vault.prepare_note_write(ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"]))
    approval = _approve(vault, plan, plan_id=uuid4())

    with pytest.raises(InvalidApprovalError):
        vault.commit_write(plan, approval)


def test_commit_rejects_approval_digest_mismatch_even_with_matching_plan_id(
    vault: FakeObsidianVault,
) -> None:
    plan = vault.prepare_note_write(ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"]))
    forged_approval = _approve(vault, plan, approval_digest="b" * 64)

    with pytest.raises(InvalidApprovalError):
        vault.commit_write(plan, forged_approval)


def test_modifying_note_content_after_approval_invalidates_it(vault: FakeObsidianVault) -> None:
    """The exact scenario: approve one version of the content, then change
    your mind and re-prepare with different content for the same path —
    the old approval must not authorize the new plan."""
    original_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])
    )
    stale_approval = _approve(vault, original_plan)

    revised_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(
            note=_note(body_markdown="# Routing Concepts\n\nRevised content."),
            source_ids=["src-1"],
        )
    )

    assert revised_plan.plan_id != original_plan.plan_id
    assert revised_plan.approval_digest != original_plan.approval_digest
    with pytest.raises(InvalidApprovalError):
        vault.commit_write(revised_plan, stale_approval)


# --- tampering with individual approval-relevant fields ---------------------
#
# In every case below, the tampered plan reuses the ORIGINAL plan_id and
# its (now-stale) stored approval_digest verbatim — only one field
# changes. commit_write must still reject it, because it recomputes the
# digest from the plan's live fields rather than trusting either the
# plan's own stored approval_digest or the fact that plan_id is unchanged.


def test_commit_rejects_tampered_path(vault: FakeObsidianVault) -> None:
    original_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])
    )
    approval = _approve(vault, original_plan)

    tampered_note = original_plan.note.model_copy(
        update={"relative_path": "02-Sources/different-note.md"}
    )
    tampered_plan = original_plan.model_copy(update={"note": tampered_note})

    assert tampered_plan.plan_id == original_plan.plan_id
    assert tampered_plan.approval_digest == original_plan.approval_digest
    with pytest.raises(InvalidApprovalError):
        vault.commit_write(tampered_plan, approval)


def test_commit_rejects_tampered_attachment_link(vault: FakeObsidianVault) -> None:
    original_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])
    )
    approval = _approve(vault, original_plan)

    malicious_link = ObsidianArtifactLink(
        markdown_note_path=original_plan.note.relative_path,
        associated_pdf_path="02-Sources/attachments/malicious.pdf",
    )
    tampered_plan = original_plan.model_copy(update={"attachment_link": malicious_link})

    assert tampered_plan.plan_id == original_plan.plan_id
    assert tampered_plan.approval_digest == original_plan.approval_digest
    with pytest.raises(InvalidApprovalError):
        vault.commit_write(tampered_plan, approval)


def test_commit_rejects_tampered_source_ids(vault: FakeObsidianVault) -> None:
    original_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])
    )
    approval = _approve(vault, original_plan)

    tampered_plan = original_plan.model_copy(update={"source_ids": ["src-evil"]})

    assert tampered_plan.plan_id == original_plan.plan_id
    assert tampered_plan.approval_digest == original_plan.approval_digest
    with pytest.raises(InvalidApprovalError):
        vault.commit_write(tampered_plan, approval)


def test_commit_rejects_tampered_privacy_classification(vault: FakeObsidianVault) -> None:
    original_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(
            note=_note(),
            source_ids=["src-1"],
            privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        )
    )
    approval = _approve(vault, original_plan)

    tampered_plan = original_plan.model_copy(
        update={"privacy_classification": PrivacyClassification.PUBLIC}
    )

    assert tampered_plan.plan_id == original_plan.plan_id
    assert tampered_plan.approval_digest == original_plan.approval_digest
    with pytest.raises(InvalidApprovalError):
        vault.commit_write(tampered_plan, approval)


def test_commit_rejects_tampered_approved_for_rag(vault: FakeObsidianVault) -> None:
    original_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"], approved_for_rag=False)
    )
    approval = _approve(vault, original_plan)

    tampered_plan = original_plan.model_copy(update={"approved_for_rag": True})

    assert tampered_plan.plan_id == original_plan.plan_id
    assert tampered_plan.approval_digest == original_plan.approval_digest
    with pytest.raises(InvalidApprovalError):
        vault.commit_write(tampered_plan, approval)


def test_commit_rejects_tampered_overwrite_intent(vault: FakeObsidianVault) -> None:
    original_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"], overwrite=False)
    )
    approval = _approve(vault, original_plan, overwrite_confirmed=True)

    tampered_plan = original_plan.model_copy(update={"overwrite": True})

    assert tampered_plan.plan_id == original_plan.plan_id
    assert tampered_plan.approval_digest == original_plan.approval_digest
    with pytest.raises(InvalidApprovalError):
        vault.commit_write(tampered_plan, approval)


# --- overwrite boundary -----------------------------------------------------


def test_commit_to_an_existing_path_without_overwrite_intent_fails(
    vault: FakeObsidianVault,
) -> None:
    first_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])
    )
    vault.commit_write(first_plan, _approve(vault, first_plan))

    second_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(
            note=_note(body_markdown="# Routing Concepts\n\nUpdated content."),
            source_ids=["src-1"],
        )
    )
    approval = _approve(vault, second_plan)

    with pytest.raises(NotePathConflictError):
        vault.commit_write(second_plan, approval)


def test_overwrite_without_confirmed_approval_fails(vault: FakeObsidianVault) -> None:
    first_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])
    )
    vault.commit_write(first_plan, _approve(vault, first_plan))

    second_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(
            note=_note(body_markdown="# Routing Concepts\n\nUpdated content."),
            source_ids=["src-1"],
            overwrite=True,
        )
    )
    unconfirmed_approval = _approve(vault, second_plan, overwrite_confirmed=False)

    with pytest.raises(OverwriteNotConfirmedError):
        vault.commit_write(second_plan, unconfirmed_approval)


def test_overwrite_with_confirmed_approval_replaces_content(vault: FakeObsidianVault) -> None:
    first_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])
    )
    vault.commit_write(first_plan, _approve(vault, first_plan))

    second_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(
            note=_note(body_markdown="# Routing Concepts\n\nUpdated content."),
            source_ids=["src-1"],
            overwrite=True,
        )
    )
    confirmed_approval = _approve(vault, second_plan, overwrite_confirmed=True)

    result = vault.commit_write(second_plan, confirmed_approval)

    assert result.plan.note.body_markdown == "# Routing Concepts\n\nUpdated content."
    read_back = vault.read_note(ObsidianNoteReadRequest(relative_path=_note().relative_path))
    assert read_back.note is not None
    assert read_back.note.body_markdown == "# Routing Concepts\n\nUpdated content."


def test_second_prepared_plan_for_an_existing_path_reports_update_intent(
    vault: FakeObsidianVault,
) -> None:
    first_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])
    )
    vault.commit_write(first_plan, _approve(vault, first_plan))

    second_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"], overwrite=True)
    )

    assert first_plan.write_intent == ObsidianWriteIntent.CREATE
    assert second_plan.write_intent == ObsidianWriteIntent.UPDATE


# --- Markdown plus associated PDF link --------------------------------------


def test_attachment_association_and_write_plan_carry_the_pdf_link(
    vault: FakeObsidianVault,
) -> None:
    association = vault.prepare_attachment_association(
        ObsidianAttachmentAssociationRequest(
            markdown_note_path=_note().relative_path,
            associated_pdf_path="02-Sources/attachments/routing-concepts.pdf",
            attachment_reference="![[routing-concepts.pdf]]",
        )
    )
    plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(
            note=_note(), source_ids=["src-1"], attachment_link=association.link
        )
    )
    approval = _approve(vault, plan)

    result = vault.commit_write(plan, approval)

    assert result.plan.attachment_link is not None
    assert (
        result.plan.attachment_link.associated_pdf_path
        == "02-Sources/attachments/routing-concepts.pdf"
    )
    assert result.plan.attachment_link.attachment_reference == "![[routing-concepts.pdf]]"


def test_attachment_association_alone_performs_no_write(vault: FakeObsidianVault) -> None:
    vault.prepare_attachment_association(
        ObsidianAttachmentAssociationRequest(markdown_note_path=_note().relative_path)
    )

    assert vault.list_notes().notes == []


# --- provenance preservation -------------------------------------------------


def test_committed_write_preserves_all_provenance_source_ids(vault: FakeObsidianVault) -> None:
    source_ids = ["src-original-1", "src-original-2", "src-reconstructed-pdf"]
    plan = vault.prepare_note_write(ObsidianNoteWriteRequest(note=_note(), source_ids=source_ids))

    result = vault.commit_write(plan, _approve(vault, plan))

    assert result.plan.source_ids == source_ids
    summary = vault.list_notes().notes[0]
    assert summary.source_ids == source_ids


# --- RAG approval separation -------------------------------------------------


def test_reconstruction_approval_does_not_authorize_a_write(vault: FakeObsidianVault) -> None:
    """A separately-approved ReconstructionCandidate carries no authority
    here — commit_write always requires its own ObsidianWriteApproval."""
    candidate = ReconstructionCandidate(
        ordered_source_ids=["src-1", "src-2"],
        proposed_document_type=SourceType.EBOOK,
        confidence=0.9,
        grouping_rationale="matching page numbering",
        approval_status=ApprovalStatus.APPROVED,
    )
    plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=candidate.ordered_source_ids)
    )

    with pytest.raises(InvalidApprovalError):
        vault.commit_write(plan, None)  # type: ignore[arg-type]


def test_write_approval_does_not_imply_trusted_for_rag(vault: FakeObsidianVault) -> None:
    plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"])
    )  # approved_for_rag defaults False

    result = vault.commit_write(plan, _approve(vault, plan))

    assert plan.approved_for_rag is False
    assert result.plan.approved_for_rag is False


def test_approved_for_rag_must_be_explicitly_requested(vault: FakeObsidianVault) -> None:
    plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"], approved_for_rag=True)
    )

    result = vault.commit_write(plan, _approve(vault, plan))

    assert result.plan.approved_for_rag is True


# --- restricted-local-only handling ------------------------------------------


def test_restricted_local_only_classification_survives_prepare_and_commit(
    vault: FakeObsidianVault,
) -> None:
    plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(
            note=_note(),
            source_ids=["src-1"],
            privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        )
    )

    result = vault.commit_write(plan, _approve(vault, plan))

    assert plan.privacy_classification == PrivacyClassification.RESTRICTED_LOCAL_ONLY
    assert result.plan.privacy_classification == PrivacyClassification.RESTRICTED_LOCAL_ONLY


def test_vault_protocol_has_no_hosted_export_capability() -> None:
    """This module has no method that could plausibly send content to a
    hosted service — the actual hosted/local routing boundary is enforced
    elsewhere (DeterministicRouter). Guards against a future method
    silently reintroducing that capability here without review."""
    method_names = {name for name in dir(ObsidianVault) if not name.startswith("_")}
    expected = {
        "read_note",
        "list_notes",
        "prepare_note_write",
        "prepare_attachment_association",
        "commit_write",
        "reject_write",
        "close",
    }
    assert method_names == expected


# --- default isolation between vault instances ------------------------------


def test_two_vault_instances_do_not_share_state() -> None:
    first = FakeObsidianVault()
    second = FakeObsidianVault()
    plan = first.prepare_note_write(ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"]))
    first.commit_write(plan, _approve(first, plan))

    assert first.list_notes().notes != []
    assert second.list_notes().notes == []


def test_close_clears_committed_state(vault: FakeObsidianVault) -> None:
    plan = vault.prepare_note_write(ObsidianNoteWriteRequest(note=_note(), source_ids=["src-1"]))
    vault.commit_write(plan, _approve(vault, plan))
    assert vault.list_notes().notes != []

    vault.close()

    assert vault.list_notes().notes == []


# --- list_notes filtering ----------------------------------------------------


def test_list_notes_filters_by_path_prefix_and_rag_approval(vault: FakeObsidianVault) -> None:
    ccna_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(
            note=_note(relative_path="02-Sources/ccna/note.md"),
            source_ids=["src-1"],
            approved_for_rag=True,
        )
    )
    vault.commit_write(ccna_plan, _approve(vault, ccna_plan))

    other_plan = vault.prepare_note_write(
        ObsidianNoteWriteRequest(
            note=_note(relative_path="02-Sources/other/note.md"),
            source_ids=["src-2"],
            approved_for_rag=False,
        )
    )
    vault.commit_write(other_plan, _approve(vault, other_plan))

    ccna_only = vault.list_notes(ObsidianNoteListRequest(path_prefix="02-Sources/ccna/"))
    rag_only = vault.list_notes(ObsidianNoteListRequest(approved_for_rag_only=True))

    assert [n.relative_path for n in ccna_only.notes] == ["02-Sources/ccna/note.md"]
    assert [n.relative_path for n in rag_only.notes] == ["02-Sources/ccna/note.md"]
