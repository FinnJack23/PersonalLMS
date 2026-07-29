"""Claim grounding score — the design formula, implemented exactly.

Authoritative source:
``docs/design/ccna-mastery-micro-lab-design/micro/03-INGESTION-RAG-EVIDENCE.md``.

For one evidence item supporting one claim::

    E_i = A_i × D_i × P_i × X_i × F_i

and for the claim::

    G(c) = max(0, 100 × min(1, E1 + 0.15·E2 + 0.05·E3) − C)

where ``E1``, ``E2``, ``E3`` are the three strongest *independent groups*
and ``C`` is a conflict penalty. A material unresolved contradiction
blocks the claim regardless of the numeric score.

Why a product, not an average
-----------------------------

An earlier implementation used a weighted arithmetic mean, which is not
merely a different formula — it is systematically more permissive in the
exact region that matters. Five factors of 0.85 give 0.85 under a mean but
0.4437 under the product, which is the difference between "cleared for
answer keys" (85–100) and "exclude from factual learner output" (below
70). A product means one worthless factor cannot be averaged away by four
good ones, which is the whole point: evidence that is authoritative,
well-provenanced, cleanly extracted, and current is still worthless if it
does not actually address the claim.

Arithmetic
----------

Everything is exact integer arithmetic in basis points (0–10000). Factors
arrive as basis points, so an edge is a product of five such values
divided by ``10000**4`` to return to basis points. The aggregate is
computed over a common denominator of ``10**18`` and floored exactly once,
at the end. There is no floating-point arithmetic anywhere in this module,
so the same inputs give bit-identical output on every platform.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from personal_lms.domain.objective_packs import ApprovedClaim, ClaimSupport

__all__ = [
    "BASIS_POINTS",
    "CLAIM_SCORE_ALGORITHM_PROVENANCE",
    "CLAIM_SCORE_POLICY_VERSION",
    "GROUP_WEIGHTS_PER_TEN_THOUSAND",
    "ClaimGroundingResult",
    "ClaimEvidencePolicy",
]

#: One whole unit, expressed in basis points.
BASIS_POINTS = 10_000

#: **The public calculation-policy identifier.** This is the name a pack's
#: ``ClaimSupport.calculation_policy_version`` must carry, the value
#: recorded on every result and validation report, and the string a gate
#: comparison keys on. Two scores are comparable exactly when they share
#: it.
#:
#: It is deliberately *not* the name of the design document below. A
#: policy identifier answers "which agreed scoring contract were these
#: factors judged under?"; the specification reference answers "where is
#: that contract written down?". Conflating them made a reviewed fixture
#: — which correctly declares the public contract — appear to disagree
#: with an implementation that was faithfully implementing it.
CLAIM_SCORE_POLICY_VERSION = "ccna-grounding-v1"

#: **Where the arithmetic is specified.** Provenance, never an identity: a
#: pack declaring this string as its ``calculation_policy_version`` is
#: still a mismatch, because it is not the public contract name. Recorded
#: alongside every result so a reader can find the specification without
#: being able to mistake it for the policy identifier.
#:
#: There is deliberately no alias table. A name this module does not
#: recognise as the public identifier fails closed, exactly as an unknown
#: policy always has — accepting a set of "equivalent" names is how two
#: genuinely different formulas end up compared as though they were one.
CLAIM_SCORE_ALGORITHM_PROVENANCE = "design-03-ingestion-rag-evidence-1.0"

#: Diminishing-return weights for the strongest three independent groups,
#: as ten-thousandths: 1.0, 0.15, 0.05 from the design's aggregation rule.
#: Groups beyond the third contribute nothing at all — that is the
#: design's choice, not a truncation for convenience.
GROUP_WEIGHTS_PER_TEN_THOUSAND: tuple[int, ...] = (10_000, 1_500, 500)

# An edge is the product of five basis-point factors. Dividing by
# 10000**4 returns that product to basis points, for reporting.
_EDGE_DENOMINATOR = BASIS_POINTS**4

# Aggregation deliberately works from the *exact* five-factor products
# rather than from already-floored edge scores, so the result is floored
# exactly once. A raw product carries units of 10000**5; multiplying by a
# ten-thousandths weight adds one more factor of 10000, and the final
# score is in basis points, so the divisor is 10000**5.
_AGGREGATE_DENOMINATOR = BASIS_POINTS**5


@dataclass(frozen=True, slots=True)
class ClaimGroundingResult:
    """One claim's recomputed grounding, with everything a reviewer needs.

    ``blocked`` is deliberately separate from a zero score. A blocked claim
    is not "weakly supported"; it is a claim whose supporting evidence
    materially contradicts itself, which no amount of additional support
    can resolve. Collapsing the two would let a reviewer read a blocking
    condition as a quality problem.
    """

    claim_id: str
    score_basis_points: int
    calculation_policy_version: str
    # kw_only so this field cannot shift the position of every field after
    # it. It was inserted between calculation_policy_version and
    # contributing_groups, and a positional caller built against the
    # earlier eight-field signature would otherwise silently bind its
    # contributing_groups tuple to this str parameter instead — a type
    # mismatch that dataclasses do not check at runtime.
    algorithm_provenance: str = field(default=CLAIM_SCORE_ALGORITHM_PROVENANCE, kw_only=True)
    contributing_groups: tuple[str, ...] = ()
    group_scores: tuple[int, ...] = ()
    blocked: bool = False
    block_reason: str | None = None
    edge_scores: dict[str, int] = field(default_factory=dict)

    @property
    def meets(self) -> int:
        """The score, or 0 when blocked — the value a floor check should use."""
        return 0 if self.blocked else self.score_basis_points


class ClaimEvidencePolicy:
    """Recomputes a claim's grounding score from its support edges.

    ``minor_conflict_penalty_basis_points`` defaults to **zero**. The
    design specifies a conflict penalty ``C`` but does not fix its value
    for a minor conflict, and inventing a hidden default would be exactly
    the kind of undocumented substitution this policy exists to avoid. A
    deployment that wants a penalty must state it *and* give the resulting
    policy a distinct ``policy_version``, so a score is always traceable to
    the arithmetic that produced it.
    """

    def __init__(
        self,
        *,
        policy_version: str = CLAIM_SCORE_POLICY_VERSION,
        algorithm_provenance: str = CLAIM_SCORE_ALGORITHM_PROVENANCE,
        minor_conflict_penalty_basis_points: int = 0,
    ) -> None:
        if not policy_version:
            raise ValueError("policy_version must be a nonempty calculation-policy identifier")
        if not algorithm_provenance:
            raise ValueError("algorithm_provenance must be a nonempty specification reference")
        if not 0 <= minor_conflict_penalty_basis_points <= BASIS_POINTS:
            raise ValueError("minor conflict penalty must be 0..10000 basis points")
        if minor_conflict_penalty_basis_points and policy_version == CLAIM_SCORE_POLICY_VERSION:
            raise ValueError(
                "a nonzero minor-conflict penalty changes the arithmetic and therefore "
                "requires its own policy_version; reusing the baseline version would "
                "make two different formulas indistinguishable in a gate report"
            )
        confused_with_provenance = policy_version in (
            CLAIM_SCORE_ALGORITHM_PROVENANCE,
            algorithm_provenance,
        )
        if confused_with_provenance:
            raise ValueError(
                "the specification reference is provenance, not a policy identifier; "
                "a policy_version equal to the known default provenance or to this "
                "policy's own supplied algorithm_provenance would let a document name "
                "stand in for the agreed scoring contract"
            )
        self._policy_version = policy_version
        self._algorithm_provenance = algorithm_provenance
        self._minor_penalty = minor_conflict_penalty_basis_points

    @property
    def policy_version(self) -> str:
        """The public calculation-policy identifier a pack must declare."""
        return self._policy_version

    @property
    def algorithm_provenance(self) -> str:
        """Where this formula is specified. Never used for comparison."""
        return self._algorithm_provenance

    @property
    def minor_conflict_penalty_basis_points(self) -> int:
        return self._minor_penalty

    @staticmethod
    def _factor_values(support: ClaimSupport) -> tuple[int, ...]:
        """The five factors, read by name rather than by reflection.

        Explicit access keeps the scoring path fully typed and makes a
        renamed field a type error instead of a silent zero.
        """
        return (
            support.authority_basis_points,
            support.directness_basis_points,
            support.provenance_completeness_basis_points,
            support.extraction_integrity_basis_points,
            support.fitness_and_currency_basis_points,
        )

    @classmethod
    def _edge_product(cls, support: ClaimSupport) -> int:
        """The exact five-factor product, unrounded.

        ``relationship`` deliberately does not participate. The design
        assigns no multiplier to it; directness already carries "how
        squarely does this address the claim", and adding a second
        relationship factor would double-count it.
        """
        product = 1
        for value in cls._factor_values(support):
            product *= value
        return product

    @classmethod
    def score_support(cls, support: ClaimSupport) -> int:
        """One support edge in basis points, for reporting.

        Aggregation uses the exact product instead, so a claim's score is
        floored exactly once rather than once per edge.
        """
        return cls._edge_product(support) // _EDGE_DENOMINATOR

    def recompute_score(self, claim: ApprovedClaim) -> ClaimGroundingResult:
        """The claim's recomputed grounding under the design formula."""
        edge_products = {
            support.support_id: self._edge_product(support) for support in claim.support
        }
        edge_scores = {
            support_id: product // _EDGE_DENOMINATOR
            for support_id, product in edge_products.items()
        }

        # Collapse correlated support: edges sharing an independence group
        # are one observation, taking the group's strongest edge. Two crops
        # of one figure are not two independent confirmations.
        # Independence is derived from *evidence identity*, not from the
        # author's ``independence_group`` label. Relabelling one evidence
        # record into three groups multiplied its contribution by 1.20,
        # which lifts five 0.94 factors from 7339 (excluded) to 8806
        # (cleared for answer keys) without adding a single new source.
        #
        # The declared label may only *merge* further: two genuinely
        # distinct evidence records sharing a label are one observation,
        # because a reviewer has asserted they are correlated. It can
        # never split one record into many.
        by_evidence: dict[str, int] = {}
        label_by_evidence: dict[str, str] = {}
        for support in claim.support:
            product = edge_products[support.support_id]
            evidence_id = support.evidence_id
            if product > by_evidence.get(evidence_id, -1):
                by_evidence[evidence_id] = product
            label_by_evidence.setdefault(evidence_id, support.independence_group)

        strongest_per_group: dict[str, int] = {}
        for evidence_id, product in by_evidence.items():
            label = label_by_evidence[evidence_id]
            # Group by the declared label when it correlates several
            # distinct records; otherwise the evidence id is its own group.
            shares_label = sum(1 for other in label_by_evidence.values() if other == label)
            group = label if shares_label > 1 else evidence_id
            if product > strongest_per_group.get(group, -1):
                strongest_per_group[group] = product

        # Sort by exact product descending, then by group name so ties are
        # stable regardless of dictionary insertion order.
        ordered = sorted(strongest_per_group.items(), key=lambda pair: (-pair[1], pair[0]))
        contributing = ordered[: len(GROUP_WEIGHTS_PER_TEN_THOUSAND)]

        numerator = sum(
            product * weight
            for (_, product), weight in zip(
                contributing, GROUP_WEIGHTS_PER_TEN_THOUSAND, strict=False
            )
        )
        # min(1, ...) applied before scaling is equivalent to capping the
        # scaled value, because floor(10000 * S) >= 10000 exactly when S >= 1.
        raw = min(BASIS_POINTS, numerator // _AGGREGATE_DENOMINATOR)

        penalty = self._conflict_penalty(claim)
        score = max(0, raw - penalty)

        blocked = claim.conflict_status == "material"
        return ClaimGroundingResult(
            claim_id=claim.claim_id,
            score_basis_points=score,
            calculation_policy_version=self._policy_version,
            algorithm_provenance=self._algorithm_provenance,
            contributing_groups=tuple(group for group, _ in contributing),
            group_scores=tuple(product // _EDGE_DENOMINATOR for _, product in contributing),
            blocked=blocked,
            block_reason=(
                "a material unresolved contradiction blocks the claim regardless of score"
                if blocked
                else None
            ),
            edge_scores=edge_scores,
        )

    def _conflict_penalty(self, claim: ApprovedClaim) -> int:
        """``C`` for this claim.

        A material conflict blocks rather than merely penalises, so it
        contributes no numeric penalty here — ``blocked`` carries it.
        """
        if claim.conflict_status == "minor":
            return self._minor_penalty
        return 0
