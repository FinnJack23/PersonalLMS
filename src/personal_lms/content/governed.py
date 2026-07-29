"""Governed retrieval: eligibility bound to current content, applied before ``LIMIT``.

Extends the existing content layer rather than replacing it. There is one
``ContentRepository`` and one FTS5 index; this module adds a *narrow
subprotocol* (``GovernedContentRepository``) plus the record and filter
shapes that let governance participate in the same SQL query. Existing
callers and wrappers stay structurally compatible with the base protocol,
and an ungoverned search behaves exactly as it always did.

Two defects in the first attempt drove the design:

**Governance was bound to nothing.** A row keyed only by ``chunk_id``
outlived the content it governed: replacing a chunk's text, downgrading
its parent document, or superseding its source left the row intact and the
chunk still retrievable, so an approval quietly transferred to material no
one had approved. Every ``GovernedChunkEligibility`` row now pins the
chunk's text hash, the parent document's content hash, the source
identity and bytes, the governing review decision, and the eligibility
policy version. Any of those changing makes the row stop matching — no
invalidation sweep required, because the join simply fails.

**Objective scope was a parallel string.** The row carried its own
``objective_ref``, duplicating (and able to disagree with) the existing
``KnowledgeScope.objective_framework`` relation that the rest of the
platform filters on. Objective scope now comes from that relation and
nowhere else.

Every constraint is expressed in the WHERE clause, so SQLite evaluates it
while selecting candidates — before ``ORDER BY`` and ``LIMIT``. Filtering
after ``LIMIT`` would be a security hole shaped like a performance
detail: ineligible rows would consume the result window and the caller
would be told "no results" rather than the truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from personal_lms.content.protocol import ChunkSearchFilters, ChunkSearchHit, SourceSearchMode
from personal_lms.domain.objective_packs import PermittedUse, QuarantineStatus, TrustStatus
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import SourceRightsStatus

__all__ = [
    "GOVERNED_DIMENSIONS",
    "GovernedChunkEligibility",
    "GovernedContentRepository",
    "GovernedRetrievalPolicy",
    "build_governed_filters",
]

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: The dimensions a governed retrieval constrains, all in SQL.
GOVERNED_DIMENSIONS: tuple[str, ...] = (
    "quarantine",
    "rights",
    "permitted_use",
    "privacy",
    "objective_version",
    "review_state",
    "trust",
    "content_binding",
)

# Rights states that grant nothing. UNKNOWN is a denial rather than a
# neutral default: absence of a recorded right is not permission.
_RIGHTS_GRANTING = frozenset(
    {
        SourceRightsStatus.OWNED,
        SourceRightsStatus.LICENSED,
        SourceRightsStatus.PUBLIC_REFERENCE,
    }
)


@dataclass(frozen=True, slots=True)
class GovernedChunkEligibility:
    """One chunk's governance record, bound to the exact content it governs.

    The binding fields are not metadata — they are the join keys. A
    governed search matches this row only when the chunk's *current* text
    hash, its parent document's *current* content hash, and its source's
    *current* identity and bytes all still equal what was recorded here.
    That is what makes stale governance structurally impossible rather
    than a thing to remember to clean up.

    ``review_decision_id`` names the persisted ``EvidenceReviewDecision``
    that authorized this chunk. It is required and non-empty: governance
    without a decision behind it is not governance.
    """

    chunk_id: str
    chunk_text_hash: str
    document_content_hash: str
    source_id: str
    source_sha256: str
    review_decision_id: str
    eligibility_policy_version: str
    rights_status: SourceRightsStatus = SourceRightsStatus.UNKNOWN
    permitted_uses: frozenset[PermittedUse] = frozenset()
    trust_status: TrustStatus = TrustStatus.UNTRUSTED
    quarantine_status: QuarantineStatus = QuarantineStatus.CLEAR

    def __post_init__(self) -> None:
        for name in ("chunk_id", "source_id", "eligibility_policy_version"):
            if not getattr(self, name):
                raise ValueError(f"{name} must not be empty")
        if not self.review_decision_id:
            raise ValueError(
                "review_decision_id must name the persisted decision that authorized "
                "this chunk; governance with no decision behind it is not governance"
            )
        for name in ("chunk_text_hash", "document_content_hash", "source_sha256"):
            value = getattr(self, name)
            if not _SHA256_HEX_PATTERN.fullmatch(value):
                raise ValueError(f"{name} must be exactly 64 lowercase hex characters")


@dataclass(frozen=True, slots=True)
class GovernedRetrievalPolicy:
    """What a governed retrieval is asking for.

    Mirrors ``objective_packs.eligibility.EvidencePolicy`` at the
    retrieval layer. ``policy_version`` participates in the match, so a
    governance row written under one policy never satisfies a query made
    under another — a policy change invalidates prior eligibility rather
    than silently reinterpreting it.
    """

    policy_version: str
    objective_ref: str
    requested_use: PermittedUse = PermittedUse.LOCAL_TEACH
    privacy_ceiling: PrivacyClassification = PrivacyClassification.INTERNAL


@runtime_checkable
class GovernedContentRepository(Protocol):
    """Narrow governance extension over the base ``ContentRepository``.

    Deliberately a *separate* protocol rather than new members on the base
    one, so existing wrappers and fakes that satisfy ``ContentRepository``
    keep satisfying it without change.
    """

    def upsert_eligibility(self, record: GovernedChunkEligibility) -> None:
        """Insert or replace one chunk's governance record.

        The chunk must already exist — see
        ``content.errors.ChunkNotFoundError``.
        """
        ...

    def get_eligibility(self, chunk_id: str) -> GovernedChunkEligibility | None:
        """The chunk's governance record, or ``None`` if never recorded.

        ``None`` means *not eligible* to every governed query, never
        "unfiltered".
        """
        ...

    def search(
        self,
        query: str,
        *,
        mode: SourceSearchMode = SourceSearchMode.ALL_TERMS,
        filters: ChunkSearchFilters | None = None,
        limit: int = 20,
    ) -> tuple[ChunkSearchHit, ...]: ...


def build_governed_filters(
    policy: GovernedRetrievalPolicy,
    *,
    known_decision_ids: frozenset[str] | None = None,
    current_source_sha256: dict[str, str] | None = None,
) -> ChunkSearchFilters:
    """The fully-constrained filter set for one governed retrieval.

    Turns on every dimension at once. A caller hand-assembling a partial
    filter for the same purpose is almost certainly leaving one open by
    accident, which is why the gate path always goes through here.

    ``known_decision_ids`` is the set of review decisions that actually
    exist in the review store. A governance row naming anything else is
    refused: a non-empty ``review_decision_id`` string is not evidence
    that a human decided anything, and treating it as such let a
    fabricated UUID authorize retrieval. Passing ``None`` means "the
    caller has not resolved the review store", which is only appropriate
    for tests of the *other* dimensions — a gate run always passes the
    real set.

    ``current_source_sha256`` maps source id to the bytes that source
    currently holds, so a governance row pinned to superseded bytes stops
    matching. ``None`` means the caller could not prove current source
    bytes through an existing repository boundary; the gate treats that
    as BLOCKED rather than silently claiming the binding holds.

    Objective scope is expressed as ``objective_framework`` — the existing
    ``KnowledgeScope`` relation — rather than a governance-table column,
    so governed and ungoverned retrieval agree on what "this objective
    version" means.
    """
    from personal_lms.content.protocol import ChunkEligibilityFilter

    return ChunkSearchFilters(
        objective_framework=policy.objective_ref,
        allowed_privacy_classifications=_allowed_privacy(policy.privacy_ceiling),
        eligibility=ChunkEligibilityFilter(
            exclude_quarantined=True,
            allowed_rights_statuses=_RIGHTS_GRANTING,
            required_permitted_use=policy.requested_use,
            allowed_trust_statuses=frozenset({TrustStatus.TRUSTED}),
            allowed_document_privacy=_allowed_privacy(policy.privacy_ceiling),
            require_current_binding=True,
            eligibility_policy_version=policy.policy_version,
            known_review_decision_ids=known_decision_ids,
            current_source_sha256=(
                tuple(sorted(current_source_sha256.items()))
                if current_source_sha256 is not None
                else None
            ),
        ),
    )


def _allowed_privacy(
    ceiling: PrivacyClassification,
) -> frozenset[PrivacyClassification]:
    """Every classification a consumer at ``ceiling`` may see.

    Explicit ranking, deliberately not derived from the enum's declaration
    order, so reordering that enum for display cannot silently change a
    security decision. Mirrors ``librarian.content_grounding._PRIVACY_RANK``.
    """
    rank = {
        PrivacyClassification.PUBLIC: 0,
        PrivacyClassification.INTERNAL: 1,
        PrivacyClassification.SENSITIVE: 2,
        PrivacyClassification.RESTRICTED_LOCAL_ONLY: 3,
    }
    ceiling_rank = rank[ceiling]
    return frozenset(
        classification for classification, value in rank.items() if value <= ceiling_rank
    )
