"""Evidence review domain contracts: reviewer-only decisions over evidence regions.

Pure data shapes only — no persistence, filesystem access, clock read,
model call, or provider call happens here. See
``personal_lms.evidence_review`` for the append-only repository protocol,
its SQLite implementation, and the service that enforces the review
boundary.

Why this module exists
----------------------

Fixture YAML cannot approve itself. A pack authoring envelope may *claim*
that a region is trusted, but only a persisted decision made by an
identified human reviewer, bound to the exact bytes that were reviewed,
authorizes evidence for teaching or answer-bearing use. This module is the
shape of that decision.

Two properties are structural rather than conventional:

- **Binding to exact content.** Every decision pins
  ``evidence_content_sha256`` (and, for an image region, the image hash
  through the region's own selector). A decision made against one version
  of a region can never silently authorize different bytes — see
  ``EvidenceReviewService.is_current_for`` and the stale-decision check in
  the service layer.
- **Append-only history.** A reviewer changing their mind appends a new
  decision that supersedes the prior one; nothing overwrites or deletes
  review history. ``EvidenceReviewDecision.supersedes_decision_id`` records
  the chain, mirroring ``domain.source_inventory.SourceVersion``'s
  established append-only convention.

Nothing in this module or its package manufactures an approval. There is
no default that produces ``APPROVED``, and no code path where a validator,
loader, or gate runner can construct one on a reviewer's behalf.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, field_validator, model_validator

from personal_lms.domain.base import StrictModel

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Fixed, hardcoded namespace for this module's deterministic decision
# identity derivation — generated once and never changed. Mirrors the
# precedent in ``domain.source_inventory._SOURCE_INVENTORY_NAMESPACE`` and
# ``domain.extraction._EXTRACTION_ARTIFACT_NAMESPACE``: uuid5 only, never
# uuid4, never a process hash, never the system clock.
_EVIDENCE_REVIEW_NAMESPACE = UUID("b1d4c0a6-3f27-4a19-9c5e-2d8f7a6b4013")


def _valid_sha256_hex(value: str) -> str:
    if not _SHA256_HEX_PATTERN.fullmatch(value):
        raise ValueError("must be exactly 64 lowercase hex characters")
    return value


class EvidenceReviewOutcome(StrEnum):
    """What a reviewer decided about one evidence region.

    There is deliberately no "auto" or "inherited" outcome: a decision is
    always an explicit human act. ``NEEDS_CHANGES`` is distinct from
    ``REJECTED`` — the first invites a corrected region, the second closes
    the region out.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CHANGES = "needs_changes"


class EvidenceReviewKind(StrEnum):
    """What aspect of a region was reviewed.

    ``VISUAL`` records that a human actually looked at an image region and
    authored or confirmed its accessible description — the only way image
    content becomes readable in a gate that does not claim OCR.
    """

    TEXT = "text"
    VISUAL = "visual"


class ReviewerIdentity(StrictModel):
    """Who made a decision.

    ``reviewer_id`` is a stable opaque local identifier, never an email
    address, account name, or other personal identifier — this record is
    written into gate reports and must stay free of personal data.
    """

    reviewer_id: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=64)

    @field_validator("reviewer_id")
    @classmethod
    def _reviewer_id_is_opaque(cls, value: str) -> str:
        if "@" in value:
            raise ValueError(
                "reviewer_id must be an opaque local identifier, never an email address"
            )
        return value


