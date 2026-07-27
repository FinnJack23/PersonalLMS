"""Eligibility tests: every dimension denies, and denial order is stable.

Each dimension gets a test proving it alone blocks otherwise-eligible
evidence, because a policy where one dimension silently does nothing is
indistinguishable from a policy where it works — until it matters.
"""

from __future__ import annotations

import pytest

from personal_lms.domain.objective_packs import (
    EligibilityState,
    PermittedUse,
    QuarantineStatus,
    ReviewState,
    TrustStatus,
    ValidationReasonCode,
)
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.domain.source_inventory import SourceRightsStatus
from personal_lms.objective_packs.eligibility import (
    ELIGIBILITY_DIMENSIONS,
    EvidenceEligibility,
    EvidenceIndexSnapshot,
    EvidencePolicy,
    allowed_privacy_classifications,
)

from ._helpers import (
    OBJECTIVE_REF,
    OTHER_OBJECTIVE_REF,
    make_image_region,
    make_source,
    make_text_region,
)


@pytest.fixture
def policy() -> EvidencePolicy:
    return EvidencePolicy(
        policy_version="test-1.0",
        objective_ref=OBJECTIVE_REF,
        requested_use=PermittedUse.LOCAL_TEACH,
    )


class _ApprovesEverything:
    """A stand-in review authority for tests about the *other* dimensions.

    Review authority has its own dedicated suite
    (``tests/unit/gate1_regression/test_review_authority.py``). This double
    keeps that dimension satisfied so each test here isolates the
    dimension it is actually about — without it, every case would fail on
    the review check before reaching its subject.
    """

    def authorizes(self, evidence_id: str) -> bool:
        return True


@pytest.fixture
def eligibility(policy: EvidencePolicy) -> EvidenceEligibility:
    return EvidenceEligibility(policy, authority=_ApprovesEverything())


class TestEligibleBaseline:
    def test_a_fully_cleared_region_is_eligible(self, eligibility: EvidenceEligibility) -> None:
        decision = eligibility.evaluate(make_text_region(), make_source())

        assert decision.state is EligibilityState.ELIGIBLE
        assert decision.reason_code is None


class TestEachDimensionDenies:
    def test_quarantined_region_is_blocked(self, eligibility: EvidenceEligibility) -> None:
        region = make_text_region(
            quarantine_status=QuarantineStatus.QUARANTINED, trust_status=TrustStatus.UNTRUSTED
        )

        decision = eligibility.evaluate(region, make_source())

        assert decision.state is EligibilityState.BLOCKED
        assert decision.reason_code is ValidationReasonCode.EVIDENCE_QUARANTINED

    def test_quarantined_artifact_blocks_a_clean_region(
        self, eligibility: EvidenceEligibility
    ) -> None:
        artifact = make_source(
            quarantine_status=QuarantineStatus.QUARANTINED,
            trust_status=TrustStatus.UNTRUSTED,
        )

        decision = eligibility.evaluate(make_text_region(), artifact)

        assert decision.reason_code is ValidationReasonCode.EVIDENCE_QUARANTINED

    @pytest.mark.parametrize(
        "rights_status", [SourceRightsStatus.RESTRICTED, SourceRightsStatus.UNKNOWN]
    )
    def test_denied_rights_block(
        self, eligibility: EvidenceEligibility, rights_status: SourceRightsStatus
    ) -> None:
        artifact = make_source(
            rights_status=rights_status,
            permitted_uses=frozenset() if rights_status is SourceRightsStatus.RESTRICTED else None,
        )

        decision = eligibility.evaluate(make_text_region(), artifact)

        assert decision.reason_code is ValidationReasonCode.RIGHTS_DENIED

    def test_an_unlisted_use_is_denied_rather_than_implied(
        self, eligibility: EvidenceEligibility
    ) -> None:
        artifact = make_source(permitted_uses=frozenset({PermittedUse.LOCAL_INDEX}))

        decision = eligibility.evaluate(make_text_region(), artifact)

        assert decision.reason_code is ValidationReasonCode.USE_NOT_PERMITTED

    def test_a_region_above_the_privacy_ceiling_is_blocked(
        self, eligibility: EvidenceEligibility
    ) -> None:
        region = make_text_region(
            privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY
        )

        decision = eligibility.evaluate(region, make_source())

        assert decision.reason_code is ValidationReasonCode.PRIVACY_RESTRICTED

    def test_the_wrong_blueprint_version_is_out_of_scope(
        self, eligibility: EvidenceEligibility
    ) -> None:
        region = make_text_region(objective_refs=(OTHER_OBJECTIVE_REF,))

        decision = eligibility.evaluate(region, make_source())

        assert decision.state is EligibilityState.INELIGIBLE
        assert decision.reason_code is ValidationReasonCode.OBJECTIVE_REF_MISMATCH

    def test_evidence_with_no_persisted_approval_is_blocked(self, policy: EvidencePolicy) -> None:
        """No authority supplied means nothing is approved — fail closed."""
        decision = EvidenceEligibility(policy).evaluate(make_text_region(), make_source())

        assert decision.reason_code is ValidationReasonCode.EVIDENCE_NOT_REVIEWED

    def test_untrusted_evidence_is_blocked(self, eligibility: EvidenceEligibility) -> None:
        region = make_text_region(trust_status=TrustStatus.PROVISIONAL)

        decision = eligibility.evaluate(region, make_source())

        assert decision.reason_code is ValidationReasonCode.UNTRUSTED_EVIDENCE


