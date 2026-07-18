from __future__ import annotations

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    ContentChunk,
    CorpusDocument,
    KnowledgeScope,
    PrivacyClassification,
    ProvenanceMetadata,
    SourceProcessingStatus,
)

_VALID_SHA256 = "a" * 64


def _document(**overrides: object) -> CorpusDocument:
    defaults: dict[str, object] = {
        "document_id": "doc-1",
        "source_id": "src-1",
        "title": "Routing Concepts Module 14",
        "content_hash": _VALID_SHA256,
    }
    defaults.update(overrides)
    return CorpusDocument.model_validate(defaults)


def _chunk(**overrides: object) -> ContentChunk:
    defaults: dict[str, object] = {
        "chunk_id": "chunk-1",
        "document_id": "doc-1",
        "source_id": "src-1",
        "ordinal": 0,
        "text": "The DR is elected by priority, then router ID.",
        "text_hash": _VALID_SHA256,
    }
    defaults.update(overrides)
    return ContentChunk.model_validate(defaults)


# --- CorpusDocument -----------------------------------------------------


def test_document_domain_neutral_minimal_construction() -> None:
    document = _document()
    assert document.knowledge_scopes == []
    assert document.status == SourceProcessingStatus.RAW
    assert document.privacy_classification == PrivacyClassification.INTERNAL
    assert document.language is None
    assert document.version is None


def test_document_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _document(vendor="anthropic")


def test_document_rejects_empty_title() -> None:
    with pytest.raises(ValidationError):
        _document(title="")


def test_document_rejects_malformed_content_hash() -> None:
    with pytest.raises(ValidationError):
        _document(content_hash="short")


def test_document_rejects_uppercase_content_hash() -> None:
    with pytest.raises(ValidationError):
        _document(content_hash="A" * 64)


def test_document_accepts_language_and_version() -> None:
    document = _document(language="en", version="2nd edition")
    assert document.language == "en"
    assert document.version == "2nd edition"


def test_document_provenance_defaults_when_omitted() -> None:
    document = _document()
    assert isinstance(document.provenance, ProvenanceMetadata)


def test_document_knowledge_scopes_default_is_isolated_between_instances() -> None:
    first = _document(document_id="doc-1")
    second = _document(document_id="doc-2")
    first.knowledge_scopes.append(KnowledgeScope(certification="CCNA"))
    assert second.knowledge_scopes == []


def test_document_json_round_trip() -> None:
    document = _document(
        language="en",
        knowledge_scopes=[KnowledgeScope(certification="CCNA")],
        provenance=ProvenanceMetadata(imported_by="alan"),
    )
    restored = CorpusDocument.model_validate_json(document.model_dump_json())
    assert restored == document


# --- ContentChunk: basic construction and required invariants ----------


def test_chunk_domain_neutral_minimal_construction() -> None:
    chunk = _chunk()
    assert chunk.knowledge_scopes == []
    assert chunk.status == SourceProcessingStatus.RAW
    assert chunk.trusted_for_rag is False
    assert chunk.page_number is None
    assert chunk.section_title is None
    assert chunk.timestamp_start_seconds is None
    assert chunk.timestamp_end_seconds is None


def test_chunk_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _chunk(reviewer="alan")


def test_chunk_rejects_negative_ordinal() -> None:
    with pytest.raises(ValidationError):
        _chunk(ordinal=-1)


def test_chunk_accepts_zero_ordinal() -> None:
    chunk = _chunk(ordinal=0)
    assert chunk.ordinal == 0


def test_chunk_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        _chunk(text="")


def test_chunk_rejects_whitespace_only_text() -> None:
    with pytest.raises(ValidationError):
        _chunk(text="   ")


def test_chunk_rejects_malformed_text_hash() -> None:
    with pytest.raises(ValidationError):
        _chunk(text_hash="not-a-hash")


def test_chunk_rejects_zero_page_number() -> None:
    with pytest.raises(ValidationError):
        _chunk(page_number=0)


def test_chunk_rejects_negative_page_number() -> None:
    with pytest.raises(ValidationError):
        _chunk(page_number=-1)


def test_chunk_accepts_positive_page_number() -> None:
    chunk = _chunk(page_number=42)
    assert chunk.page_number == 42


# --- timestamp range: both-or-neither, nonnegative, ordered -----------------


