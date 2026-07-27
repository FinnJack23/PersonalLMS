"""Objective Pack domain contracts: versioned assessment definitions and their evidence boundaries.

Pure data shapes only — no filesystem access, hashing of files, byte
reading, extraction, model call, provider call, or Obsidian access happens
here. See ``personal_lms.objective_packs`` for the deterministic loader and
validator that read configured paths and turn authoring envelopes into
these records.

Domain-neutral throughout: no certification, vendor, exam, or
knowledge-domain name is hard-coded anywhere in this module. A CCNA pack
and an A+ pack are the same schema with different data — see
``docs/product-specs/RAG_KNOWLEDGE_PLANE.md``'s domain-neutrality
requirement and ``domain.knowledge_scope.KnowledgeScope`` for the same
principle applied to retrieval scope.

This module sits alongside — never replaces — the existing retrieval
contracts. ``domain.librarian.GroundingBundle`` remains a runtime
*retrieval result* with backward-compatible serialized forms protected by
existing tests; an Objective Pack is a versioned *assessment definition*.
The two meet through ``ObjectivePackEvidenceEnvelope`` below, which
references evidence by ID rather than embedding or restating a
``GroundingBundle``.

Reused rather than duplicated:

- ``domain.base.StrictModel`` — every boundary record derives from it.
- ``domain.privacy.PrivacyClassification`` — no second privacy enum.
- ``domain.source_inventory.SourceRightsStatus`` — no second rights enum.
- ``domain.knowledge_scope.KnowledgeScope`` — objective/version scope is
  expressed through ``objective_framework``, not a new filter dimension.

Nothing in this module grants approval. ``ReviewState``, ``TrustStatus``,
and ``EligibilityState`` are *recorded* states; only a persisted reviewer
decision (see ``domain.evidence_review``) may move evidence into an
approved state, and no validator or loader in this codebase manufactures
one.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from personal_lms.domain.base import StrictModel
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import SourceRightsStatus

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# An objective reference is "<exam-code>-<blueprint-version>:<number>",
# e.g. "ccna-200-301-v1.1:2.2". The exam version is always part of the
# reference: a v1.1 and a v2.0 mapping of the same objective number are
# separate records even when their labels overlap. Deliberately a pattern
# over a parsed model at this layer so a reference stays one stable,
# comparable, hashable string everywhere it is used as a key.
_OBJECTIVE_REF_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9.\-]*-v[0-9]+(?:\.[0-9]+)*:[0-9]+(?:\.[0-9]+)*$"
)


def _valid_sha256_hex(value: str) -> str:
    if not _SHA256_HEX_PATTERN.fullmatch(value):
        raise ValueError("must be exactly 64 lowercase hex characters")
    return value


class ObjectiveFacet(StrEnum):
    """A dimension of competence an item or claim exercises.

    Generic by design: these five facets describe *how* something is
    known, never *what* domain it belongs to. A pack that needs no
    practical facet simply never references ``CLI_CONFIGURATION`` — the
    schema requires no objective-specific branch.
    """

    CONCEPT = "concept"
    TOPOLOGY_REASONING = "topology_reasoning"
    CLI_CONFIGURATION = "cli_configuration"
    VERIFICATION_TROUBLESHOOTING = "verification_troubleshooting"
    NOVEL_TRANSFER = "novel_transfer"


class PermittedUse(StrEnum):
    """What a source artifact has been cleared to be used for.

    A permission is always explicit and enumerated. Absence of a use is
    denial, never an implied grant — see
    ``EvidenceEligibility.permits_use``.
    """

    LOCAL_EXTRACT = "local_extract"
    LOCAL_INDEX = "local_index"
    LOCAL_TEACH = "local_teach"
    HOSTED_EXCERPT = "hosted_excerpt"
    DERIVED_ITEM = "derived_item"
    PORTFOLIO_DEMO = "portfolio_demo"


class TrustStatus(StrEnum):
    """Whether content may back a graded answer or enter a model context.

    Distinct from ``ReviewState``: review is *whether a human looked*;
    trust is *what the outcome permits*. Untrusted content is still
    retrievable and auditable — it is never silently dropped — but it can
    never make evidence sufficient.
    """

    UNTRUSTED = "untrusted"
    PROVISIONAL = "provisional"
    TRUSTED = "trusted"


class QuarantineStatus(StrEnum):
    """Whether content is withheld from all teaching and retrieval use.

    ``QUARANTINED`` is a hard exclusion applied before ranking, not a
    ranking penalty. It exists so a known-bad region (an injected
    instruction, a wrong-blueprint passage) stays inspectable in the
    catalog while being structurally unable to reach a question, a
    grounding bundle, or a provider context.
    """

    CLEAR = "clear"
    QUARANTINED = "quarantined"


class ReviewState(StrEnum):
    """Where a record sits in human review. Never set automatically.

    No loader, validator, or gate runner in this codebase moves a record
    to ``APPROVED``; only a persisted reviewer decision does (see
    ``domain.evidence_review.EvidenceReviewDecision``).
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EligibilityState(StrEnum):
    """The computed outcome of applying an ``EvidencePolicy`` to a record.

    Always derived, never authored: a pack that declares itself eligible
    is not eligible. ``BLOCKED`` names a policy denial; ``INELIGIBLE``
    names a record that simply does not match the requested scope.
    """

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    BLOCKED = "blocked"


