"""Provider-neutral ``SourceVerifier`` structural contract.

Structural contract for any semantic claim-support verifier — fake,
local-model-backed, or hosted-model-backed. No vendor name, model name,
HTTP method, API key, or transport detail belongs here, mirroring
``personal_lms.providers.protocol.ModelProvider``'s own vendor-neutrality
(see ADR-0002). This package never depends on
``personal_lms.tutor`` internals — the dependency runs the other way (the
Tutor package depends on this contract), keeping the Source Verifier
usable outside any specific Tutor flow.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from personal_lms.domain.source_verification import (
    SourceVerificationRequest,
    SourceVerificationResult,
)
from personal_lms.source_verification.errors import SourceVerificationContractError


@runtime_checkable
class SourceVerifier(Protocol):
    """Structural contract every semantic claim-support verifier must satisfy."""

    async def verify(self, request: SourceVerificationRequest) -> SourceVerificationResult: ...


def validate_result_matches_request(
    request: SourceVerificationRequest,
    result: SourceVerificationResult,
    *,
    verifier_id: str,
) -> None:
    """Defensively cross-check any ``SourceVerifier``'s result against its request.

    Enforced here (integration-side), not inside ``SourceVerificationResult``
    itself, since a single Pydantic model cannot validate against a sibling
    object's fields. Protects callers against a misbehaving verifier
    implementation (fake or real) that echoes the wrong ``request_id``,
    fabricates an unknown citation label as "verified", or attributes a
    claim to an evidence label the request never supplied — i.e. this is
    where "verified citation labels must be a subset of structurally used
    labels" and "no unknown evidence label may appear" are actually
    enforced.
    """
    if result.request_id != request.request_id:
        raise SourceVerificationContractError(
            verifier_id,
            f"result.request_id {result.request_id!r} does not match the request",
        )

    used = set(request.used_citation_labels)

    unknown_verified = set(result.verified_citation_labels) - used
    if unknown_verified:
        raise SourceVerificationContractError(
            verifier_id,
            f"result verified unknown citation label(s): {sorted(unknown_verified)!r}",
        )

    unknown_claim_labels = {
        label for claim in result.claims for label in claim.evidence_labels
    } - used
    if unknown_claim_labels:
        raise SourceVerificationContractError(
            verifier_id,
            f"result claim references unknown citation label(s): {sorted(unknown_claim_labels)!r}",
        )