def test_chunk_rejects_negative_timestamp_start() -> None:
    with pytest.raises(ValidationError):
        _chunk(timestamp_start_seconds=-1.0, timestamp_end_seconds=10.0)


def test_chunk_rejects_negative_timestamp_end() -> None:
    with pytest.raises(ValidationError):
        _chunk(timestamp_start_seconds=0.0, timestamp_end_seconds=-1.0)


def test_chunk_rejects_start_after_end() -> None:
    with pytest.raises(ValidationError):
        _chunk(timestamp_start_seconds=30.0, timestamp_end_seconds=10.0)


def test_chunk_accepts_start_equal_to_end() -> None:
    chunk = _chunk(timestamp_start_seconds=10.0, timestamp_end_seconds=10.0)
    assert chunk.timestamp_start_seconds == chunk.timestamp_end_seconds


def test_chunk_accepts_start_before_end() -> None:
    chunk = _chunk(timestamp_start_seconds=10.0, timestamp_end_seconds=30.0)
    assert chunk.timestamp_start_seconds == 10.0
    assert chunk.timestamp_end_seconds == 30.0


def test_chunk_accepts_neither_timestamp_set() -> None:
    chunk = _chunk()
    assert chunk.timestamp_start_seconds is None
    assert chunk.timestamp_end_seconds is None


def test_chunk_rejects_only_start_set() -> None:
    with pytest.raises(ValidationError):
        _chunk(timestamp_start_seconds=10.0)


def test_chunk_rejects_only_end_set() -> None:
    with pytest.raises(ValidationError):
        _chunk(timestamp_end_seconds=30.0)


def test_chunk_complete_timestamp_range_round_trips_through_json() -> None:
    chunk = _chunk(timestamp_start_seconds=10.0, timestamp_end_seconds=45.5)
    restored = ContentChunk.model_validate_json(chunk.model_dump_json())
    assert restored.timestamp_start_seconds == 10.0
    assert restored.timestamp_end_seconds == 45.5
    assert restored == chunk


# --- trusted_for_rag requires a reviewed status -----------------------------


def test_chunk_rejects_trusted_for_rag_with_raw_status() -> None:
    with pytest.raises(ValidationError):
        _chunk(status=SourceProcessingStatus.RAW, trusted_for_rag=True)


def test_chunk_rejects_trusted_for_rag_with_candidate_status() -> None:
    with pytest.raises(ValidationError):
        _chunk(status=SourceProcessingStatus.CANDIDATE, trusted_for_rag=True)


@pytest.mark.parametrize(
    "status",
    [
        SourceProcessingStatus.APPROVED,
        SourceProcessingStatus.REVIEWED,
        SourceProcessingStatus.TRUSTED_FOR_RAG,
    ],
)
def test_chunk_accepts_trusted_for_rag_with_reviewed_statuses(
    status: SourceProcessingStatus,
) -> None:
    chunk = _chunk(status=status, trusted_for_rag=True)
    assert chunk.trusted_for_rag is True


def test_chunk_trusted_for_rag_false_is_valid_with_any_status() -> None:
    chunk = _chunk(status=SourceProcessingStatus.RAW, trusted_for_rag=False)
    assert chunk.trusted_for_rag is False


# --- JSON round trip / isolation --------------------------------------------


def test_chunk_knowledge_scopes_default_is_isolated_between_instances() -> None:
    first = _chunk(chunk_id="chunk-1")
    second = _chunk(chunk_id="chunk-2")
    first.knowledge_scopes.append(KnowledgeScope(topic="OSPF"))
    assert second.knowledge_scopes == []


def test_chunk_json_round_trip() -> None:
    chunk = _chunk(
        page_number=12,
        section_title="OSPF DR Election",
        timestamp_start_seconds=10.0,
        timestamp_end_seconds=45.5,
        knowledge_scopes=[KnowledgeScope(certification="CCNA")],
        status=SourceProcessingStatus.APPROVED,
        trusted_for_rag=True,
    )
    restored = ContentChunk.model_validate_json(chunk.model_dump_json())
    assert restored == chunk


def test_different_chunk_ids_with_identical_text_may_coexist() -> None:
    """No uniqueness constraint on text/text_hash — only chunk_id identifies a chunk."""
    first = _chunk(chunk_id="chunk-1", text="duplicate passage")
    second = _chunk(chunk_id="chunk-2", text="duplicate passage")
    assert first.text == second.text
    assert first.chunk_id != second.chunk_id