class ValidationSeverity(StrEnum):
    """How a ``ValidationFinding`` affects the overall validation result."""

    ERROR = "error"
    WARNING = "warning"


class ValidationReasonCode(StrEnum):
    """Stable, machine-comparable reasons a record failed validation.

    These strings are part of the gate contract: expected artifacts cite
    them, and negative tests assert on them. Renaming one is a breaking
    change to every frozen gate report, so values are append-only.
    """

    UNKNOWN_ITEM_ID = "unknown_item_id"
    DUPLICATE_ITEM_ID = "duplicate_item_id"
    UNKNOWN_CLAIM_ID = "unknown_claim_id"
    DUPLICATE_CLAIM_ID = "duplicate_claim_id"
    UNKNOWN_EVIDENCE_ID = "unknown_evidence_id"
    DUPLICATE_EVIDENCE_ID = "duplicate_evidence_id"
    UNRESOLVED_CITATION = "unresolved_citation"
    UNKNOWN_SOURCE_ID = "unknown_source_id"
    BASELINE_CARDINALITY = "baseline_cardinality"
    EXPOSURE_SETS_OVERLAP = "exposure_sets_overlap"
    DECLARED_COVERAGE_MISMATCH = "declared_coverage_mismatch"
    OBJECTIVE_REF_MISMATCH = "objective_ref_mismatch"
    BLUEPRINT_VERSION_MISMATCH = "blueprint_version_mismatch"
    UNAPPROVED_ANSWER_EVIDENCE = "unapproved_answer_evidence"
    EVIDENCE_NOT_REVIEWED = "evidence_not_reviewed"
    EVIDENCE_QUARANTINED = "evidence_quarantined"
    RIGHTS_DENIED = "rights_denied"
    USE_NOT_PERMITTED = "use_not_permitted"
    PRIVACY_RESTRICTED = "privacy_restricted"
    UNTRUSTED_EVIDENCE = "untrusted_evidence"
    SOURCE_HASH_MISMATCH = "source_hash_mismatch"
    MANIFEST_HASH_MISMATCH = "manifest_hash_mismatch"
    MANIFEST_ENTRY_MISSING = "manifest_entry_missing"
    MANIFEST_ENTRY_UNLISTED = "manifest_entry_unlisted"
    FOLLOWUP_LIMIT_EXCEEDED = "followup_limit_exceeded"
    FOLLOWUP_RULE_UNMAPPED = "followup_rule_unmapped"
    GROUNDING_BELOW_THRESHOLD = "grounding_below_threshold"
    SCHEMA_VERSION_UNSUPPORTED = "schema_version_unsupported"
    DUPLICATE_SOURCE_ID = "duplicate_source_id"
    EXIT_PROBE_CARDINALITY = "exit_probe_cardinality"
    REQUIRED_FACET_UNCOVERED = "required_facet_uncovered"
    FACET_WEIGHTS_INVALID = "facet_weights_invalid"
    REQUIRED_CLAIM_UNRESOLVED = "required_claim_unresolved"
    RECORD_NOT_REVIEWED = "record_not_reviewed"
    CONTENT_DIGEST_MISMATCH = "content_digest_mismatch"
    CLAIM_BLOCKED_BY_CONFLICT = "claim_blocked_by_conflict"
    CALCULATION_POLICY_MISMATCH = "calculation_policy_mismatch"


