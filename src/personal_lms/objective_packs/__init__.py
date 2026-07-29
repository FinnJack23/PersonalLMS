"""Objective Packs: deterministic loading, eligibility, and validation.

A bounded Tier 0 service. Nothing in this package makes a model call, a
provider call, or a network request; nothing writes to the filesystem;
and nothing manufactures a human approval. Running any of it twice over
unchanged inputs produces identical results, which is what lets a gate
report cite its hashes as evidence.

Layout:

- ``loader``     — path admission, byte verification, strict parsing.
- ``eligibility``— the one definition of evidence eligibility.
- ``validation`` — recompute-never-trust structural checks.
- ``hashing``    — canonical logical-record hashing (never storage bytes).
- ``errors``     — typed, reason-coded failures.

The domain contracts these operate on live in
``personal_lms.domain.objective_packs``.
"""

from __future__ import annotations

from personal_lms.objective_packs.eligibility import (
    ELIGIBILITY_DIMENSIONS,
    EligibilityDecision,
    EvidenceEligibility,
    EvidenceIndexSnapshot,
    EvidencePolicy,
)
from personal_lms.objective_packs.errors import (
    ObjectivePackError,
    PackFileNotFoundError,
    PackFileTooLargeError,
    PackFileTypeError,
    PackHashMismatchError,
    PackManifestError,
    PackPathEscapesRootError,
    PackRootNotConfiguredError,
    PackSchemaError,
)
from personal_lms.objective_packs.hashing import canonical_json, hash_record, hash_records
from personal_lms.objective_packs.loader import (
    LoaderLimits,
    ObjectivePackLoader,
    PackFileReader,
    PackLoadResult,
)
from personal_lms.objective_packs.scoring import (
    CLAIM_SCORE_ALGORITHM_PROVENANCE,
    CLAIM_SCORE_POLICY_VERSION,
    ClaimEvidencePolicy,
    ClaimGroundingResult,
)
from personal_lms.objective_packs.validation import (
    GATE_1_GROUNDING_FLOOR_BASIS_POINTS,
    ObjectivePackValidator,
)

__all__ = [
    "CLAIM_SCORE_ALGORITHM_PROVENANCE",
    "CLAIM_SCORE_POLICY_VERSION",
    "ELIGIBILITY_DIMENSIONS",
    "GATE_1_GROUNDING_FLOOR_BASIS_POINTS",
    "ClaimEvidencePolicy",
    "ClaimGroundingResult",
    "EligibilityDecision",
    "EvidenceEligibility",
    "EvidenceIndexSnapshot",
    "EvidencePolicy",
    "LoaderLimits",
    "ObjectivePackError",
    "ObjectivePackLoader",
    "ObjectivePackValidator",
    "PackFileNotFoundError",
    "PackFileReader",
    "PackFileTooLargeError",
    "PackFileTypeError",
    "PackHashMismatchError",
    "PackLoadResult",
    "PackManifestError",
    "PackPathEscapesRootError",
    "PackRootNotConfiguredError",
    "PackSchemaError",
    "canonical_json",
    "hash_record",
    "hash_records",
]
