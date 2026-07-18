from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    ApprovalStatus,
    KnowledgeScope,
    ObsidianArtifactLink,
    ReconstructedDocument,
    ReconstructionCandidate,
    ReconstructionManifest,
    SearchableTextStatus,
    SourceType,
)


def _candidate(**overrides: object) -> ReconstructionCandidate:
    defaults: dict[str, object] = {
        "ordered_source_ids": ["src-1", "src-2", "src-3"],
        "proposed_document_type": SourceType.EBOOK,
        "confidence": 0.8,
        "grouping_rationale": "consistent chapter header and page numbering across images",
    }
    defaults.update(overrides)
    return ReconstructionCandidate.model_validate(defaults)


def _document(**overrides: object) -> ReconstructedDocument:
    defaults: dict[str, object] = {
        "candidate_id": uuid4(),
        "source_image_ids": ["src-1", "src-2", "src-3"],
    }
    defaults.update(overrides)
    return ReconstructedDocument.model_validate(defaults)


# --- ReconstructionCandidate ----------------------------------------------


def test_candidate_domain_neutral_minimal_construction() -> None:
    candidate = _candidate()
    assert candidate.approval_status == ApprovalStatus.PENDING
    assert candidate.knowledge_scope is None
    assert candidate.warnings == []
    assert candidate.page_order_rationale is None


def test_candidate_rejects_empty_ordered_source_ids() -> None:
    with pytest.raises(ValidationError):
        _candidate(ordered_source_ids=[])


def test_candidate_rejects_duplicate_ordered_source_ids() -> None:
    """Duplicate-page rejection: the same image cannot be listed as two pages."""
    with pytest.raises(ValidationError):
        _candidate(ordered_source_ids=["src-1", "src-2", "src-1"])


def test_candidate_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        _candidate(confidence=1.01)


def test_candidate_rejects_empty_grouping_rationale() -> None:
    with pytest.raises(ValidationError):
        _candidate(grouping_rationale="")


def test_candidate_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _candidate(reviewer="alan")  # type: ignore[call-arg]


def test_candidate_records_warnings_and_scope() -> None:
    candidate = _candidate(
        warnings=["page 7 may be missing"],
        knowledge_scope=KnowledgeScope(certification="CCNA"),
    )
    assert candidate.warnings == ["page 7 may be missing"]
    assert candidate.knowledge_scope is not None
    assert candidate.knowledge_scope.certification == "CCNA"


def test_candidate_json_round_trip() -> None:
    candidate = _candidate(proposed_title="Routing Concepts Ebook")
    restored = ReconstructionCandidate.model_validate_json(candidate.model_dump_json())
    assert restored == candidate


# --- ReconstructionManifest ------------------------------------------------


def test_manifest_defaults_to_empty_candidates_and_requires_human_review() -> None:
    manifest = ReconstructionManifest()
    assert manifest.candidates == []
    assert manifest.unresolved_source_ids == []
    assert manifest.requires_human_review is True


def test_manifest_rejects_requires_human_review_false() -> None:
    with pytest.raises(ValidationError):
        ReconstructionManifest(requires_human_review=False)


def test_manifest_unresolved_ids_are_never_silently_dropped() -> None:
    manifest = ReconstructionManifest(unresolved_source_ids=["src-021", "src-022"])
    assert manifest.unresolved_source_ids == ["src-021", "src-022"]


def test_manifest_records_duplicate_source_ids() -> None:
    manifest = ReconstructionManifest(duplicate_source_ids=["src-014"])
    assert manifest.duplicate_source_ids == ["src-014"]


def test_manifest_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ReconstructionManifest(auto_approved=True)  # type: ignore[call-arg]


def test_manifest_json_round_trip() -> None:
    manifest = ReconstructionManifest(
        batch_label="2026-07-20 screenshot import",
        candidates=[_candidate()],
        unresolved_source_ids=["src-099"],
    )
    restored = ReconstructionManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest


def test_manifest_candidates_default_is_isolated_between_instances() -> None:
    first = ReconstructionManifest()
    second = ReconstructionManifest()
    first.candidates.append(_candidate())
    assert second.candidates == []


# --- 30-image acceptance scenario ------------------------------------------


def test_thirty_image_batch_accounts_for_every_source_id_with_none_dropped() -> None:
    """30 screenshots enter the batch: 12 form an ebook, 4 form an
    infographic set, 4 are PDF pages, and the remaining 10 stay
    unresolved. Every id must be represented exactly once across the
    candidates and the unresolved list — never silently dropped."""
    ebook_pages = [f"src-{i:03d}" for i in range(1, 13)]  # 12 pages
    infographic_pages = [f"src-{i:03d}" for i in range(13, 17)]  # 4 images
    pdf_pages = [f"src-{i:03d}" for i in range(17, 21)]  # 4 pages
    unresolved = [f"src-{i:03d}" for i in range(21, 31)]  # 10 remaining
    all_ids = ebook_pages + infographic_pages + pdf_pages + unresolved
    assert len(all_ids) == 30

    manifest = ReconstructionManifest(
        batch_label="2026-07-20 screenshot import",
        candidates=[
            _candidate(
                ordered_source_ids=ebook_pages,
                proposed_document_type=SourceType.EBOOK,
                proposed_title="Routing Concepts Ebook",
                grouping_rationale="consistent chapter header and page numbering",
                page_order_rationale="page numbers visible in each screenshot's footer",
            ),
            _candidate(
                ordered_source_ids=infographic_pages,
                proposed_document_type=SourceType.IMAGE,
                proposed_title="OSI Model Infographic Set",
                grouping_rationale="shared visual theme and matching color palette",
                confidence=0.65,
            ),
            _candidate(
                ordered_source_ids=pdf_pages,
                proposed_document_type=SourceType.PDF,
                proposed_title="Subnetting Cheat Sheet",
                grouping_rationale="identical PDF viewer chrome and page-count indicator",
                page_order_rationale="page x of y indicator visible in each screenshot",
            ),
        ],
        unresolved_source_ids=unresolved,
    )

    assert len(manifest.candidates) == 3
    assert [c.proposed_document_type for c in manifest.candidates] == [
        SourceType.EBOOK,
        SourceType.IMAGE,
        SourceType.PDF,
    ]

    grouped_ids = [sid for c in manifest.candidates for sid in c.ordered_source_ids]
    accounted_ids = grouped_ids + manifest.unresolved_source_ids
    assert sorted(accounted_ids) == sorted(all_ids)
    assert len(accounted_ids) == len(set(accounted_ids)) == 30

    # Nothing is pre-approved — every proposed group awaits human review.
    assert all(c.approval_status == ApprovalStatus.PENDING for c in manifest.candidates)
    assert manifest.requires_human_review is True