class ObjectiveRef(StrictModel):
    """Parsed form of a canonical objective reference string.

    ``ObjectivePack`` and every record below key off the canonical
    ``value`` string rather than this parsed form — this model exists so
    a caller can validate and destructure a reference without inventing
    its own parser, and so version comparison is explicit rather than a
    substring test.
    """

    exam_code: str = Field(min_length=1)
    blueprint_version: str = Field(min_length=1)
    number: str = Field(min_length=1)

    @classmethod
    def parse(cls, value: str) -> Self:
        """Destructure ``"<exam-code>-v<version>:<number>"``.

        Raises ``ValueError`` for any string the canonical pattern does
        not accept — never a best-effort partial parse.
        """
        if not _OBJECTIVE_REF_PATTERN.fullmatch(value):
            raise ValueError(
                "objective_ref must look like '<exam-code>-v<version>:<number>', "
                f"e.g. 'example-exam-v1.1:2.2' (got {value!r})"
            )
        prefix, number = value.split(":", 1)
        exam_code, blueprint_version = prefix.rsplit("-v", 1)
        return cls(exam_code=exam_code, blueprint_version=blueprint_version, number=number)

    @property
    def value(self) -> str:
        """The canonical reference string this record round-trips to."""
        return f"{self.exam_code}-v{self.blueprint_version}:{self.number}"


def validate_objective_ref(value: str) -> str:
    """Field-validator helper: the canonical reference, or ``ValueError``."""
    ObjectiveRef.parse(value)
    return value


class ExtractionMetadata(StrictModel):
    """How one evidence region's content was obtained from source bytes.

    ``method`` names the adapter that did the work (e.g. a local fixture
    extractor's ID), never a claim about a tool that did not run. Gate 1
    does not claim OCR: an image region carries a human-authored
    ``accessible_description`` reviewed through
    ``domain.evidence_review``, and its ``method`` says so.
    """

    method: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    extracted_at: AwareDatetime
    confidence_basis_points: int = Field(
        ge=0,
        le=10_000,
        description="Integer basis points (10000 = 1.0); never a float score.",
    )
    is_ocr: bool = Field(
        default=False,
        description="Gate 1 adapters set this False; OCR is out of scope for this gate.",
    )


class PageTextSelector(StrictModel):
    """Locates text on a page of a paginated source."""

    kind: Literal["page_text"] = "page_text"
    page_number: int = Field(gt=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)

    @model_validator(mode="after")
    def _range_is_ordered(self) -> Self:
        if self.start_offset >= self.end_offset:
            raise ValueError("start_offset must be strictly less than end_offset")
        return self


class ImageRegionSelector(StrictModel):
    """Locates a normalized bounding box within an image.

    Coordinates are normalized basis points of the image's own width and
    height, so a region stays meaningful without embedding pixel
    dimensions that could drift from the actual bytes. ``image_sha256``
    pins the exact image the box was drawn against — a region can never
    be reinterpreted against different bytes after the fact.
    """

    kind: Literal["image_region"] = "image_region"
    page_number: int | None = Field(default=None, gt=0)
    image_sha256: str
    left_basis_points: int = Field(ge=0, le=10_000)
    top_basis_points: int = Field(ge=0, le=10_000)
    right_basis_points: int = Field(ge=0, le=10_000)
    bottom_basis_points: int = Field(ge=0, le=10_000)
    figure_label: str | None = Field(default=None, min_length=1)

    @field_validator("image_sha256")
    @classmethod
    def _image_hash_is_valid_sha256(cls, value: str) -> str:
        return _valid_sha256_hex(value)

    @model_validator(mode="after")
    def _box_is_ordered(self) -> Self:
        if self.left_basis_points >= self.right_basis_points:
            raise ValueError("left_basis_points must be strictly less than right_basis_points")
        if self.top_basis_points >= self.bottom_basis_points:
            raise ValueError("top_basis_points must be strictly less than bottom_basis_points")
        return self


EvidenceSelector = PageTextSelector | ImageRegionSelector


