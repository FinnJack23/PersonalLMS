from __future__ import annotations

from enum import StrEnum


class RoutingOutcome(StrEnum):
    """Possible results of a routing decision. Never a vendor selection."""

    TIER_0_DETERMINISTIC = "tier_0_deterministic"
    TIER_1_LOCAL = "tier_1_local"
    TIER_2_HOSTED = "tier_2_hosted"
    APPROVAL_REQUIRED = "approval_required"
    REJECTED = "rejected"


class LatencyClass(StrEnum):
    INTERACTIVE = "interactive"
    STANDARD = "standard"
    BATCH = "batch"


class CostClass(StrEnum):
    FREE = "free"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RunStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class ApprovalActionType(StrEnum):
    """The kinds of actions that require human approval before execution.

    Mirrors the master plan's approval triggers: destructive, expensive, or
    publishing actions.
    """

    HOSTED_ESCALATION = "hosted_escalation"
    VAULT_WRITE = "vault_write"
    VAULT_PROMOTION = "vault_promotion"
    DESTRUCTIVE_ACTION = "destructive_action"
    LARGE_BATCH_CALL = "large_batch_call"
    PUBLICATION = "publication"
    OTHER = "other"


class SourceType(StrEnum):
    """The physical/media type of a cataloged source.

    Domain-neutral: no certification- or course-specific value. Domain
    scope is always optional metadata via ``KnowledgeScope``, never a
    ``SourceType`` value.
    """

    PDF = "pdf"
    IMAGE = "image"
    EBOOK = "ebook"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    URL = "url"
    ARCHIVE = "archive"
    OTHER = "other"


class SourceProcessingStatus(StrEnum):
    """Lifecycle status of a cataloged source, from raw archive entry
    through trusted-RAG eligibility."""

    RAW = "raw"
    CATALOGED = "cataloged"
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    RECONSTRUCTED = "reconstructed"
    REVIEWED = "reviewed"
    TRUSTED_FOR_RAG = "trusted_for_rag"


class SourceRelationshipType(StrEnum):
    """How two cataloged sources relate to one another."""

    DERIVED_FROM = "derived_from"
    SUPERSEDES = "supersedes"
    DUPLICATE_OF = "duplicate_of"
    ATTACHMENT_OF = "attachment_of"
    RECONSTRUCTED_FROM = "reconstructed_from"


class SearchableTextStatus(StrEnum):
    """Whether a reconstructed document carries OCR/searchable text."""

    NONE = "none"
    PARTIAL = "partial"
    COMPLETE = "complete"


class ObsidianWriteIntent(StrEnum):
    """Whether a prepared Obsidian write plan targets a new or existing note."""

    CREATE = "create"
    UPDATE = "update"
