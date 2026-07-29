"""Deterministic Objective Pack validator.

Findings are *data*, not exceptions. A validator run reports every problem
it can see in one pass, so a reviewer fixes a pack once rather than
discovering the next error after each repair. Callers decide what a
finding means: ``ObjectivePackValidationReport.is_valid`` is simply "no
error-severity findings".

The governing rule of this module is **recompute, never trust**. A pack
may declare its own coverage counts and per-claim grounding scores; the
validator ignores both as authority and recomputes them from the
underlying reference graph and support factors. A declared value that
disagrees with the recomputed one is itself a finding
(``DECLARED_COVERAGE_MISMATCH``) — which is exactly the check that stops
a pack from passing a gate by asserting its own quality.

No LLM call, provider call, network access, or filesystem access happens
here. The validator operates purely on already-loaded records, so it is
reproducible by construction.
"""

from __future__ import annotations

from collections.abc import Iterable

from personal_lms.domain.objective_packs import (
    ExposureClass,
    ObjectivePack,
    ObjectivePackEvidenceEnvelope,
    ObjectivePackValidationReport,
    QuarantineStatus,
    ReviewState,
    ValidationFinding,
    ValidationReasonCode,
    ValidationSeverity,
)
from personal_lms.objective_packs.eligibility import (
    EvidenceEligibility,
    EvidenceIndexSnapshot,
    EvidencePolicy,
    ReviewAuthority,
)
from personal_lms.objective_packs.hashing import hash_record
from personal_lms.objective_packs.scoring import ClaimEvidencePolicy, ClaimGroundingResult

__all__ = [
    "GATE_1_GROUNDING_FLOOR_BASIS_POINTS",
    "ObjectivePackValidator",
]

#: The grounding floor Gate 1 requires of any claim an answer key depends
#: on, from the design's use table (85-100 = "teaching, answer key, rubric,
#: grading rationale"). This belongs to the **trusted gate definition**,
#: not to pack data: an author-controlled field could otherwise lower the
#: bar for their own pack. See ``ObjectivePackValidator.grounding_floor_for``.
GATE_1_GROUNDING_FLOOR_BASIS_POINTS = 8_500


def _scope_severity(*, quarantined: bool, rejected: bool) -> ValidationSeverity:
    """Error for a record still in play; warning for one already excluded."""
    return ValidationSeverity.WARNING if quarantined or rejected else ValidationSeverity.ERROR