class SourceArtifactRef(StrictModel):
    """Identity, rights, and governance state of one frozen source artifact.

    ``sha256`` is the artifact's identity: any byte change is a different
    artifact, never an update of this one. Nothing here reads bytes — see
    ``extraction.artifacts.verify_source_bytes`` for the deterministic
    check that a file on disk actually matches this record.
    """

    source_id: str = Field(min_length=1)
    sha256: str
    media_type: Literal["application/pdf", "image/png"]
    size_bytes: int = Field(ge=0)
    title: str = Field(min_length=1)
    publisher: str | None = Field(default=None, min_length=1)
    edition_or_version: str | None = Field(default=None, min_length=1)

    rights_status: SourceRightsStatus = SourceRightsStatus.UNKNOWN
    permitted_uses: frozenset[PermittedUse] = Field(default_factory=frozenset)
    attribution_text: str | None = Field(default=None, min_length=1)

    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    trust_status: TrustStatus = TrustStatus.UNTRUSTED
    quarantine_status: QuarantineStatus = QuarantineStatus.CLEAR
    review_state: ReviewState = ReviewState.PENDING

    current_for_objective_refs: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Objective refs this artifact is authoritative for; empty means none.",
    )

    @field_validator("sha256")
    @classmethod
    def _sha_is_valid(cls, value: str) -> str:
        return _valid_sha256_hex(value)

    @field_validator("current_for_objective_refs")
    @classmethod
    def _objective_refs_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for ref in value:
            validate_objective_ref(ref)
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def _restricted_rights_grant_no_use(self) -> Self:
        if self.rights_status is SourceRightsStatus.RESTRICTED and self.permitted_uses:
            raise ValueError(
                "rights_status=restricted must not carry any permitted_uses; "
                "restricted material grants no use"
            )
        return self

    @model_validator(mode="after")
    def _quarantined_is_never_trusted(self) -> Self:
        if (
            self.quarantine_status is QuarantineStatus.QUARANTINED
            and self.trust_status is TrustStatus.TRUSTED
        ):
            raise ValueError("a quarantined artifact must not be trust_status=trusted")
        return self

    @model_validator(mode="after")
    def _trusted_requires_approved_review(self) -> Self:
        if (
            self.trust_status is TrustStatus.TRUSTED
            and self.review_state is not ReviewState.APPROVED
        ):
            raise ValueError("trust_status=trusted requires review_state=approved")
        return self


class EvidenceRegion(StrictModel):
    """One reviewable slice of a source artifact.

    Carries its own governance state rather than inheriting the parent
    artifact's: a single approved PDF may contain one approved paragraph
    and one quarantined injected paragraph, and the difference must be
    expressible. ``EvidencePolicy`` evaluates *both* the region and its
    artifact — the stricter of the two always wins.
    """

    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    selector: EvidenceSelector = Field(discriminator="kind")

    exact_text: str | None = Field(default=None, min_length=1)
    accessible_description: str | None = Field(default=None, min_length=1)
    content_sha256: str

    extraction: ExtractionMetadata
    objective_refs: tuple[str, ...] = Field(default_factory=tuple)
    concept_tags: tuple[str, ...] = Field(default_factory=tuple)

    trust_status: TrustStatus = TrustStatus.UNTRUSTED
    quarantine_status: QuarantineStatus = QuarantineStatus.CLEAR
    review_state: ReviewState = ReviewState.PENDING
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL

    @field_validator("content_sha256")
    @classmethod
    def _content_hash_is_valid(cls, value: str) -> str:
        return _valid_sha256_hex(value)

    @field_validator("objective_refs")
    @classmethod
    def _objective_refs_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for ref in value:
            validate_objective_ref(ref)
        return tuple(sorted(set(value)))

    @field_validator("concept_tags")
    @classmethod
    def _tags_are_sorted_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({tag for tag in value if tag}))

    @model_validator(mode="after")
    def _has_reviewable_content(self) -> Self:
        if self.exact_text is None and self.accessible_description is None:
            raise ValueError(
                "an evidence region must carry exact_text or accessible_description; "
                "a region with neither is not reviewable"
            )
        return self

    @model_validator(mode="after")
    def _image_region_requires_description(self) -> Self:
        if isinstance(self.selector, ImageRegionSelector) and self.accessible_description is None:
            raise ValueError(
                "an image region requires an accessible_description: this gate does not "
                "claim OCR, so a human-authored description is the only readable content"
            )
        return self

    @model_validator(mode="after")
    def _quarantined_is_never_trusted(self) -> Self:
        if (
            self.quarantine_status is QuarantineStatus.QUARANTINED
            and self.trust_status is TrustStatus.TRUSTED
        ):
            raise ValueError("a quarantined region must not be trust_status=trusted")
        return self

    @model_validator(mode="after")
    def _trusted_requires_approved_review(self) -> Self:
        if (
            self.trust_status is TrustStatus.TRUSTED
            and self.review_state is not ReviewState.APPROVED
        ):
            raise ValueError("trust_status=trusted requires review_state=approved")
        return self

    @property
    def is_image_region(self) -> bool:
        return isinstance(self.selector, ImageRegionSelector)

    def review_content_for(self, kind: object) -> str:
        """The exact content a review of ``kind`` binds to.

        Selector kind decides, not field precedence. An image region binds
        to its ``accessible_description`` even when it also carries a
        caption in ``exact_text`` — the description is what a learner
        reads, and preferring the caption meant editing the description
        left a prior approval intact. That was a real hole: the reviewed
        text and the shipped text could diverge silently.

        For an image region the caption is folded in *after* the
        description, so changing either one changes the digest, but the
        description can never be shadowed.
        """
        if self.is_image_region:
            description = self.accessible_description or ""
            caption = self.exact_text or ""
            return f"description:{description}\ncaption:{caption}"
        if self.exact_text is not None:
            return self.exact_text
        if self.accessible_description is not None:
            return self.accessible_description
        raise ValueError("region carries neither exact_text nor accessible_description")

    @property
    def resolved_content(self) -> str:
        """The region's readable content, chosen by selector kind.

        See ``review_content_for``: an image region never lets a caption
        shadow the reviewer-approved description.
        """
        return self.review_content_for(None)

    @property
    def expected_content_sha256(self) -> str:
        """The digest ``content_sha256`` *should* hold, recomputed from content.

        A pack states its own ``content_sha256``; that statement is a
        claim, not authority. The validator compares this recomputed value
        against the authored one and reports
        ``ValidationReasonCode.CONTENT_DIGEST_MISMATCH`` when they differ.
        """
        return hashlib.sha256(self.resolved_content.encode("utf-8")).hexdigest()


