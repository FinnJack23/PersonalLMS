"""Source Verifier domain contracts: semantic claim-support verification.

Pure data shapes only — no retrieval, generation, provider routing, or
source curation happens here. These schemas answer a strictly narrower and
different question than structural citation-label validation (see
``personal_lms.tutor._generation.verify_citations`` and
``personal_lms.domain.tutor.CitationIntegrityStatus``):

- structural citation-label validation asks: are the ``[E<n>]`` labels a
  generated answer uses syntactically valid and mapped to evidence that
  was actually supplied to the model?
- the Source Verifier asks: do the cited evidence passages actually
  support the generated claims?

This module never conflates the two. A ``SourceVerificationRequest`` is
only ever constructed *after* structural citation-label validation has
already passed — ``used_citation_labels`` is documented as already
structurally valid, not re-derived or re-checked against a
``GroundingBundle`` here (that cross-check, and the "must not broaden the
grounding bundle" / "no unknown evidence label" guarantees, are enforced
by the calling integration code — see
``personal_lms.source_verification.protocol.validate_result_matches_request``
— since a single Pydantic model cannot validate against a sibling
model's fields).

Avoid storing duplicate full claim text or evidence passages in
``ClaimVerification`` — only structurally-shaped identifiers (a
``claim_id`` and the evidence labels judged relevant) are recorded.
``SourceVerificationRequest.generated_text`` is the one place actual
generated text is allowed to appear, because semantic analysis requires
it; ``SourceVerificationResult`` itself, and every typed
``SourceVerificationError``, must never reproduce generated or evidence
text.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from personal_lms.domain.base import StrictModel
from personal_lms.domain.librarian import GroundingBundle
from personal_lms.domain.privacy import PrivacyClassification

_EVIDENCE_LABEL_PATTERN = re.compile(r"^E\d+$")


class ClaimSupportStatus(StrEnum):
    """Per-claim semantic support verdict — never a structural judgment."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"
    NOT_VERIFIABLE = "not_verifiable"


class SourceVerificationStatus(StrEnum):
    """Overall semantic-verification outcome for one generated answer.

    ``VERIFIED`` is deliberately hard to earn — see
    ``SourceVerificationResult``'s validator: it requires every listed
    claim to be individually ``SUPPORTED`` and both count fields to be
    zero. ``NOT_ASSESSED`` (no verifier was configured/run) and
    ``NOT_APPLICABLE`` (verification was never reachable at all, e.g.
    grounding was insufficient or generation never happened) are distinct
    from each other and from ``FAILED`` (a configured verifier was
    invoked and itself raised a typed failure).
    """

    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    REJECTED = "rejected"
    NOT_ASSESSED = "not_assessed"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


class ClaimVerification(StrictModel):
    """One factual claim's structural support-status verdict.

    ``evidence_labels`` names the already-structurally-valid ``E<n>``
    labels the verifier judged (not) supportive of this claim.
    ``claim_id`` is an opaque identifier (e.g. a sentence index) — the
    claim's own generated text is deliberately never stored here.
    """

    claim_id: str = Field(min_length=1)
    status: ClaimSupportStatus
    evidence_labels: tuple[str, ...] = Field(default_factory=tuple)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)


class SourceVerificationRequest(StrictModel):
    """One request asking whether a generated answer's claims are
    semantically supported by the cited evidence.

    ``generated_text`` is the one place actual generated text is allowed
    to appear in this module — semantic analysis requires it. Safe
    errors and audit summaries elsewhere must never reproduce it.

    ``privacy_classification`` must be copied verbatim from the
    originating ``TutorTeachingRequest.privacy_classification`` — the
    sole privacy source for this whole request chain; no separate
    override is constructed here or accepted downstream.

    ``grounding_bundle`` must be exactly the bundle already used for
    generation — the calling integration code is responsible for never
    broadening it with additional evidence the model never saw.

    ``used_citation_labels`` must already have passed structural
    citation-label validation (see
    ``personal_lms.tutor._generation.verify_citations``) before this
    request is ever constructed; the field validator below only confirms
    each label is *shaped* like ``E<n>``, not that it is actually present
    in ``grounding_bundle`` — that cross-check happens in the calling
    integration code, which already knows the exact set it validated
    against.
    """

    request_id: str = Field(min_length=1)
    generated_text: str = Field(min_length=1)
    grounding_bundle: GroundingBundle
    used_citation_labels: tuple[str, ...] = Field(min_length=1)
    privacy_classification: PrivacyClassification

    @field_validator("used_citation_labels")
    @classmethod
    def _labels_are_structurally_shaped(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for label in value:
            if not _EVIDENCE_LABEL_PATTERN.fullmatch(label):
                raise ValueError(
                    f"{label!r} is not a structurally valid evidence label (expected E<n>)"
                )
        return value


class SourceVerificationResult(StrictModel):
    """The verifier's structured verdict for one ``SourceVerificationRequest``.

    Never claims full text — this schema deliberately carries no
    generated-text or evidence-text field at all.
    """

    request_id: str = Field(min_length=1)
    status: SourceVerificationStatus
    claims: tuple[ClaimVerification, ...] = Field(default_factory=tuple)
    verified_citation_labels: tuple[str, ...] = Field(default_factory=tuple)
    unsupported_claim_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    semantic_confidence: float | None = Field(default=None, ge=0, le=1)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _verified_requires_every_claim_fully_supported(self) -> Self:
        if self.status is SourceVerificationStatus.VERIFIED:
            if self.unsupported_claim_count != 0 or self.conflict_count != 0:
                raise ValueError(
                    "status=VERIFIED requires unsupported_claim_count==0 and conflict_count==0"
                )
            if any(claim.status is not ClaimSupportStatus.SUPPORTED for claim in self.claims):
                raise ValueError("status=VERIFIED requires every listed claim to be SUPPORTED")
        return self

    @model_validator(mode="after")
    def _rejected_requires_a_machine_readable_reason(self) -> Self:
        if self.status is SourceVerificationStatus.REJECTED and not self.reason_codes:
            raise ValueError("status=REJECTED requires at least one reason code")
        return self

    @model_validator(mode="after")
    def _not_assessed_or_not_applicable_never_fabricates_confidence(self) -> Self:
        no_judgment_statuses = (
            SourceVerificationStatus.NOT_ASSESSED,
            SourceVerificationStatus.NOT_APPLICABLE,
        )
        if self.status in no_judgment_statuses and self.semantic_confidence is not None:
            raise ValueError(
                "status=NOT_ASSESSED/NOT_APPLICABLE must not report a semantic_confidence value"
            )
        return self