class EvidenceReviewDecision(StrictModel):
    """One immutable, reviewer-authored decision about one evidence region.

    Immutable by contract: a correction is a *new* decision naming this
    one in ``supersedes_decision_id``. The repository enforces that at the
    persistence layer (see
    ``evidence_review.errors.EvidenceReviewImmutableError``); this schema
    enforces the shape.

    ``decided_at`` is a required explicit input — this schema never reads
    the system clock, matching the explicit-clock convention used across
    ``source_inventory``, ``extraction``, and ``promotion``.
    """

    decision_id: UUID
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    objective_ref: str = Field(min_length=1)
    kind: EvidenceReviewKind
    outcome: EvidenceReviewOutcome

    subject_digest: str = Field(
        description=(
            "Canonical digest of the whole logical subject reviewed — pack identity, "
            "objective version, evidence identity, kind, source identity and bytes, "
            "selector, and resolved content. See compute_review_subject_digest."
        )
    )
    source_sha256: str = Field(description="The exact source bytes the region was drawn from.")

    reviewer: ReviewerIdentity
    reason: str = Field(min_length=1, max_length=2_000)
    accessible_description: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
        description="Reviewer-authored description; required for an approved visual review.",
    )

    decided_at: AwareDatetime
    supersedes_decision_id: UUID | None = None

    @field_validator("subject_digest", "source_sha256")
    @classmethod
    def _hashes_are_valid(cls, value: str) -> str:
        return _valid_sha256_hex(value)

    @model_validator(mode="after")
    def _cannot_supersede_itself(self) -> Self:
        if self.supersedes_decision_id == self.decision_id:
            raise ValueError("supersedes_decision_id must not equal decision_id")
        return self

    @model_validator(mode="after")
    def _approved_visual_review_carries_a_description(self) -> Self:
        if (
            self.kind is EvidenceReviewKind.VISUAL
            and self.outcome is EvidenceReviewOutcome.APPROVED
            and self.accessible_description is None
        ):
            raise ValueError(
                "an approved visual review requires an accessible_description: "
                "this gate does not claim OCR, so the reviewer's description is "
                "the only readable content the region will ever have"
            )
        return self

    def authorizes(self, *, subject_digest: str) -> bool:
        """Whether this decision authorizes exactly this logical subject.

        Fails closed on any mismatch: a decision made against a different
        subject authorizes nothing, regardless of outcome. Because the
        digest covers pack version, objective version, source bytes,
        selector, and resolved content, changing *any* of them makes a
        prior approval stop applying — which is the whole stale-review
        guarantee in one comparison.
        """
        if self.outcome is not EvidenceReviewOutcome.APPROVED:
            return False
        return self.subject_digest == subject_digest


def compute_review_subject_digest(
    *,
    pack_id: str,
    pack_version: str,
    objective_ref: str,
    evidence_id: str,
    kind: EvidenceReviewKind,
    source_id: str,
    source_sha256: str,
    selector: object,
    resolved_content: str,
) -> str:
    """The canonical digest of everything a reviewer actually reviewed.

    Deliberately broad. A reviewer looking at a region is implicitly
    approving it *as part of this pack version, for this objective
    version, drawn from these exact source bytes, at this exact
    selector, reading this exact content*. Binding the decision to a
    narrower subject — say, region content alone, as the first
    implementation did — would let an approval silently transfer to a
    different pack, a different blueprint version, or a re-cropped
    region that the reviewer never saw.

    ``resolved_content`` is the *recomputed* content: extracted text for
    a text region, the reviewer-approved accessible description for an
    image region. It is never a pack-authored digest, because a pack
    stating its own hash is a claim rather than authority.
    """
    from personal_lms.objective_packs.hashing import canonical_bytes

    payload = {
        "schema": "review-subject-1.0",
        "pack_id": pack_id,
        "pack_version": pack_version,
        "objective_ref": objective_ref,
        "evidence_id": evidence_id,
        "kind": kind.value,
        "source_id": source_id,
        "source_sha256": source_sha256,
        "selector": selector,
        "content_sha256": hashlib.sha256(resolved_content.encode("utf-8")).hexdigest(),
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def derive_decision_id(
    *,
    evidence_id: str,
    subject_digest: str,
    reviewer_id: str,
    decided_at: str,
) -> UUID:
    """Deterministic decision identity — never ``uuid4()``, never random.

    Keyed by the region, the exact subject reviewed, the reviewer, and the
    decision instant, so recording the same logical decision twice (an
    idempotent retry) produces the same ID rather than a duplicate row,
    while a genuine later decision by the same reviewer about the same
    region gets its own identity through ``decided_at``.
    """
    return uuid5(
        _EVIDENCE_REVIEW_NAMESPACE,
        f"{evidence_id}:{subject_digest}:{reviewer_id}:{decided_at}",
    )


__all__ = [
    "EvidenceReviewDecision",
    "EvidenceReviewKind",
    "EvidenceReviewOutcome",
    "ReviewerIdentity",
    "compute_review_subject_digest",
    "derive_decision_id",
]