class ClaimSupport(StrictModel):
    """One evidence edge backing a claim, with claim-specific factors.

    Authority is a property of *this claim's* relationship to *this
    evidence*, not of the whole source — a highly authoritative document
    can still be weak support for a claim it only mentions in passing.
    All factors are integer basis points; no float score is ever stored.

    ``independence_group`` lets the policy avoid double-counting
    correlated support: two regions extracted from the same figure are
    one independent observation, not two.
    """

    support_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    relationship: Literal["direct", "corroborating", "scope_only"]
    authority_basis_points: int = Field(ge=0, le=10_000)
    directness_basis_points: int = Field(ge=0, le=10_000)
    provenance_completeness_basis_points: int = Field(ge=0, le=10_000)
    extraction_integrity_basis_points: int = Field(ge=0, le=10_000)
    fitness_and_currency_basis_points: int = Field(ge=0, le=10_000)
    independence_group: str = Field(
        min_length=1,
        description=(
            "Advisory correlation label. It may merge distinct evidence a reviewer "
            "considers correlated; it can never split one evidence record into "
            "several independent observations — see objective_packs.scoring."
        ),
    )
    calculation_policy_version: str = Field(
        min_length=1,
        description=(
            "The scoring formula these five factors were assessed under. Required "
            "by 07-DATA-CONTRACTS: factors judged under one policy are not "
            "comparable with factors judged under another."
        ),
    )


class ApprovedClaim(StrictModel):
    """One technical assertion a pack teaches and assesses against.

    ``grounding_score_basis_points`` is *declared* by the authoring
    envelope and is never trusted: ``ObjectivePackValidator`` recomputes
    it from ``support`` and reports
    ``ValidationReasonCode.DECLARED_COVERAGE_MISMATCH`` when the two
    disagree. The field exists so the disagreement is detectable, not so
    the number can be believed.
    """

    claim_id: str = Field(min_length=1)
    canonical_text: str = Field(min_length=1)
    objective_ref: str
    facet: ObjectiveFacet
    support: tuple[ClaimSupport, ...] = Field(default_factory=tuple)
    declared_grounding_score_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    conflict_status: Literal["clear", "minor", "material"] = "clear"
    review_state: ReviewState = ReviewState.PENDING
    is_answer_bearing: bool = Field(
        default=False,
        description="True when an item's answer key depends on this claim.",
    )

    @field_validator("objective_ref")
    @classmethod
    def _objective_ref_is_canonical(cls, value: str) -> str:
        return validate_objective_ref(value)

    @model_validator(mode="after")
    def _support_ids_are_unique(self) -> Self:
        support_ids = [support.support_id for support in self.support]
        if len(support_ids) != len(set(support_ids)):
            raise ValueError("support_id values must be unique within a claim")
        return self


