from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_lms.domain import (
    PrivacyClassification,
    SourceApprovalStatus,
    SourceInventoryRecord,
    SourceLocation,
    SourceLocatorKind,
    SourceMediaType,
    SourceVersion,
    derive_source_id,
    normalize_locator,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _record(**overrides: object) -> SourceInventoryRecord:
    defaults: dict[str, object] = {
        "source_id": derive_source_id(canonical_locator="https://example.com/a"),
        "locator_kind": SourceLocatorKind.WEB_URL,
        "locator": "https://example.com/a",
        "media_type": SourceMediaType.HTML,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return SourceInventoryRecord.model_validate(defaults)


# --- domain validation --------------------------------------------------------


def test_valid_source_record() -> None:
    record = _record()
    assert record.approval_status is SourceApprovalStatus.UNREVIEWED
    assert record.canonical_locator == "https://example.com/a"


def test_empty_locator_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(locator="")


def test_malformed_locator_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(
            locator_kind=SourceLocatorKind.WEB_URL,
            locator="not a url at all but also no scheme",
        )


def test_url_credentials_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(locator="https://user:pass@example.com/a")


def test_unsupported_url_scheme_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(locator="ftp://example.com/a")


def test_negative_size_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(size_bytes=-1)


def test_malformed_sha256_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(content_hash_sha256="not-a-sha256")
    with pytest.raises(ValidationError):
        _record(content_hash_sha256="A" * 64)  # uppercase rejected


def test_metadata_values_normalize_deterministically() -> None:
    record = _record(knowledge_domains=("  networking  ", "security"))
    assert record.knowledge_domains == ("networking", "security")


def test_duplicate_tags_collapse() -> None:
    record = _record(certifications=("CCNA", "CCNA", "CCNA"))
    assert record.certifications == ("CCNA",)


def test_updated_time_cannot_precede_created_time() -> None:
    with pytest.raises(ValidationError):
        _record(created_at=_NOW, updated_at=_NOW - timedelta(seconds=1))


def test_default_state_is_not_approved() -> None:
    record = _record()
    assert record.approval_status is not SourceApprovalStatus.APPROVED


def test_restricted_local_only_privacy_is_accepted() -> None:
    record = _record(privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY)
    assert record.privacy_classification is PrivacyClassification.RESTRICTED_LOCAL_ONLY


def test_domain_model_stores_no_source_body_field() -> None:
    assert set(SourceInventoryRecord.model_fields) == {
        "source_id",
        "locator_kind",
        "locator",
        "media_type",
        "title",
        "description",
        "mime_type",
        "language",
        "content_hash_sha256",
        "size_bytes",
        "processing_status",
        "approval_status",
        "rights_status",
        "authority_level",
        "privacy_classification",
        "knowledge_domains",
        "certifications",
        "courses",
        "topics",
        "created_at",
        "updated_at",
    }


def test_file_path_locator_does_not_require_the_path_to_exist() -> None:
    record = _record(
        locator_kind=SourceLocatorKind.FILE_PATH, locator="/does/not/exist/on/this/machine.pdf"
    )
    assert record.canonical_locator == "/does/not/exist/on/this/machine.pdf"


def test_file_path_normalization_is_deterministic() -> None:
    a = normalize_locator(SourceLocatorKind.FILE_PATH, "/a/./b/../c/file.pdf")
    b = normalize_locator(SourceLocatorKind.FILE_PATH, "/a/c/file.pdf")
    assert a == b == "/a/c/file.pdf"


def test_locator_error_message_does_not_leak_the_locator_value() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _record(locator="https://user:pass@internal-secret-host.example/private")
    assert "internal-secret-host" not in str(exc_info.value)


# --- SourceVersion --------------------------------------------------------------


def test_source_version_self_supersession_rejected() -> None:
    version_id = uuid4()
    with pytest.raises(ValidationError):
        SourceVersion(
            version_id=version_id,
            source_id=uuid4(),
            content_hash_sha256="a" * 64,
            observed_at=_NOW,
            supersedes_version_id=version_id,
        )


def test_source_version_requires_valid_sha256() -> None:
    with pytest.raises(ValidationError):
        SourceVersion(
            version_id=uuid4(),
            source_id=uuid4(),
            content_hash_sha256="bad-hash",
            observed_at=_NOW,
        )


def test_source_version_metadata_must_be_json_safe() -> None:
    with pytest.raises(ValidationError):
        SourceVersion(
            version_id=uuid4(),
            source_id=uuid4(),
            content_hash_sha256="a" * 64,
            observed_at=_NOW,
            metadata_json={"bad": {1, 2, 3}},  # a set is not JSON-safe
        )


# --- SourceLocation --------------------------------------------------------------


def test_source_location_last_cannot_precede_first() -> None:
    with pytest.raises(ValidationError):
        SourceLocation(
            source_id=uuid4(),
            locator_kind=SourceLocatorKind.WEB_URL,
            locator="https://example.com/a",
            first_observed_at=_NOW,
            last_observed_at=_NOW - timedelta(seconds=1),
        )


# --- identity --------------------------------------------------------------------


def test_existing_uuid_preserved() -> None:
    existing = uuid4()
    assert derive_source_id(existing_id=existing, content_hash_sha256="a" * 64) == existing


def test_same_url_produces_same_uuid() -> None:
    first = derive_source_id(canonical_locator="https://example.com/a")
    second = derive_source_id(canonical_locator="https://example.com/a")
    assert first == second


def test_equivalent_canonical_urls_produce_the_defined_deterministic_result() -> None:
    canonical_a = normalize_locator(SourceLocatorKind.WEB_URL, "https://example.com/a?x=1#frag")
    canonical_b = normalize_locator(SourceLocatorKind.WEB_URL, "https://example.com/a?x=1")
    assert canonical_a == canonical_b
    assert derive_source_id(canonical_locator=canonical_a) == derive_source_id(
        canonical_locator=canonical_b
    )


def test_same_content_hash_produces_same_uuid() -> None:
    first = derive_source_id(content_hash_sha256="a" * 64)
    second = derive_source_id(content_hash_sha256="a" * 64)
    assert first == second


def test_different_content_hashes_produce_different_uuids() -> None:
    first = derive_source_id(content_hash_sha256="a" * 64)
    second = derive_source_id(content_hash_sha256="b" * 64)
    assert first != second


def test_content_hash_identity_takes_precedence_over_locator() -> None:
    by_hash = derive_source_id(content_hash_sha256="a" * 64, canonical_locator="https://x.com/y")
    by_hash_alone = derive_source_id(content_hash_sha256="a" * 64)
    assert by_hash == by_hash_alone


def test_derive_source_id_requires_at_least_one_input() -> None:
    with pytest.raises(ValueError, match="requires one of"):
        derive_source_id()


def test_derive_source_id_uses_no_random_or_clock_source() -> None:
    """Deterministic proof: repeated calls with the same input in the same
    process always agree — a random or clock-derived source would not."""
    results = {derive_source_id(canonical_locator="https://example.com/stable") for _ in range(50)}
    assert len(results) == 1
