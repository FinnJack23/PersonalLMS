"""Design evidence formula (review findings #6–#10).

The first implementation invented a weighted arithmetic mean with a
relationship multiplier and a halving-headroom aggregation. The design
document is authoritative and specifies something different:

    E_i = A_i × D_i × P_i × X_i × F_i

    G(c) = max(0, 100 × min(1, E1 + 0.15·E2 + 0.05·E3) − C)

E1, E2, E3 are the three strongest *independent groups*. A material
unresolved conflict blocks the claim regardless of the numeric score.

The invented formula was not merely different — it was systematically more
generous. Five factors of 0.85 score 0.4437 under the design (a product),
but scored near 0.85 under a weighted mean, which is the difference
between "excluded from factual learner output" and "cleared for answer
keys".
"""

from __future__ import annotations

import pytest

from personal_lms.objective_packs.validation import ClaimEvidencePolicy

from ..objective_packs._helpers import make_claim, make_support


@pytest.fixture
def policy() -> ClaimEvidencePolicy:
    return ClaimEvidencePolicy()


class TestSupportEdgeIsAProduct:
    def test_five_factors_of_8500_produce_4437_basis_points(
        self, policy: ClaimEvidencePolicy
    ) -> None:
        """0.85^5 = 0.4437053125 -> 4437 basis points, floored.

        This single number distinguishes the design formula from every
        averaging variant: a mean of five 8500s is 8500.
        """
        support = make_support(strength=8_500)

        assert policy.score_support(support) == 4_437

    def test_all_perfect_factors_produce_the_maximum(self, policy: ClaimEvidencePolicy) -> None:
        assert policy.score_support(make_support(strength=10_000)) == 10_000

    def test_a_single_zero_factor_zeroes_the_edge(self, policy: ClaimEvidencePolicy) -> None:
        """A product means one worthless factor cannot be averaged away."""
        support = make_support(strength=10_000).model_copy(update={"directness_basis_points": 0})

        assert policy.score_support(support) == 0

    def test_the_relationship_field_does_not_multiply_the_score(
        self, policy: ClaimEvidencePolicy
    ) -> None:
        """The design has no relationship multiplier; adding one is invention."""
        direct = make_support(relationship="direct", strength=9_000)
        corroborating = make_support(relationship="corroborating", strength=9_000)

        assert policy.score_support(direct) == policy.score_support(corroborating)


class TestAggregationUsesThreeStrongestGroups:
    def test_a_single_group_scores_its_own_edge(self, policy: ClaimEvidencePolicy) -> None:
        claim = make_claim(support=(make_support(strength=8_500),))

        assert policy.recompute_score(claim).score_basis_points == 4_437

    def test_the_second_group_contributes_fifteen_percent(
        self, policy: ClaimEvidencePolicy
    ) -> None:
        claim = make_claim(
            support=(
                make_support(evidence_id="ev-1", strength=10_000),
                make_support(support_id="s2", evidence_id="ev-2", strength=10_000),
            )
        )

        # min(1, 1.0 + 0.15) = 1.0 -> capped at 10000.
        assert policy.recompute_score(claim).score_basis_points == 10_000

    def test_weaker_groups_add_their_exact_shares(self, policy: ClaimEvidencePolicy) -> None:
        """E1=0.4437053125, E2=E3 identical: 0.4437053125 * 1.20 = 0.532446375."""
        claim = make_claim(
            support=tuple(
                make_support(support_id=f"s{index}", evidence_id=f"ev-{index}", strength=8_500)
                for index in range(3)
            )
        )

        assert policy.recompute_score(claim).score_basis_points == 5_324

    def test_only_the_three_strongest_groups_contribute(self, policy: ClaimEvidencePolicy) -> None:
        three_groups = make_claim(
            support=tuple(
                make_support(support_id=f"s{index}", evidence_id=f"ev-{index}", strength=8_500)
                for index in range(3)
            )
        )
        eight_groups = make_claim(
            support=tuple(
                make_support(support_id=f"s{index}", evidence_id=f"ev-{index}", strength=8_500)
                for index in range(8)
            )
        )

        assert (
            policy.recompute_score(eight_groups).score_basis_points
            == policy.recompute_score(three_groups).score_basis_points
        )

    def test_correlated_support_collapses_to_its_strongest_edge(
        self, policy: ClaimEvidencePolicy
    ) -> None:
        single = make_claim(support=(make_support(strength=9_000),))
        duplicated = make_claim(
            support=tuple(
                make_support(support_id=f"s{index}", strength=9_000, independence_group="g1")
                for index in range(5)
            )
        )

        assert (
            policy.recompute_score(single).score_basis_points
            == policy.recompute_score(duplicated).score_basis_points
        )

    def test_the_contributing_groups_are_reported(self, policy: ClaimEvidencePolicy) -> None:
        claim = make_claim(
            support=(
                make_support(evidence_id="ev-weak", strength=5_000),
                make_support(support_id="s2", evidence_id="ev-strong", strength=10_000),
            )
        )

        result = policy.recompute_score(claim)

        assert result.contributing_groups[0] == "ev-strong"

    def test_an_unsupported_claim_scores_zero(self, policy: ClaimEvidencePolicy) -> None:
        assert policy.recompute_score(make_claim(support=())).score_basis_points == 0