class ExposureClass(StrEnum):
    """Which phase of a session an item may be presented in.

    Exposure sets must be pairwise disjoint: an item burned as a baseline
    diagnostic cannot also serve as its own follow-up or exit probe.
    """

    BASELINE = "baseline"
    FOLLOWUP = "followup"
    EXIT_PROBE = "exit_probe"
    RETEST = "retest"


class AssessmentItem(StrictModel):
    """One approved, gradeable item definition.

    Carries the answer key and rubric; this record never crosses a
    learner-facing or provider-facing boundary. Runtime generation of
    unapproved graded items is prohibited — an item reaches a session only
    by being present in a validated pack.
    """

    item_id: str = Field(min_length=1)
    item_version: int = Field(ge=1)
    objective_ref: str
    exposure_class: ExposureClass
    prompt: str = Field(min_length=1)
    answer_key_sha256: str = Field(
        description="Hash of the canonical answer key; the key itself is pack data, not a field."
    )
    claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    facet_weights: dict[ObjectiveFacet, int] = Field(default_factory=dict)
    misconception_tags: tuple[str, ...] = Field(default_factory=tuple)
    difficulty: Literal["foundation", "application", "transfer"] = "foundation"
    review_state: ReviewState = ReviewState.PENDING

    @field_validator("objective_ref")
    @classmethod
    def _objective_ref_is_canonical(cls, value: str) -> str:
        return validate_objective_ref(value)

    @field_validator("answer_key_sha256")
    @classmethod
    def _answer_key_hash_is_valid(cls, value: str) -> str:
        return _valid_sha256_hex(value)

    @field_validator("claim_ids", "misconception_tags")
    @classmethod
    def _sorted_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({entry for entry in value if entry}))

    @field_validator("facet_weights")
    @classmethod
    def _weights_are_basis_points(
        cls, value: dict[ObjectiveFacet, int]
    ) -> dict[ObjectiveFacet, int]:
        for facet, weight in value.items():
            if not 0 <= weight <= 10_000:
                raise ValueError(f"facet weight for {facet.value} must be 0..10000 basis points")
        return value


class FollowUpRule(StrictModel):
    """A frozen misconception-to-item mapping.

    Deterministic by construction: a finding maps to exactly the listed
    items, in the listed order, or to nothing. No model, heuristic, or
    ranking participates in follow-up selection.
    """

    rule_id: str = Field(min_length=1)
    misconception_tag: str = Field(min_length=1)
    followup_item_ids: tuple[str, ...] = Field(min_length=1)
    mapping_policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _item_ids_are_unique(self) -> Self:
        if len(self.followup_item_ids) != len(set(self.followup_item_ids)):
            raise ValueError("followup_item_ids must not repeat an item")
        return self


class MasteryPolicy(StrictModel):
    """Pack-level policy data driving acquisition and retention decisions.

    Required facets are *pack data*, which is what lets a pack with no
    practical component run through the same generic runner without an
    objective-specific branch: it simply does not list
    ``CLI_CONFIGURATION`` as required.
    """

    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    baseline_item_count: int = Field(gt=0)
    maximum_followup_items: int = Field(ge=0)
    exit_probe_item_count: int = Field(ge=0)
    required_facets: frozenset[ObjectiveFacet] = Field(default_factory=frozenset)
    minimum_claim_grounding_basis_points: int = Field(default=8_500, ge=0, le=10_000)


