"""Redacted, domain-neutral source readiness gate for the Build Week slice."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid5

from pydantic import Field, field_validator

from personal_lms.domain.base import StrictModel
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import (
    SourceApprovalStatus,
    SourceAuthorityLevel,
    SourceInventoryProcessingStatus,
    SourceInventoryRecord,
    SourceLocatorKind,
    SourceMediaType,
    SourceRightsStatus,
    SourceVersion,
)

_NAMESPACE = UUID("a2d5a3d1-cc06-43f0-a1b1-9d1eab8b1f10")
_PRIVATE_PATH = re.compile(r"(^|[/\\])(?:home|users|private|mnt|var)[/\\]", re.I)
_SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]")


class SourceReadinessStatus(StrEnum):
    AVAILABLE = "available"
    EXCLUDED = "excluded"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"


class SourceSafetyFlag(StrEnum):
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    CLOUD_PLACEHOLDER = "CLOUD_PLACEHOLDER"
    AVAILABILITY_UNKNOWN = "AVAILABILITY_UNKNOWN"
    ARCHIVE_CONTAINER = "ARCHIVE_CONTAINER"
    HISTORICAL_EXPORT = "HISTORICAL_EXPORT"
    BACKUP_COPY = "BACKUP_COPY"
    UNAPPROVED_SOURCE = "UNAPPROVED_SOURCE"
    RIGHTS_REVIEW_REQUIRED = "RIGHTS_REVIEW_REQUIRED"
    PRIVACY_RESTRICTED = "PRIVACY_RESTRICTED"
    SOURCE_VERSION_CONFLICT = "SOURCE_VERSION_CONFLICT"


class SourceDuplicateIndicator(StrictModel):
    group_id: str = Field(min_length=1)
    match_type: str = Field(min_length=1)
    is_preferred: bool = False


class SourceReadinessEntry(StrictModel):
    entry_id: str = Field(min_length=1)
    source_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    media_type: SourceMediaType
    size_bytes: int | None = Field(default=None, ge=0)
    content_identity: str = Field(min_length=1)
    duplicate_group_id: str | None = None
    availability_status: SourceReadinessStatus
    safety_flags: tuple[SourceSafetyFlag, ...] = ()
    duplicate_indicator: SourceDuplicateIndicator | None = None
    approval_status: SourceApprovalStatus = SourceApprovalStatus.UNREVIEWED
    authority_level: SourceAuthorityLevel = SourceAuthorityLevel.UNKNOWN
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    rights_status: SourceRightsStatus = SourceRightsStatus.UNKNOWN
    candidate_locator: str = Field(min_length=1)

    @field_validator("candidate_locator", "source_label", "display_name", "content_identity")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        if _PRIVATE_PATH.search(value) or _SECRET.search(value):
            raise ValueError("private paths and credentials are prohibited")
        return value.strip()


class SourceReadinessManifest(StrictModel):
    manifest_version: str = Field(min_length=1)
    manifest_id: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    entries: tuple[SourceReadinessEntry, ...] = ()


class SourceReadinessImport(StrictModel):
    manifest_id: str
    imported_entry_ids: tuple[str, ...]
    skipped_entry_ids: tuple[str, ...]
    approved_count: int = Field(ge=0)


class InventoryWriter(Protocol):
    def add_source(self, source: SourceInventoryRecord) -> SourceInventoryRecord: ...
    def find_by_locator(
        self, kind: SourceLocatorKind, locator: str
    ) -> SourceInventoryRecord | None: ...
    def add_version(self, version: SourceVersion) -> SourceVersion: ...


def _source_id(manifest_id: str, entry_id: str) -> UUID:
    return uuid5(_NAMESPACE, f"{manifest_id}:{entry_id}")


class SourceReadinessImporter:
    """Validate and import only metadata; never opens candidate locators."""

    def __init__(self, inventory: InventoryWriter) -> None:
        self._inventory = inventory

    def import_manifest(
        self, manifest: SourceReadinessManifest, *, now: datetime
    ) -> SourceReadinessImport:
        imported: list[str] = []
        skipped: list[str] = []
        approved = 0
        for entry in manifest.entries:
            if self._inventory.find_by_locator(SourceLocatorKind.OTHER, entry.candidate_locator):
                skipped.append(entry.entry_id)
                continue
            source_id = _source_id(manifest.manifest_id, entry.entry_id)
            source = SourceInventoryRecord(
                source_id=source_id,
                locator_kind=SourceLocatorKind.OTHER,
                locator=entry.candidate_locator,
                media_type=entry.media_type,
                title=entry.display_name,
                size_bytes=entry.size_bytes,
                content_hash_sha256=None,
                processing_status=SourceInventoryProcessingStatus.CATALOGED,
                approval_status=entry.approval_status,
                rights_status=entry.rights_status,
                authority_level=entry.authority_level,
                privacy_classification=entry.privacy_classification,
                created_at=now,
                updated_at=now,
            )
            self._inventory.add_source(source)
            if entry.content_identity.startswith("synthetic:"):
                version = SourceVersion(
                    source_id=source_id,
                    content_hash_sha256=("0" * 63) + "1",
                    size_bytes=entry.size_bytes,
                    observed_at=now,
                    metadata_json={"content_identity": entry.content_identity},
                )
                self._inventory.add_version(version)
            imported.append(entry.entry_id)
            approved += int(entry.approval_status is SourceApprovalStatus.APPROVED)
        return SourceReadinessImport(
            manifest_id=manifest.manifest_id,
            imported_entry_ids=tuple(imported),
            skipped_entry_ids=tuple(skipped),
            approved_count=approved,
        )


__all__ = [
    "SourceDuplicateIndicator",
    "SourceReadinessEntry",
    "SourceReadinessImport",
    "SourceReadinessImporter",
    "SourceReadinessManifest",
    "SourceReadinessStatus",
    "SourceSafetyFlag",
]