class TestDenialOrderIsStable:
    def test_quarantine_wins_over_every_other_failure(
        self, eligibility: EvidenceEligibility
    ) -> None:
        """A region failing several dimensions always reports the first one."""
        region = make_text_region(
            quarantine_status=QuarantineStatus.QUARANTINED,
            review_state=ReviewState.PENDING,
            trust_status=TrustStatus.UNTRUSTED,
            objective_refs=(OTHER_OBJECTIVE_REF,),
        )
        artifact = make_source(
            rights_status=SourceRightsStatus.RESTRICTED, permitted_uses=frozenset()
        )

        decision = eligibility.evaluate(region, artifact)

        assert decision.reason_code is ValidationReasonCode.EVIDENCE_QUARANTINED
        assert decision.dimension == "quarantine"

    def test_the_policy_dimensions_are_covered_by_the_retrieval_filter(self) -> None:
        """Every policy dimension must be expressible in SQL before LIMIT.

        The retrieval filter carries one dimension the in-memory policy
        does not — ``content_binding``, which ties a governance row to the
        current chunk, document, and source. Coverage is therefore a
        subset check rather than equality.
        """
        from personal_lms.content.protocol import ChunkEligibilityFilter

        assert set(ELIGIBILITY_DIMENSIONS) <= set(ChunkEligibilityFilter.DIMENSIONS)


class TestPrivacyCeiling:
    def test_a_ceiling_permits_itself_and_everything_less_restrictive(self) -> None:
        assert allowed_privacy_classifications(PrivacyClassification.INTERNAL) == frozenset(
            {PrivacyClassification.PUBLIC, PrivacyClassification.INTERNAL}
        )

    def test_public_permits_only_public(self) -> None:
        assert allowed_privacy_classifications(PrivacyClassification.PUBLIC) == frozenset(
            {PrivacyClassification.PUBLIC}
        )


class TestIndexSnapshot:
    def test_snapshot_separates_eligible_from_excluded(
        self, eligibility: EvidenceEligibility
    ) -> None:
        regions = (
            make_text_region(evidence_id="ev-ok"),
            make_text_region(
                evidence_id="ev-quarantined",
                quarantine_status=QuarantineStatus.QUARANTINED,
                trust_status=TrustStatus.UNTRUSTED,
            ),
        )
        artifacts = {"src-pdf": make_source()}

        snapshot = EvidenceIndexSnapshot.build(
            eligibility=eligibility, regions=regions, artifacts_by_id=artifacts
        )

        assert snapshot.eligible_evidence_ids == ("ev-ok",)
        assert snapshot.excluded["ev-quarantined"] is ValidationReasonCode.EVIDENCE_QUARANTINED

    def test_a_region_with_unresolvable_provenance_is_blocked_not_skipped(
        self, eligibility: EvidenceEligibility
    ) -> None:
        regions = (make_text_region(evidence_id="ev-orphan", source_id="src-missing"),)

        snapshot = EvidenceIndexSnapshot.build(
            eligibility=eligibility, regions=regions, artifacts_by_id={}
        )

        assert snapshot.eligible_evidence_ids == ()
        assert snapshot.excluded["ev-orphan"] is ValidationReasonCode.UNKNOWN_SOURCE_ID

    def test_the_index_hash_is_reproducible_and_order_independent(
        self, eligibility: EvidenceEligibility
    ) -> None:
        artifacts = {"src-pdf": make_source(), "src-png": make_source(source_id="src-png")}
        first_order = (make_text_region(evidence_id="ev-a"), make_text_region(evidence_id="ev-b"))
        second_order = tuple(reversed(first_order))

        first = EvidenceIndexSnapshot.build(
            eligibility=eligibility, regions=first_order, artifacts_by_id=artifacts
        )
        second = EvidenceIndexSnapshot.build(
            eligibility=eligibility, regions=second_order, artifacts_by_id=artifacts
        )

        assert first.content_hash == second.content_hash

    def test_the_index_hash_changes_when_eligible_content_changes(
        self, eligibility: EvidenceEligibility
    ) -> None:
        artifacts = {"src-pdf": make_source()}
        baseline = EvidenceIndexSnapshot.build(
            eligibility=eligibility,
            regions=(make_text_region(evidence_id="ev-a"),),
            artifacts_by_id=artifacts,
        )
        altered = EvidenceIndexSnapshot.build(
            eligibility=eligibility,
            regions=(make_text_region(evidence_id="ev-a", text="Different synthetic text."),),
            artifacts_by_id=artifacts,
        )

        assert baseline.content_hash != altered.content_hash


class TestRelaxedPolicies:
    def test_a_policy_may_admit_untrusted_evidence_for_exploration(self) -> None:
        relaxed = EvidencePolicy(
            policy_version="relaxed-1.0",
            objective_ref=OBJECTIVE_REF,
            require_trusted=False,
            require_approved_review=False,
        )
        region = make_text_region(
            trust_status=TrustStatus.PROVISIONAL, review_state=ReviewState.PENDING
        )

        decision = EvidenceEligibility(relaxed).evaluate(region, make_source())

        assert decision.state is EligibilityState.ELIGIBLE

    def test_relaxing_review_never_relaxes_it_for_a_gate_policy(self) -> None:
        """The gate's own policy always requires a persisted approval."""
        strict = EvidencePolicy(policy_version="gate-1", objective_ref=OBJECTIVE_REF)

        assert strict.require_approved_review is True
        assert strict.require_trusted is True

    def test_an_image_region_follows_the_same_rules(self, eligibility: EvidenceEligibility) -> None:
        from ._helpers import PNG_BYTES

        artifact = make_source(source_id="src-png", payload=PNG_BYTES, media_type="image/png")

        decision = eligibility.evaluate(make_image_region(), artifact)

        assert decision.state is EligibilityState.ELIGIBLE