class ManifestEntry(StrictModel):
    """One pinned file in a pack manifest: relative path plus exact bytes.

    ``relative_path`` is always POSIX-style and always relative — an
    absolute path or a traversal segment is rejected here, before any
    loader touches the filesystem, so a malformed manifest cannot even
    describe a file outside its pack root.
    """

    relative_path: str = Field(min_length=1)
    sha256: str
    size_bytes: int = Field(ge=0)
    media_type: str | None = Field(default=None, min_length=1)

    @field_validator("sha256")
    @classmethod
    def _sha_is_valid(cls, value: str) -> str:
        return _valid_sha256_hex(value)

    @field_validator("relative_path")
    @classmethod
    def _path_is_relative_and_contained(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/"):
            raise ValueError("relative_path must not be absolute")
        if re.fullmatch(r"[a-zA-Z]:.*", normalized):
            raise ValueError("relative_path must not carry a drive letter")
        segments = normalized.split("/")
        if any(segment == ".." for segment in segments):
            raise ValueError("relative_path must not contain a '..' traversal segment")
        if any(segment == "" for segment in segments):
            raise ValueError("relative_path must not contain an empty path segment")
        return normalized


class ObjectivePackManifest(StrictModel):
    """The pinned file inventory and version identity of one pack.

    Every file a pack loads must appear here with its exact hash, and
    every file present must be listed — the loader reports both
    ``MANIFEST_ENTRY_MISSING`` and ``MANIFEST_ENTRY_UNLISTED`` so neither
    a removed nor a smuggled-in file passes unnoticed.
    """

    schema_version: Literal["1.0"] = "1.0"
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    objective_ref: str
    entries: tuple[ManifestEntry, ...] = Field(default_factory=tuple)
    fixture_status: Literal["draft_for_human_review", "reviewed"] = "draft_for_human_review"

    @field_validator("objective_ref")
    @classmethod
    def _objective_ref_is_canonical(cls, value: str) -> str:
        return validate_objective_ref(value)

    @model_validator(mode="after")
    def _entry_paths_are_unique(self) -> Self:
        paths = [entry.relative_path for entry in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest entries must not repeat a relative_path")
        return self

    @property
    def entries_by_path(self) -> dict[str, ManifestEntry]:
        return {entry.relative_path: entry for entry in self.entries}


class ObjectivePack(StrictModel):
    """One versioned, self-consistent assessment definition.

    Assembled only by ``objective_packs.loader``; constructing one
    directly is legitimate in tests but never implies it has been
    validated. Validation is a separate, explicit step
    (``ObjectivePackValidator.validate``) whose findings are data, not
    exceptions, so a gate can report every problem at once rather than
    stopping at the first.
    """

    schema_version: Literal["1.0"] = "1.0"
    manifest: ObjectivePackManifest
    objective_ref: str
    objective_title: str = Field(min_length=1)

    source_artifacts: tuple[SourceArtifactRef, ...] = Field(default_factory=tuple)
    evidence_regions: tuple[EvidenceRegion, ...] = Field(default_factory=tuple)
    claims: tuple[ApprovedClaim, ...] = Field(default_factory=tuple)
    items: tuple[AssessmentItem, ...] = Field(default_factory=tuple)
    followup_rules: tuple[FollowUpRule, ...] = Field(default_factory=tuple)
    mastery_policy: MasteryPolicy

    baseline_item_ids: tuple[str, ...] = Field(default_factory=tuple)
    exit_probe_item_ids: tuple[str, ...] = Field(default_factory=tuple)
    required_claim_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Claims the objective requires; each must resolve to a defined claim.",
    )

    declared_coverage: dict[str, int] = Field(
        default_factory=dict,
        description="Author-declared counts; recomputed by the validator, never trusted.",
    )
    review_state: ReviewState = ReviewState.PENDING

    @field_validator("objective_ref")
    @classmethod
    def _objective_ref_is_canonical(cls, value: str) -> str:
        return validate_objective_ref(value)

    @model_validator(mode="after")
    def _manifest_objective_matches(self) -> Self:
        if self.manifest.objective_ref != self.objective_ref:
            raise ValueError(
                "manifest.objective_ref must equal the pack's objective_ref; "
                "a pack never spans two objective versions"
            )
        return self

    @property
    def answer_bearing_claim_ids(self) -> frozenset[str]:
        """Claim IDs an item's answer key actually depends on.

        **Derived from item references, never from the authored
        ``ApprovedClaim.is_answer_bearing`` flag.** That flag is a pack
        assertion, and trusting it would let an author opt a graded claim
        out of the grounding floor simply by writing ``false``. The union
        of the two is used so an author can additionally *mark* a claim
        answer-bearing, but never un-mark one.
        """
        referenced = {claim_id for item in self.items for claim_id in item.claim_ids}
        marked = {claim.claim_id for claim in self.claims if claim.is_answer_bearing}
        return frozenset(referenced | marked)

    @property
    def items_by_id(self) -> dict[str, AssessmentItem]:
        return {item.item_id: item for item in self.items}

    @property
    def claims_by_id(self) -> dict[str, ApprovedClaim]:
        return {claim.claim_id: claim for claim in self.claims}

    @property
    def evidence_by_id(self) -> dict[str, EvidenceRegion]:
        return {region.evidence_id: region for region in self.evidence_regions}

    @property
    def sources_by_id(self) -> dict[str, SourceArtifactRef]:
        return {source.source_id: source for source in self.source_artifacts}


class ValidationFinding(StrictModel):
    """One machine-comparable problem found during validation.

    ``subject_id`` names the record the finding is about (an item ID, a
    claim ID, a path) so a report can be grouped and diffed without
    parsing prose. ``message`` is for humans and is never asserted on by
    a gate test; ``reason_code`` is the stable contract.
    """

    reason_code: ValidationReasonCode
    severity: ValidationSeverity = ValidationSeverity.ERROR
    subject_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    detail: dict[str, str] = Field(default_factory=dict)

    @property
    def sort_key(self) -> tuple[str, str, str]:
        """Deterministic ordering key: reason, subject, message."""
        return (self.reason_code.value, self.subject_id, self.message)


class ObjectivePackValidationReport(StrictModel):
    """The complete outcome of validating one pack.

    ``is_valid`` is derived, never authored: it is exactly "no findings of
    severity error". A caller cannot construct a passing report that
    carries errors.
    """

    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    objective_ref: str
    findings: tuple[ValidationFinding, ...] = Field(default_factory=tuple)
    recomputed_coverage: dict[str, int] = Field(default_factory=dict)
    recomputed_claim_scores: dict[str, int] = Field(default_factory=dict)
    blocked_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    answer_bearing_claim_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Derived from item references, never from an authored flag.",
    )
    grounding_floor_basis_points: int = Field(ge=0, le=10_000)
    calculation_policy_version: str = Field(
        min_length=1,
        description="Which scoring formula produced recomputed_claim_scores.",
    )
    canonical_pack_hash: str

    @field_validator("canonical_pack_hash")
    @classmethod
    def _hash_is_valid(cls, value: str) -> str:
        return _valid_sha256_hex(value)

    @property
    def errors(self) -> tuple[ValidationFinding, ...]:
        return tuple(
            finding for finding in self.findings if finding.severity is ValidationSeverity.ERROR
        )

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def reason_codes(self) -> tuple[str, ...]:
        """Sorted, de-duplicated reason codes — the stable gate comparison surface."""
        return tuple(sorted({finding.reason_code.value for finding in self.findings}))