class ObjectivePackValidator:
    """Validates one loaded pack, returning every finding at once.

    Constructed with an optional ``ClaimEvidencePolicy`` so the scoring
    formula is injected rather than fixed — a later policy version can be
    exercised side by side with this one without editing the validator.
    """

    def __init__(self, *, claim_policy: ClaimEvidencePolicy | None = None) -> None:
        self._claim_policy = claim_policy if claim_policy is not None else ClaimEvidencePolicy()

    @property
    def claim_policy(self) -> ClaimEvidencePolicy:
        return self._claim_policy

    def validate(self, pack: ObjectivePack) -> ObjectivePackValidationReport:
        """Every structural, reference, and grounding check, in one pass."""
        findings: list[ValidationFinding] = []

        findings.extend(self.validate_unique_ids(pack))
        findings.extend(self.validate_item_references(pack))
        findings.extend(self.validate_baseline_cardinality(pack))
        findings.extend(self.validate_exit_probe_cardinality(pack))
        findings.extend(self.validate_exposure_sets(pack))
        findings.extend(self.validate_evidence_references(pack))
        findings.extend(self.validate_content_digests(pack))
        findings.extend(self.validate_objective_consistency(pack))
        findings.extend(self.validate_required_claims(pack))
        findings.extend(self.validate_record_review_states(pack))
        findings.extend(self.validate_facet_weights(pack))
        findings.extend(self.validate_required_facet_coverage(pack))
        findings.extend(self.validate_followup_rules(pack))
        findings.extend(self.validate_support_policy_versions(pack))

        grounding = {
            claim.claim_id: self._claim_policy.recompute_score(claim) for claim in pack.claims
        }
        floor = self.grounding_floor_for(pack)
        findings.extend(self.validate_claim_grounding(pack, grounding, floor=floor))

        recomputed_coverage = self.recompute_coverage(pack)
        findings.extend(self.validate_declared_coverage(pack, recomputed_coverage))

        return ObjectivePackValidationReport(
            pack_id=pack.manifest.pack_id,
            pack_version=pack.manifest.pack_version,
            objective_ref=pack.objective_ref,
            findings=tuple(sorted(findings, key=lambda finding: finding.sort_key)),
            recomputed_coverage=recomputed_coverage,
            recomputed_claim_scores={
                claim_id: result.score_basis_points for claim_id, result in grounding.items()
            },
            blocked_claim_ids=tuple(
                sorted(claim_id for claim_id, result in grounding.items() if result.blocked)
            ),
            answer_bearing_claim_ids=tuple(sorted(pack.answer_bearing_claim_ids)),
            grounding_floor_basis_points=floor,
            calculation_policy_version=self._claim_policy.policy_version,
            calculation_algorithm_provenance=self._claim_policy.algorithm_provenance,
            canonical_pack_hash=hash_record(pack),
        )

    @staticmethod
    def grounding_floor_for(pack: ObjectivePack) -> int:
        """The grounding floor a pack must actually meet.

        The Gate 1 floor is a property of the *gate*, not of the pack, so
        a pack cannot lower it: the effective floor is the stricter of the
        trusted gate minimum and whatever the pack's own mastery policy
        asks for. A pack may therefore raise its bar but never relax it.
        """
        return max(
            GATE_1_GROUNDING_FLOOR_BASIS_POINTS,
            pack.mastery_policy.minimum_claim_grounding_basis_points,
        )

    # ---- individual checks -------------------------------------------------

    @staticmethod
    def validate_unique_ids(pack: ObjectivePack) -> list[ValidationFinding]:
        """No repeated item, claim, evidence, or source identity."""
        findings: list[ValidationFinding] = []
        checks = (
            ([item.item_id for item in pack.items], ValidationReasonCode.DUPLICATE_ITEM_ID, "item"),
            (
                [claim.claim_id for claim in pack.claims],
                ValidationReasonCode.DUPLICATE_CLAIM_ID,
                "claim",
            ),
            (
                [source.source_id for source in pack.source_artifacts],
                ValidationReasonCode.DUPLICATE_SOURCE_ID,
                "source artifact",
            ),
            (
                [region.evidence_id for region in pack.evidence_regions],
                ValidationReasonCode.DUPLICATE_EVIDENCE_ID,
                "evidence region",
            ),
        )
        for identifiers, reason_code, label in checks:
            for duplicate in sorted(_duplicates(identifiers)):
                findings.append(
                    ValidationFinding(
                        reason_code=reason_code,
                        subject_id=duplicate,
                        message=f"{label} id appears more than once in the pack",
                    )
                )
        return findings

    @staticmethod
    def validate_item_references(pack: ObjectivePack) -> list[ValidationFinding]:
        """Every referenced item ID resolves to exactly one item record."""
        findings: list[ValidationFinding] = []
        known = pack.items_by_id

        referenced: list[tuple[str, str]] = [
            (item_id, "baseline_item_ids") for item_id in pack.baseline_item_ids
        ]
        referenced.extend((item_id, "exit_probe_item_ids") for item_id in pack.exit_probe_item_ids)
        for rule in pack.followup_rules:
            referenced.extend(
                (item_id, f"followup_rule:{rule.rule_id}") for item_id in rule.followup_item_ids
            )

        for item_id, origin in referenced:
            if item_id not in known:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.UNKNOWN_ITEM_ID,
                        subject_id=item_id,
                        message=f"{origin} references an item that the pack does not define",
                        detail={"referenced_from": origin},
                    )
                )

        for reference_list, origin in (
            (pack.baseline_item_ids, "baseline_item_ids"),
            (pack.exit_probe_item_ids, "exit_probe_item_ids"),
        ):
            for duplicate in sorted(_duplicates(reference_list)):
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.DUPLICATE_ITEM_ID,
                        subject_id=duplicate,
                        message=f"{origin} resolves the same item more than once",
                        detail={"referenced_from": origin},
                    )
                )
        return findings

    @staticmethod
    def validate_baseline_cardinality(pack: ObjectivePack) -> list[ValidationFinding]:
        """The baseline holds exactly the item count the mastery policy requires."""
        expected = pack.mastery_policy.baseline_item_count
        actual = len(pack.baseline_item_ids)
        if actual == expected:
            return []
        return [
            ValidationFinding(
                reason_code=ValidationReasonCode.BASELINE_CARDINALITY,
                subject_id=pack.manifest.pack_id,
                message=(
                    f"baseline holds {actual} item reference(s) but the mastery policy "
                    f"requires exactly {expected}"
                ),
                detail={"expected": str(expected), "actual": str(actual)},
            )
        ]

    @staticmethod
    def validate_exposure_sets(pack: ObjectivePack) -> list[ValidationFinding]:
        """Baseline, follow-up, and exit-probe item sets are pairwise disjoint.

        An item burned as a diagnostic cannot honestly re-measure the same
        learner later in the same session, so overlap is an error rather
        than a warning.
        """
        by_class: dict[ExposureClass, set[str]] = {
            ExposureClass.BASELINE: set(pack.baseline_item_ids),
            ExposureClass.EXIT_PROBE: set(pack.exit_probe_item_ids),
            ExposureClass.FOLLOWUP: {
                item_id for rule in pack.followup_rules for item_id in rule.followup_item_ids
            },
        }

        findings: list[ValidationFinding] = []
        ordered = sorted(by_class.items(), key=lambda pair: pair[0].value)
        for index, (left_class, left_ids) in enumerate(ordered):
            for right_class, right_ids in ordered[index + 1 :]:
                for shared in sorted(left_ids & right_ids):
                    findings.append(
                        ValidationFinding(
                            reason_code=ValidationReasonCode.EXPOSURE_SETS_OVERLAP,
                            subject_id=shared,
                            message=(
                                f"item appears in both the {left_class.value} and "
                                f"{right_class.value} exposure sets"
                            ),
                            detail={"left": left_class.value, "right": right_class.value},
                        )
                    )

        # An item's own declared exposure_class must agree with the set it
        # is actually referenced from — otherwise the disjointness proof
        # above says nothing about how the item will really be used.
        items_by_id = pack.items_by_id
        for exposure_class, item_ids in ordered:
            for item_id in sorted(item_ids):
                item = items_by_id.get(item_id)
                if item is not None and item.exposure_class is not exposure_class:
                    findings.append(
                        ValidationFinding(
                            reason_code=ValidationReasonCode.EXPOSURE_SETS_OVERLAP,
                            subject_id=item_id,
                            message=(
                                f"item declares exposure_class={item.exposure_class.value} but is "
                                f"referenced from the {exposure_class.value} set"
                            ),
                            detail={
                                "declared": item.exposure_class.value,
                                "referenced_as": exposure_class.value,
                            },
                        )
                    )
        return findings

    @staticmethod
    def validate_evidence_references(pack: ObjectivePack) -> list[ValidationFinding]:
        """Every claim, support edge, and item citation resolves.

        This is the check that makes an invented citation impossible to
        pass: a support edge naming evidence the pack does not define, or
        an evidence region naming a source it does not define, is an
        error.
        """
        findings: list[ValidationFinding] = []
        known_evidence = pack.evidence_by_id
        known_claims = pack.claims_by_id
        known_sources = pack.sources_by_id

        for region in pack.evidence_regions:
            if region.source_id not in known_sources:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.UNKNOWN_SOURCE_ID,
                        subject_id=region.evidence_id,
                        message="evidence region cites a source artifact the pack does not define",
                        detail={"source_id": region.source_id},
                    )
                )

        for claim in pack.claims:
            for support in claim.support:
                if support.evidence_id not in known_evidence:
                    findings.append(
                        ValidationFinding(
                            reason_code=ValidationReasonCode.UNRESOLVED_CITATION,
                            subject_id=claim.claim_id,
                            message=(
                                "claim support cites an evidence region the pack does not define"
                            ),
                            detail={
                                "support_id": support.support_id,
                                "evidence_id": support.evidence_id,
                            },
                        )
                    )

        for item in pack.items:
            for claim_id in item.claim_ids:
                if claim_id not in known_claims:
                    findings.append(
                        ValidationFinding(
                            reason_code=ValidationReasonCode.UNKNOWN_CLAIM_ID,
                            subject_id=item.item_id,
                            message="item cites a claim the pack does not define",
                            detail={"claim_id": claim_id},
                        )
                    )
        return findings

    @staticmethod
    def validate_objective_consistency(pack: ObjectivePack) -> list[ValidationFinding]:
        """Every claim and item belongs to the pack's own objective version.

        A pack spanning two blueprint versions cannot be reasoned about:
        the same objective number means different things across versions,
        so a mismatch is an error rather than a scope note.
        """
        findings: list[ValidationFinding] = []
        expected = pack.objective_ref

        for claim in pack.claims:
            if claim.objective_ref != expected:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.OBJECTIVE_REF_MISMATCH,
                        subject_id=claim.claim_id,
                        message="claim belongs to a different objective version than the pack",
                        detail={"expected": expected, "actual": claim.objective_ref},
                    )
                )
        for item in pack.items:
            if item.objective_ref != expected:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.OBJECTIVE_REF_MISMATCH,
                        subject_id=item.item_id,
                        message="item belongs to a different objective version than the pack",
                        detail={"expected": expected, "actual": item.objective_ref},
                    )
                )

        # Evidence scope and artifact currency both have to agree with the
        # pack's objective version. A region scoped elsewhere would be
        # filtered at retrieval anyway; catching it here says *why*.
        #
        # Severity depends on whether the record is still in play. A pack
        # is expected to carry deliberate distractors — a wrong-blueprint
        # passage, an injected paragraph — precisely so the exclusion
        # machinery has something to exclude, and those records are
        # quarantined or rejected already. Reporting their out-of-scope
        # refs as *errors* made a pack unable to validate for containing
        # exactly the negative cases the gate requires. They stay
        # reported, as warnings, so they remain inspectable; a record that
        # is not otherwise excluded is still an error, because that one
        # really could reach a learner.
        for region in pack.evidence_regions:
            if expected not in region.objective_refs:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.OBJECTIVE_REF_MISMATCH,
                        severity=_scope_severity(
                            quarantined=region.quarantine_status is QuarantineStatus.QUARANTINED,
                            rejected=region.review_state is ReviewState.REJECTED,
                        ),
                        subject_id=region.evidence_id,
                        message=("evidence region is not scoped to the pack's objective version"),
                        detail={
                            "expected": expected,
                            "actual": ",".join(region.objective_refs) or "(none)",
                        },
                    )
                )

        for source in pack.source_artifacts:
            if expected not in source.current_for_objective_refs:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.OBJECTIVE_REF_MISMATCH,
                        severity=_scope_severity(
                            quarantined=source.quarantine_status is QuarantineStatus.QUARANTINED,
                            rejected=source.review_state is ReviewState.REJECTED,
                        ),
                        subject_id=source.source_id,
                        message=(
                            "source artifact is not recorded as current for the pack's "
                            "objective version"
                        ),
                        detail={
                            "expected": expected,
                            "actual": ",".join(source.current_for_objective_refs) or "(none)",
                        },
                    )
                )
        return findings

    @staticmethod
    def validate_followup_rules(pack: ObjectivePack) -> list[ValidationFinding]:
        """Follow-up mappings stay inside the policy's bound and cover their tags."""
        findings: list[ValidationFinding] = []
        maximum = pack.mastery_policy.maximum_followup_items

        for rule in pack.followup_rules:
            if len(rule.followup_item_ids) > maximum:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.FOLLOWUP_LIMIT_EXCEEDED,
                        subject_id=rule.rule_id,
                        message=(
                            f"rule maps {len(rule.followup_item_ids)} follow-up items but the "
                            f"mastery policy allows at most {maximum}"
                        ),
                        detail={"maximum": str(maximum)},
                    )
                )

        mapped_tags = {rule.misconception_tag for rule in pack.followup_rules}
        for item in pack.items:
            for tag in item.misconception_tags:
                if tag not in mapped_tags:
                    findings.append(
                        ValidationFinding(
                            reason_code=ValidationReasonCode.FOLLOWUP_RULE_UNMAPPED,
                            # An error, not a warning: an item that can
                            # detect a misconception with no deterministic
                            # remediation leaves the adaptive round with
                            # nothing to select, which is a gate-blocking
                            # hole rather than a style note.
                            severity=ValidationSeverity.ERROR,
                            subject_id=item.item_id,
                            message=(
                                f"item carries misconception tag {tag!r} with no follow-up rule; "
                                "a detected gap would have no deterministic remediation"
                            ),
                            detail={"misconception_tag": tag},
                        )
                    )
        return findings

    def validate_support_policy_versions(self, pack: ObjectivePack) -> list[ValidationFinding]:
        """Every support edge was assessed under this validator's formula.

        Five factors judged under one scoring policy are not comparable
        with five judged under another, so mixing them silently would make
        a recomputed score meaningless.

        The comparison is exact equality against the *public* policy
        identifier, and there is deliberately no alias table: an unknown
        or merely similar name fails closed. The specification reference
        (``algorithm_provenance``) is never accepted here — a document
        name is not a contract name.
        """
        expected = self._claim_policy.policy_version
        findings: list[ValidationFinding] = []
        for claim in pack.claims:
            for support in claim.support:
                if support.calculation_policy_version != expected:
                    findings.append(
                        ValidationFinding(
                            reason_code=ValidationReasonCode.CALCULATION_POLICY_MISMATCH,
                            subject_id=claim.claim_id,
                            message=(
                                f"support {support.support_id} was assessed under "
                                f"{support.calculation_policy_version!r}, but this run "
                                f"scores under {expected!r}"
                            ),
                            detail={
                                "support_id": support.support_id,
                                "declared": support.calculation_policy_version,
                                "expected": expected,
                            },
                        )
                    )
        return findings

    @staticmethod
    def validate_exit_probe_cardinality(pack: ObjectivePack) -> list[ValidationFinding]:
        """The exit probe holds exactly the count the mastery policy requires."""
        expected = pack.mastery_policy.exit_probe_item_count
        actual = len(pack.exit_probe_item_ids)
        if actual == expected:
            return []
        return [
            ValidationFinding(
                reason_code=ValidationReasonCode.EXIT_PROBE_CARDINALITY,
                subject_id=pack.manifest.pack_id,
                message=(
                    f"exit probe holds {actual} item reference(s) but the mastery policy "
                    f"requires exactly {expected}"
                ),
                detail={"expected": str(expected), "actual": str(actual)},
            )
        ]

    @staticmethod
    def validate_required_claims(pack: ObjectivePack) -> list[ValidationFinding]:
        """Every required claim ID resolves to a defined claim."""
        known = pack.claims_by_id
        return [
            ValidationFinding(
                reason_code=ValidationReasonCode.REQUIRED_CLAIM_UNRESOLVED,
                subject_id=claim_id,
                message="the objective requires a claim the pack does not define",
            )
            for claim_id in sorted(set(pack.required_claim_ids) - set(known))
        ]

    @staticmethod
    def validate_record_review_states(pack: ObjectivePack) -> list[ValidationFinding]:
        """Referenced items and claims have completed human review.

        A pack containing a pending or rejected record is not ready to be
        a gate subject, regardless of how well-formed the rest of it is.
        """
        findings: list[ValidationFinding] = []
        referenced_items = set(pack.baseline_item_ids) | set(pack.exit_probe_item_ids)
        referenced_items |= {
            item_id for rule in pack.followup_rules for item_id in rule.followup_item_ids
        }

        for item in pack.items:
            if item.item_id in referenced_items and item.review_state is not ReviewState.APPROVED:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.RECORD_NOT_REVIEWED,
                        subject_id=item.item_id,
                        message=(
                            f"referenced item has review_state={item.review_state.value}; "
                            "only an approved item may be presented"
                        ),
                        detail={"review_state": item.review_state.value},
                    )
                )

        answer_bearing = pack.answer_bearing_claim_ids
        for claim in pack.claims:
            required = claim.claim_id in answer_bearing or claim.claim_id in pack.required_claim_ids
            if required and claim.review_state is not ReviewState.APPROVED:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.RECORD_NOT_REVIEWED,
                        subject_id=claim.claim_id,
                        message=(
                            f"required claim has review_state={claim.review_state.value}; "
                            "only an approved claim may back an answer key"
                        ),
                        detail={"review_state": claim.review_state.value},
                    )
                )
        return findings

    @staticmethod
    def validate_facet_weights(pack: ObjectivePack) -> list[ValidationFinding]:
        """Each item's facet weights are present and total exactly one unit.

        Weights that do not sum to 10000 basis points make a facet score
        meaningless — the same answer would contribute a different amount
        depending on which item asked it.
        """
        findings: list[ValidationFinding] = []
        for item in pack.items:
            if not item.facet_weights:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.FACET_WEIGHTS_INVALID,
                        subject_id=item.item_id,
                        message="item declares no facet weights, so it can score nothing",
                    )
                )
                continue
            total = sum(item.facet_weights.values())
            if total != 10_000:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.FACET_WEIGHTS_INVALID,
                        subject_id=item.item_id,
                        message=(f"item facet weights total {total} basis points, not 10000"),
                        detail={"total": str(total)},
                    )
                )
        return findings

    @staticmethod
    def validate_required_facet_coverage(pack: ObjectivePack) -> list[ValidationFinding]:
        """Every facet the mastery policy requires is exercised by some item."""
        covered = {facet for item in pack.items for facet in item.facet_weights}
        return [
            ValidationFinding(
                reason_code=ValidationReasonCode.REQUIRED_FACET_UNCOVERED,
                subject_id=facet.value,
                message=(
                    "the mastery policy requires this facet but no item carries a weight "
                    "for it, so the pack can never produce evidence of it"
                ),
            )
            for facet in sorted(
                pack.mastery_policy.required_facets - covered, key=lambda entry: entry.value
            )
        ]

    @staticmethod
    def validate_content_digests(pack: ObjectivePack) -> list[ValidationFinding]:
        """Each region's authored ``content_sha256`` is recomputed, not believed.

        A pack states the digest of its own content. Accepting that
        statement would let an author bind a reviewer's approval to text
        the reviewer never saw, so the digest is recomputed from the
        content actually present.
        """
        findings: list[ValidationFinding] = []
        for region in pack.evidence_regions:
            expected = region.expected_content_sha256
            if region.content_sha256 != expected:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.CONTENT_DIGEST_MISMATCH,
                        subject_id=region.evidence_id,
                        message=(
                            "region declares a content digest that does not match its own "
                            "content; an authored digest is a claim, never authority"
                        ),
                        detail={
                            "declared": region.content_sha256,
                            "recomputed": expected,
                        },
                    )
                )
        return findings

    def validate_claim_grounding(
        self,
        pack: ObjectivePack,
        grounding: dict[str, ClaimGroundingResult],
        *,
        floor: int,
    ) -> list[ValidationFinding]:
        """Answer-bearing claims meet the effective grounding floor.

        Answer-bearing membership is *derived from item references* (see
        ``ObjectivePack.answer_bearing_claim_ids``), so a pack cannot
        exempt a graded claim by writing ``is_answer_bearing: false``.
        """
        findings: list[ValidationFinding] = []
        answer_bearing = pack.answer_bearing_claim_ids

        for claim in pack.claims:
            result = grounding[claim.claim_id]

            if result.blocked:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.CLAIM_BLOCKED_BY_CONFLICT,
                        subject_id=claim.claim_id,
                        message=(
                            result.block_reason
                            or "the claim is blocked by an unresolved contradiction"
                        ),
                        detail={"conflict_status": claim.conflict_status},
                    )
                )

            if claim.claim_id in answer_bearing and result.meets < floor:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.GROUNDING_BELOW_THRESHOLD,
                        subject_id=claim.claim_id,
                        message=(
                            f"answer-bearing claim scores {result.meets} basis points, below "
                            f"the required {floor}"
                        ),
                        detail={
                            "recomputed": str(result.score_basis_points),
                            "threshold": str(floor),
                            "calculation_policy_version": result.calculation_policy_version,
                        },
                    )
                )

            declared = claim.declared_grounding_score_basis_points
            if declared is not None and declared != result.score_basis_points:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.DECLARED_COVERAGE_MISMATCH,
                        subject_id=claim.claim_id,
                        message=(
                            f"claim declares a grounding score of {declared} basis points but "
                            f"recomputation gives {result.score_basis_points}; the declared "
                            "value is never trusted"
                        ),
                        detail={
                            "declared": str(declared),
                            "recomputed": str(result.score_basis_points),
                        },
                    )
                )
        return findings

    @staticmethod
    def recompute_coverage(pack: ObjectivePack) -> dict[str, int]:
        """Coverage counts derived from the actual reference graph.

        Never reads ``pack.declared_coverage`` — that is the value being
        checked, and using it here would make the check circular.
        """
        coverage: dict[str, int] = {
            "baseline_items": len(pack.baseline_item_ids),
            "exit_probe_items": len(pack.exit_probe_item_ids),
            "followup_items": len(
                {item_id for rule in pack.followup_rules for item_id in rule.followup_item_ids}
            ),
            "claims": len(pack.claims),
            # The *derived* set, matching what the gate actually enforces.
            # Counting the authored ``is_answer_bearing`` flag let a pack
            # report zero answer-bearing claims while the validator gated
            # one — the counter and the gate disagreed.
            "answer_bearing_claims": len(pack.answer_bearing_claim_ids),
            "evidence_regions": len(pack.evidence_regions),
            "source_artifacts": len(pack.source_artifacts),
            "followup_rules": len(pack.followup_rules),
        }
        for facet in sorted({facet.value for item in pack.items for facet in item.facet_weights}):
            coverage[f"facet:{facet}"] = sum(
                1 for item in pack.items if any(key.value == facet for key in item.facet_weights)
            )
        return coverage

    @staticmethod
    def validate_declared_coverage(
        pack: ObjectivePack, recomputed: dict[str, int]
    ) -> list[ValidationFinding]:
        """Declared coverage must agree with recomputed coverage exactly."""
        findings: list[ValidationFinding] = []
        for key, declared in sorted(pack.declared_coverage.items()):
            actual = recomputed.get(key)
            if actual is None:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.DECLARED_COVERAGE_MISMATCH,
                        subject_id=key,
                        message=(
                            "pack declares coverage for a dimension the validator does not compute"
                        ),
                        detail={"declared": str(declared)},
                    )
                )
            elif actual != declared:
                findings.append(
                    ValidationFinding(
                        reason_code=ValidationReasonCode.DECLARED_COVERAGE_MISMATCH,
                        subject_id=key,
                        message=(
                            f"pack declares {declared} for {key!r} but recomputation gives {actual}"
                        ),
                        detail={"declared": str(declared), "recomputed": str(actual)},
                    )
                )
        return findings

    # ---- evidence envelope -------------------------------------------------

    def build_evidence_envelope(
        self,
        pack: ObjectivePack,
        policy: EvidencePolicy,
        *,
        authority: ReviewAuthority | None = None,
    ) -> ObjectivePackEvidenceEnvelope:
        """The pack's eligible-evidence view under ``policy``.

        ``authority`` carries the persisted review decisions. Omitting it
        means nothing is approved — the envelope fails closed rather than
        falling back to the pack's own authored review state.

        Deliberately returns an envelope rather than a
        ``domain.librarian.GroundingBundle``: that model is an existing
        runtime retrieval result with backward-compatible serialized forms
        protected by current tests, and nothing here changes it.
        """
        snapshot = EvidenceIndexSnapshot.build(
            eligibility=EvidenceEligibility(policy, authority=authority),
            regions=pack.evidence_regions,
            artifacts_by_id=pack.sources_by_id,
        )
        return ObjectivePackEvidenceEnvelope(
            objective_ref=pack.objective_ref,
            policy_version=policy.policy_version,
            eligible_evidence_ids=snapshot.eligible_evidence_ids,
            excluded=snapshot.excluded,
            index_content_hash=snapshot.content_hash,
        )

    def validate_answer_evidence(
        self, pack: ObjectivePack, envelope: ObjectivePackEvidenceEnvelope
    ) -> list[ValidationFinding]:
        """No answer key may depend on evidence the policy did not admit.

        This is the check that stops an item being gradeable on the
        strength of quarantined, unreviewed, wrong-version, or
        rights-denied material.
        """
        findings: list[ValidationFinding] = []
        eligible = set(envelope.eligible_evidence_ids)
        claims_by_id = pack.claims_by_id

        for item in pack.items:
            for claim_id in sorted(item.claim_ids):
                claim = claims_by_id.get(claim_id)
                if claim is None:
                    continue
                for support in claim.support:
                    if support.evidence_id not in eligible:
                        findings.append(
                            ValidationFinding(
                                reason_code=ValidationReasonCode.UNAPPROVED_ANSWER_EVIDENCE,
                                subject_id=item.item_id,
                                message=(
                                    "item's answer key depends on evidence that the evidence "
                                    "policy did not admit"
                                ),
                                detail={
                                    "claim_id": claim_id,
                                    "evidence_id": support.evidence_id,
                                    "exclusion_reason": (
                                        envelope.excluded[support.evidence_id].value
                                        if support.evidence_id in envelope.excluded
                                        else ValidationReasonCode.UNKNOWN_EVIDENCE_ID.value
                                    ),
                                },
                            )
                        )
        return findings


def _duplicates(values: Iterable[str]) -> set[str]:
    """Every value appearing more than once."""
    seen: set[str] = set()
    repeated: set[str] = set()
    for value in values:
        if value in seen:
            repeated.add(value)
        seen.add(value)
    return repeated
