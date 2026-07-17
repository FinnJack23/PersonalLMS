from __future__ import annotations

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    KnowledgeScope,
    PrivacyClassification,
    ProvenanceMetadata,
    SourceAssetRelationship,
    SourceProcessingStatus,
    SourceRecord,
    SourceRelationshipType,
    SourceType,
)

_VALID_SHA256 = "a" * 64


def _source_record(**overrides: object) -> SourceRecord:
    defaults: dict[str, object] = {
        "source_id": "src-00000001",
        "source_type": SourceType.IMAGE,
        "original_location": "archive/screenshots/img001.png",
        "filename": "img001.png",
        "mime_type": "image/png",
        "sha256_hash": _VALID_SHA256,
        "byte_size": 204_800,
    }
    defaults.update(overrides)
    return SourceRecord.model_validate(defaults)


# --- SourceRecord: basic construction and domain neutrality ------------------


def test_source_record_domain_neutral_minimal_construction() -> None:
    record = _source_record()
    assert record.knowledge_scopes == []
    assert record.status == SourceProcessingStatus.RAW
    assert record.is_generated_artifact is False
    assert record.privacy_classification == PrivacyClassification.INTERNAL


def test_source_record_accepts_optional_knowledge_scopes() -> None:
    record = _source_record(
        knowledge_scopes=[KnowledgeScope(certification="CCNA"), KnowledgeScope(course="D419")]
    )
    assert len(record.knowledge_scopes) == 2


def test_source_record_provenance_defaults_when_omitted() -> None:
    record = _source_record()
    assert isinstance(record.provenance, ProvenanceMetadata)
    assert record.provenance.imported_by is None


def test_source_record_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _source_record(vendor="anthropic")


def test_source_record_rejects_empty_source_id() -> None:
    with pytest.raises(ValidationError):
        _source_record(source_id="")


def test_source_record_rejects_negative_byte_size() -> None:
    with pytest.raises(ValidationError):
        _source_record(byte_size=-1)


# --- SHA-256 validation ------------------------------------------------


def test_source_record_rejects_short_hash() -> None:
    with pytest.raises(ValidationError):
        _source_record(sha256_hash="abc123")


def test_source_record_rejects_uppercase_hash() -> None:
    with pytest.raises(ValidationError):
        _source_record(sha256_hash="A" * 64)


def test_source_record_rejects_non_hex_characters() -> None:
    with pytest.raises(ValidationError):
        _source_record(sha256_hash="g" * 64)


def test_source_record_accepts_valid_lowercase_hex_hash() -> None:
    record = _source_record(sha256_hash="0123456789abcdef" * 4)
    assert record.sha256_hash == "0123456789abcdef" * 4


# --- original assets cannot be marked as generated artifacts ----------------


def test_raw_source_cannot_be_marked_generated_artifact() -> None:
    with pytest.raises(ValidationError):
        _source_record(status=SourceProcessingStatus.RAW, is_generated_artifact=True)


def test_non_raw_source_can_be_marked_generated_artifact() -> None:
    record = _source_record(status=SourceProcessingStatus.RECONSTRUCTED, is_generated_artifact=True)
    assert record.is_generated_artifact is True


def test_raw_source_with_generated_artifact_false_is_valid() -> None:
    record = _source_record(status=SourceProcessingStatus.RAW, is_generated_artifact=False)
    assert record.is_generated_artifact is False


def test_source_record_json_round_trip() -> None:
    record = _source_record(knowledge_scopes=[KnowledgeScope(certification="CCNA")])
    restored = SourceRecord.model_validate_json(record.model_dump_json())
    assert restored == record


def test_source_record_knowledge_scopes_default_is_isolated_between_instances() -> None:
    first = _source_record(source_id="src-1")
    second = _source_record(source_id="src-2")
    first.knowledge_scopes.append(KnowledgeScope(certification="CCNA"))
    assert second.knowledge_scopes == []


# --- SourceAssetRelationship --------------------------------------------


def test_relationship_valid_construction() -> None:
    relationship = SourceAssetRelationship(
        source_id="src-pdf-reconstructed",
        related_source_id="src-page-001",
        relationship_type=SourceRelationshipType.RECONSTRUCTED_FROM,
    )
    assert relationship.relationship_type == SourceRelationshipType.RECONSTRUCTED_FROM


def test_relationship_rejects_self_relationship() -> None:
    with pytest.raises(ValidationError):
        SourceAssetRelationship(
            source_id="src-1",
            related_source_id="src-1",
            relationship_type=SourceRelationshipType.DUPLICATE_OF,
        )


def test_relationship_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SourceAssetRelationship(
            source_id="src-1",
            related_source_id="src-2",
            relationship_type=SourceRelationshipType.DUPLICATE_OF,
            confidence=0.9,  # type: ignore[call-arg]
        )


def test_relationship_json_round_trip() -> None:
    relationship = SourceAssetRelationship(
        source_id="src-1",
        related_source_id="src-2",
        relationship_type=SourceRelationshipType.SUPERSEDES,
        note="newer edition",
    )
    restored = SourceAssetRelationship.model_validate_json(relationship.model_dump_json())
    assert restored == relationship


# --- ProvenanceMetadata --------------------------------------------------


def test_provenance_metadata_all_optional_except_imported_at() -> None:
    provenance = ProvenanceMetadata()
    assert provenance.imported_by is None
    assert provenance.acquisition_note is None
    assert provenance.imported_at.tzinfo is not None


def test_provenance_metadata_rejects_empty_imported_by() -> None:
    with pytest.raises(ValidationError):
        ProvenanceMetadata(imported_by="")