# --- ReconstructedDocument ----------------------------------------------


def test_document_requires_at_least_one_source_asset() -> None:
    with pytest.raises(ValidationError):
        _document(source_image_ids=[])


def test_document_rejects_duplicate_source_image_ids() -> None:
    with pytest.raises(ValidationError):
        _document(source_image_ids=["src-1", "src-2", "src-1"])


def test_document_trusted_for_rag_defaults_false() -> None:
    document = _document()
    assert document.trusted_for_rag is False


def test_document_defaults_have_no_pdf_reference_and_no_graphics_preserved() -> None:
    document = _document()
    assert document.pdf_reference is None
    assert document.graphics_preserved is False
    assert document.searchable_text_status == SearchableTextStatus.NONE


def test_document_rejects_pdf_reference_without_graphics_preserved() -> None:
    with pytest.raises(ValidationError):
        _document(pdf_reference="src-pdf-001", graphics_preserved=False)


def test_document_accepts_pdf_reference_with_graphics_preserved() -> None:
    document = _document(pdf_reference="src-pdf-001", graphics_preserved=True)
    assert document.pdf_reference == "src-pdf-001"


def test_document_preserves_provenance_to_every_original_image() -> None:
    source_ids = ["src-1", "src-2", "src-3", "src-4"]
    document = _document(source_image_ids=source_ids)
    assert document.source_image_ids == source_ids


def test_document_accepts_obsidian_link() -> None:
    document = _document(
        obsidian_link=ObsidianArtifactLink(
            markdown_note_path="02-Sources/routing-concepts-ebook.md",
            associated_pdf_path="02-Sources/attachments/routing-concepts-ebook.pdf",
        )
    )
    assert document.obsidian_link is not None
    assert document.obsidian_link.associated_pdf_path is not None


def test_document_json_round_trip() -> None:
    document = _document(
        pdf_reference="src-pdf-001",
        graphics_preserved=True,
        searchable_text_status=SearchableTextStatus.COMPLETE,
        obsidian_link=ObsidianArtifactLink(markdown_note_path="02-Sources/note.md"),
    )
    restored = ReconstructedDocument.model_validate_json(document.model_dump_json())
    assert restored == document


# --- reconstruction approval and RAG approval are separate decisions --------


def test_approving_candidate_does_not_imply_trusted_for_rag() -> None:
    candidate = _candidate(approval_status=ApprovalStatus.APPROVED)
    document = _document(candidate_id=candidate.candidate_id)

    assert candidate.approval_status == ApprovalStatus.APPROVED
    assert document.trusted_for_rag is False


def test_trusted_for_rag_can_be_set_independently_of_candidate_approval() -> None:
    """A document may be explicitly marked trusted only after its own,
    separate RAG-approval step — modeled here as a second, independent
    construction rather than any field derived from candidate approval."""
    candidate = _candidate(approval_status=ApprovalStatus.APPROVED)
    trusted_document = _document(candidate_id=candidate.candidate_id, trusted_for_rag=True)

    assert trusted_document.trusted_for_rag is True
    assert candidate.approval_status == ApprovalStatus.APPROVED


# --- ObsidianArtifactLink -------------------------------------------------


def test_obsidian_link_rejects_absolute_markdown_path() -> None:
    with pytest.raises(ValidationError):
        ObsidianArtifactLink(markdown_note_path="/etc/passwd")


def test_obsidian_link_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        ObsidianArtifactLink(markdown_note_path="02-Sources/../../etc/passwd")


def test_obsidian_link_rejects_absolute_pdf_path() -> None:
    with pytest.raises(ValidationError):
        ObsidianArtifactLink(
            markdown_note_path="02-Sources/note.md", associated_pdf_path="/etc/passwd"
        )


def test_obsidian_link_has_no_write_method() -> None:
    link = ObsidianArtifactLink(markdown_note_path="02-Sources/note.md")
    assert not hasattr(link, "write")
    assert not hasattr(link, "save")


def test_obsidian_link_json_round_trip() -> None:
    link = ObsidianArtifactLink(
        markdown_note_path="02-Sources/note.md",
        associated_pdf_path="02-Sources/attachments/note.pdf",
        attachment_reference="![[note.pdf]]",
    )
    restored = ObsidianArtifactLink.model_validate_json(link.model_dump_json())
    assert restored == link