class TestConflictHandling:
    def test_a_material_conflict_blocks_the_claim_explicitly(
        self, policy: ClaimEvidencePolicy
    ) -> None:
        """Blocking is a state, not a score of zero — the two differ."""
        claim = make_claim(support=(make_support(strength=10_000),), conflict_status="material")

        result = policy.recompute_score(claim)

        assert result.blocked is True
        assert result.block_reason is not None

    def test_a_clear_conflict_applies_no_penalty(self, policy: ClaimEvidencePolicy) -> None:
        claim = make_claim(support=(make_support(strength=8_500),), conflict_status="clear")

        assert policy.recompute_score(claim).score_basis_points == 4_437

    def test_a_minor_conflict_penalty_defaults_to_zero(self, policy: ClaimEvidencePolicy) -> None:
        """The design leaves C unspecified for minor; a hidden default is invention."""
        claim = make_claim(support=(make_support(strength=8_500),), conflict_status="minor")

        assert policy.recompute_score(claim).score_basis_points == 4_437

    def test_a_minor_penalty_must_be_an_explicit_versioned_policy_input(self) -> None:
        configured = ClaimEvidencePolicy(
            policy_version="claim-score-1.0+minor250",
            minor_conflict_penalty_basis_points=250,
        )
        claim = make_claim(support=(make_support(strength=8_500),), conflict_status="minor")

        assert configured.recompute_score(claim).score_basis_points == 4_187

    def test_a_penalty_cannot_drive_the_score_below_zero(self) -> None:
        configured = ClaimEvidencePolicy(
            policy_version="claim-score-1.0+huge",
            minor_conflict_penalty_basis_points=10_000,
        )
        claim = make_claim(support=(make_support(strength=5_000),), conflict_status="minor")

        assert configured.recompute_score(claim).score_basis_points == 0


class TestPolicyVersionIsRecorded:
    def test_the_result_carries_its_calculation_policy_version(
        self, policy: ClaimEvidencePolicy
    ) -> None:
        result = policy.recompute_score(make_claim())

        assert result.calculation_policy_version == policy.policy_version

    def test_the_validation_report_carries_it_too(self) -> None:
        from personal_lms.objective_packs.validation import ObjectivePackValidator

        from ..objective_packs._helpers import make_pack

        report = ObjectivePackValidator().validate(make_pack())

        assert report.calculation_policy_version == ClaimEvidencePolicy().policy_version


class TestAnswerBearingIsDerivedNotAuthored:
    def test_an_item_referenced_claim_is_answer_bearing_despite_the_flag(self) -> None:
        """A pack must not be able to opt a graded claim out of its floor."""
        from personal_lms.objective_packs.validation import ObjectivePackValidator

        from ..objective_packs._helpers import make_pack

        pack = make_pack(
            claims=(
                make_claim(
                    support=(make_support(strength=1_000),),
                    is_answer_bearing=False,
                ),
            )
        )

        report = ObjectivePackValidator().validate(pack)

        assert "claim-1" in report.answer_bearing_claim_ids
        assert "grounding_below_threshold" in report.reason_codes


class TestGroundingFloorIsNotPackControlled:
    def test_a_pack_cannot_lower_the_gate_1_floor(self) -> None:
        """The 8500 floor belongs to the trusted gate definition."""
        from personal_lms.objective_packs.validation import ObjectivePackValidator

        from ..objective_packs._helpers import make_mastery_policy, make_pack

        pack = make_pack(
            mastery_policy=make_mastery_policy(
                minimum_claim_grounding_basis_points=1,
            ),
            claims=(make_claim(support=(make_support(strength=5_000),)),),
        )

        report = ObjectivePackValidator().validate(pack)

        assert "grounding_below_threshold" in report.reason_codes

    def test_a_pack_may_raise_the_floor_above_the_gate_minimum(self) -> None:
        from personal_lms.objective_packs.validation import ObjectivePackValidator

        from ..objective_packs._helpers import make_mastery_policy, make_pack

        pack = make_pack(
            mastery_policy=make_mastery_policy(
                minimum_claim_grounding_basis_points=9_900,
            ),
            claims=(make_claim(support=(make_support(strength=9_900),)),),
        )

        report = ObjectivePackValidator().validate(pack)

        assert "grounding_below_threshold" in report.reason_codes