class ObjectivePackEvidenceEnvelope(StrictModel):
    """The pack's eligible-evidence view, referenced by ID.

    Deliberately *not* a ``GroundingBundle``: that model is a runtime
    retrieval result with backward-compatible serialized forms protected
    by existing tests, and nothing here changes it. This envelope records
    which evidence a validated pack considers eligible, and why the rest
    was excluded, so a Tutor can later be handed a supplied bundle built
    only from eligible IDs.
    """

    objective_ref: str
    policy_version: str = Field(min_length=1)
    eligible_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    excluded: dict[str, ValidationReasonCode] = Field(
        default_factory=dict,
        description="evidence_id -> the first policy reason that excluded it.",
    )
    index_content_hash: str

    @field_validator("objective_ref")
    @classmethod
    def _objective_ref_is_canonical(cls, value: str) -> str:
        return validate_objective_ref(value)

    @field_validator("index_content_hash")
    @classmethod
    def _hash_is_valid(cls, value: str) -> str:
        return _valid_sha256_hex(value)

    @field_validator("eligible_evidence_ids")
    @classmethod
    def _ids_are_sorted_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))


__all__ = [
    "ApprovedClaim",
    "AssessmentItem",
    "ClaimSupport",
    "EligibilityState",
    "EvidenceRegion",
    "EvidenceSelector",
    "ExposureClass",
    "ExtractionMetadata",
    "FollowUpRule",
    "ImageRegionSelector",
    "ManifestEntry",
    "MasteryPolicy",
    "ObjectiveFacet",
    "ObjectivePack",
    "ObjectivePackEvidenceEnvelope",
    "ObjectivePackManifest",
    "ObjectivePackValidationReport",
    "ObjectiveRef",
    "PageTextSelector",
    "PermittedUse",
    "QuarantineStatus",
    "ReviewState",
    "SourceArtifactRef",
    "TrustStatus",
    "ValidationFinding",
    "ValidationReasonCode",
    "ValidationSeverity",
    "validate_objective_ref",
]
