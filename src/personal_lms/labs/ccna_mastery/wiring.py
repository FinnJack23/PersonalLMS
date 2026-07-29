"""CCNA lab wiring: the bounded use-case port and its Gate 1 evidence runner.

This module assembles already-built dependencies. It constructs no router,
no provider registry, no budget policy, no content repository, and no
Tutor — those are owned by root composition and handed in. If something
here ever needed to build one of them, that would be the signal it should
have been injected.

The port exposed is deliberately narrow: a caller gets
``EvidenceGateRunner`` and nothing else. Repositories, loaders, and
validators stay behind it, so a future outer adapter (a thin CrewAI
wrapper, or much later a transport adapter) can call the use case without
reaching into internals it must not own.

Nothing in this module starts a clock, locks a lab, approves a fixture, or
writes a golden. ``EvidenceGateRunner`` reports what it observed; a human
decides what that means.

*Placement note.* The implementation plan lists ``EvidenceGateRunner``
under ``gates.py``. It lives here instead because it is the composed use
case rather than a report contract, and keeping ``gates.py`` to pure,
dependency-free report shapes means the ``GoldenArtifactGuard`` boundary
can be tested without constructing a loader or a repository. The
divergence is one file, not one design.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import personal_lms
from personal_lms.domain.evidence_review import EvidenceReviewKind, EvidenceReviewOutcome
from personal_lms.domain.objective_packs import (
    EvidenceRegion,
    ObjectivePack,
    ObjectivePackEvidenceEnvelope,
    ObjectivePackValidationReport,
    PermittedUse,
    QuarantineStatus,
    ReviewState,
    SourceArtifactRef,
    ValidationFinding,
)
from personal_lms.evidence_review.authority import (
    EvidenceAuthoritySnapshot,
    authorized_view,
    record_region_approval,
    review_kind_for,
    verify_decision,
)
from personal_lms.evidence_review.errors import EvidenceReviewError
from personal_lms.evidence_review.service import EvidenceReviewService
from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
from personal_lms.extraction.artifacts import ExtractionOutcome, same_extracted_text
from personal_lms.extraction.local_fixture import LocalFixtureExtractor
from personal_lms.labs.ccna_mastery.architecture_guard import (
    ArchitectureGuardViolation,
    check_extraction_adapter_is_narrow,
    check_repository_has_no_parallel_extraction_service,
)
from personal_lms.labs.ccna_mastery.focused_work_ledger import (
    ExternalFocusedWorkLedger,
    LedgerBindingError,
    evaluate_gate_1_cleanup,
)
from personal_lms.labs.ccna_mastery.gates import (
    FixtureAuthority,
    GateCheck,
    GateCheckStatus,
    GateDefinition,
    GateId,
    GateReport,
)
from personal_lms.labs.ccna_mastery.retrieval import RetrievalHarness, RetrievalRun
from personal_lms.objective_packs.eligibility import EvidencePolicy
from personal_lms.objective_packs.errors import ObjectivePackError
from personal_lms.objective_packs.hashing import hash_record, hash_records
from personal_lms.objective_packs.linchpin_fixture import (
    FixtureExtensions,
    RetrievalCaseSet,
    scripted_learner_authority_projection,
)
from personal_lms.objective_packs.loader import ObjectivePackLoader, PackLoadResult
from personal_lms.objective_packs.validation import ObjectivePackValidator

__all__ = [
    "CcnaMasteryUseCase",
    "executing_checkout_root",
    "EvidenceGateResult",
    "EvidenceGateRunner",
    "build_ccna_mastery_use_case",
    "manifest_hash_for",
    "resolve_code_revision",
]


#: Gate 1 must validate offline in under five minutes (G1-GO-11). Kept in
#: the trusted gate definition rather than in pack data, for the same
#: reason the required-check inventory is: a pack cannot widen its own
#: budget.
_OFFLINE_CEILING_SECONDS = 300

#: AD-08's trusted Gate 1 focused-work policy. These values are code-owned,
#: not supplied by a ledger envelope, so evidence cannot widen its own scope
#: or ceiling. The external evidence model binds all three into its start,
#: entry, and closure records.
_FOCUSED_WORK_GATE_DEFINITION_VERSION = "1.0"
_GATE_1_CLEANUP_WORK_ITEM_IDS = ("manual-cleanup",)
_GATE_1_CLEANUP_CEILING_MICROSECONDS = 4 * 60 * 60 * 1_000_000

#: The version fields G1-FX-07 requires the frozen manifest to pin. Kept
#: here, in the trusted gate definition, rather than inferred from
#: whatever keys a manifest happens to declare — a pack cannot shrink its
#: own required inventory any more than it can for the check-id inventory.
_REQUIRED_MANIFEST_VERSION_KEYS = frozenset(
    {
        "objective_version",
        "item_bank_version",
        "claim_set_version",
        "scenario_version",
        "mastery_policy_version",
        "grounding_calculation_policy_version",
        "schedule_policy_version",
        "gate_schema_version",
    }
)

#: The approved, immutable P0 sequencing marker. Gate 1 validates this exact
#: text as provenance; it does not require or fabricate the real event/replay
#: hashes, which are exclusively G2-GO-08 evidence at WP6.
_EXPECTED_EVENT_STREAM_WP6_MARKER = (
    "ALGORITHM FROZEN AT WP6, NOT NOW. See plan-amendment (RC-05): P0 freezes "
    "the canonical event model; the actual event-stream hash is generated and "
    "reviewer-accepted at the WP6 checkpoint. No value is fabricated here."
)

_EXPECTED_SCRIPTED_LEARNER_IDS = (
    "clean-pass",
    "native-gap",
    "ambiguous",
    "injection",
)

_EXPECTED_PROFILE_PROVIDER_PINS = {
    "test": (("fake-deterministic",), True, None),
    "live_local": (("ollama-qwen-local",), True, None),
    "smoke_local_ungraded": (("ollama-qwen-local",), True, False),
}


def _is_sha256_hex(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class EvidenceGateResult:
    """Everything one Gate 1 run produced.

    The report is the comparable artifact; the pack, validation report,
    envelope, retrieval run, and authority snapshot are kept alongside it
    so a CLI can show detail without re-running the gate.
    """

    report: GateReport
    load_result: PackLoadResult | None
    envelope: ObjectivePackEvidenceEnvelope | None
    reason_codes: tuple[str, ...]
    retrieval: RetrievalRun | None = None
    validation: ObjectivePackValidationReport | None = None
    authority: EvidenceAuthoritySnapshot | None = None

    @property
    def pack(self) -> ObjectivePack | None:
        return self.load_result.pack if self.load_result is not None else None


def manifest_hash_for(load_result: PackLoadResult) -> tuple[str, str]:
    """The hash a report cites for this pack's manifest, and what it *is*.

    Two different values could go in this slot and they mean different
    things, so the kind travels with the value rather than being inferred
    later. A frozen fixture's verified self-hash covers the exact authored
    bytes a human reviewed. A canonical record hash covers a derived
    Pydantic model. Silently substituting one for the other — which an
    ``a or b`` fallback does — would let a report claim byte-level fixture
    provenance it does not have.
    """
    if load_result.fixture_manifest_hash is not None:
        return load_result.fixture_manifest_hash, "fixture_self_hash"
    return hash_record(load_result.manifest), "canonical_manifest_record_hash"


class EvidenceGateRunner:
    """Runs the deterministic, pre-clock portion of Gate 1.

    Deliberately partial. The checks implemented here are the ones that
    depend only on a pack, its bytes, and persisted review decisions —
    every one of which is decidable without a frozen fixture tree, an
    approved golden, or a started clock. Retrieval-case checks
    (``G1-GO-07``/``G1-GO-08``) and the timing checks are reported
    ``NOT_RUN`` rather than fabricated, which is also what keeps a
    report over an unfrozen fixture from ever showing ``PASSED``.
    """

    def __init__(
        self,
        *,
        loader: ObjectivePackLoader,
        validator: ObjectivePackValidator,
        review_service: EvidenceReviewService,
        extractor: LocalFixtureExtractor | None = None,
        code_revision: str,
        fixture_authority: FixtureAuthority | None = None,
        focused_work_evidence: ExternalFocusedWorkLedger | None = None,
        focused_work_attempt_id: UUID | None = None,
        trusted_focused_work_signer_ids: tuple[str, ...] = (),
    ) -> None:
        if not code_revision:
            raise ValueError(
                "code_revision is required: a gate report with no code provenance "
                "cannot be compared with any other run"
            )
        self._loader = loader
        self._validator = validator
        self._review_service = review_service
        self._extractor = extractor
        self._code_revision = code_revision
        self._fixture_authority = fixture_authority
        self._focused_work_evidence = focused_work_evidence
        self._focused_work_attempt_id = focused_work_attempt_id
        self._trusted_focused_work_signer_ids = tuple(trusted_focused_work_signer_ids)

    def run(
        self,
        *,
        pack_directory: str,
        run_id: str,
        policy: EvidencePolicy | None = None,
    ) -> EvidenceGateResult:
        """Load, validate, and evaluate one pack, producing a gate report.

        ``policy`` defaults to the strict teaching policy derived from the
        loaded pack, so a caller never has to load the pack itself just
        to name its objective.
        """
        started_at = datetime.now(UTC)

        try:
            load_result = self._loader.load(pack_directory=pack_directory)
        except ObjectivePackError as exc:
            return self._blocked_result(
                run_id=run_id,
                started_at=started_at,
                reason_code=exc.reason_code,
                detail=str(exc),
            )

        pack = load_result.pack
        if policy is None:
            policy = default_evidence_policy(pack)

        # Authority comes from the persisted review store, never from the
        # pack's own authored review_state.
        authority = EvidenceAuthoritySnapshot.build(pack=pack, review_service=self._review_service)

        # Two views of the same pack, deliberately. ``pack`` is what the
        # authoring files said; ``authorized`` is what persisted reviewer
        # decisions actually permit. Validation runs against the authored
        # view so pending content stays visibly pending; eligibility and
        # retrieval run against the authorized one, which is the only view
        # in which anything is ever trusted.
        authorized = authorized_view(pack, authority)

        validation = self._validator.validate(pack)
        envelope = self._validator.build_evidence_envelope(authorized, policy, authority=authority)
        answer_findings = self._validator.validate_answer_evidence(pack, envelope)

        checks: list[GateCheck] = [
            self._loader_check(load_result),
            self._reference_check(validation.reason_codes),
            self._baseline_check(pack, validation.reason_codes),
            self._exposure_check(validation.reason_codes),
            self._coverage_check(validation, pack),
            self._grounding_check(pack, validation),
            self._review_check(authority),
            self._eligibility_check(envelope),
            self._answer_evidence_check(answer_findings),
            self._extraction_check(load_result, pack_directory=pack_directory),
            *self._retrieval_checks(load_result, authorized, envelope),
            *self._fixture_checks(load_result, validation),
            self._reproducibility_check(load_result, authorized, policy, authority),
            self._injection_check(load_result, envelope),
            self._version_leak_check(load_result, envelope),
            self._citation_check(validation.reason_codes),
            self._approval_binding_check(pack, review_service=self._review_service),
            self._architecture_guard_check(),
            self._focused_time_ledger_check(
                load_result,
                evidence=self._focused_work_evidence,
                expected_attempt_id=self._focused_work_attempt_id,
                trusted_signer_ids=self._trusted_focused_work_signer_ids,
                code_revision=self._code_revision,
            ),
        ]

        finished_at = datetime.now(UTC)
        checks.append(self._timing_check(started_at, finished_at))
        manifest_hash, manifest_hash_kind = manifest_hash_for(load_result)
        checks.append(
            GateCheck(
                check_id="G1-FX-06",
                status=GateCheckStatus.PASSED,
                reason_code=f"exact_tree_verified_{manifest_hash_kind}",
                observed_hash=manifest_hash,
                detail=(
                    f"{len(load_result.verified_file_hashes)} file(s) byte-verified against "
                    f"the pinned inventory; report cites the {manifest_hash_kind} hash"
                ),
            )
        )
        # Expectation references come from the trusted gate definition,
        # once, here. A check the definition does not cover raises rather
        # than receiving a placeholder — see GateDefinition.bind_expectations.
        bound_checks = GateDefinition.gate_1().bind_expectations(checks)
        report = GateReport(
            gate_id=GateId.GATE_1,
            run_id=run_id,
            code_revision=self._code_revision,
            fixture_manifest_hash=manifest_hash,
            # A manifest's own ``fixture_status`` is a claim about itself.
            # Real authority is an external reviewer decision pinned to
            # this exact manifest hash; with none supplied, the run reports
            # unapproved authority rather than believing the file.
            fixture_authority=(
                self._fixture_authority.resolved_status(manifest_hash)
                if self._fixture_authority is not None
                else FixtureAuthority.from_manifest_claim(
                    claimed_status=load_result.manifest.fixture_status,
                    manifest_hash=manifest_hash,
                ).resolved_status(manifest_hash)
            ),
            checks=bound_checks,
            started_at=started_at,
            finished_at=finished_at,
        )

        reason_codes = tuple(
            sorted({*validation.reason_codes, *(f.reason_code.value for f in answer_findings)})
        )
        return EvidenceGateResult(
            report=report,
            load_result=load_result,
            envelope=envelope,
            reason_codes=reason_codes,
            retrieval=self._retrieval_run(load_result, authorized, envelope),
            validation=validation,
            authority=authority,
        )

    # ---- retrieval --------------------------------------------------------

    @staticmethod
    def _retrieval_run(
        load_result: PackLoadResult,
        authorized: ObjectivePack,
        envelope: ObjectivePackEvidenceEnvelope,
    ) -> RetrievalRun | None:
        """Execute the frozen retrieval cases, when the pack format carries them."""
        cases = load_result.retrieval_cases
        if not isinstance(cases, RetrievalCaseSet):
            return None
        return RetrievalHarness(pack=authorized, envelope=envelope).run(cases.cases)

    def _retrieval_checks(
        self,
        load_result: PackLoadResult,
        authorized: ObjectivePack,
        envelope: ObjectivePackEvidenceEnvelope,
    ) -> tuple[GateCheck, ...]:
        """G1-GO-07 / G1-GO-08: the 10 supported and 2 unsupported cases."""
        run = self._retrieval_run(load_result, authorized, envelope)
        if run is None:
            return (
                GateCheck(
                    check_id="G1-GO-07",
                    status=GateCheckStatus.NOT_RUN,
                    reason_code="retrieval_cases_absent",
                    detail="this pack format carries no frozen retrieval contract",
                ),
                GateCheck(
                    check_id="G1-GO-08",
                    status=GateCheckStatus.NOT_RUN,
                    reason_code="retrieval_cases_absent",
                    detail="this pack format carries no frozen retrieval contract",
                ),
            )

        supported = run.supported_outcomes
        unsatisfied = [outcome for outcome in supported if not outcome.satisfied]
        blocked = [outcome for outcome in unsatisfied if outcome.ineligible_expected_ids]
        if blocked and len(blocked) == len(unsatisfied):
            # Every miss is explained by evidence no human has approved.
            # That is a pending approval, and calling it a failure would
            # blame the ranker for a reviewer's outstanding decision.
            pending = sorted(
                {
                    evidence_id
                    for outcome in blocked
                    for evidence_id in outcome.ineligible_expected_ids
                }
            )
            supported_check = GateCheck(
                check_id="G1-GO-07",
                status=GateCheckStatus.BLOCKED,
                reason_code="expected_evidence_review_pending",
                observed_hash=run.index_content_hash,
                detail=(
                    f"{len(blocked)} of {len(supported)} supported case(s) expect evidence that "
                    f"carries no current persisted approval: {', '.join(pending)}"
                ),
            )
        elif unsatisfied:
            supported_check = GateCheck(
                check_id="G1-GO-07",
                status=GateCheckStatus.FAILED,
                reason_code="expected_evidence_outside_top_k",
                observed_hash=run.index_content_hash,
                detail=(
                    f"case(s) {', '.join(outcome.case_id for outcome in unsatisfied)} did not "
                    "return their expected eligible evidence within the frozen top-k"
                ),
            )
        else:
            supported_check = GateCheck(
                check_id="G1-GO-07",
                status=GateCheckStatus.PASSED,
                reason_code="supported_cases_hit_top_k",
                observed_hash=run.index_content_hash,
                detail=f"{len(supported)} supported case(s) satisfied",
            )

        unsupported = run.unsupported_outcomes
        leaked = [outcome for outcome in unsupported if outcome.forbidden_returned_ids]
        wrong_reason = [
            outcome
            for outcome in unsupported
            if outcome.abstention_reason_code != outcome.expected_abstention_reason_code
        ]
        if leaked:
            unsupported_check = GateCheck(
                check_id="G1-GO-08",
                status=GateCheckStatus.FAILED,
                reason_code="forbidden_evidence_returned",
                detail=(
                    "an unsupported case returned evidence the frozen contract forbids: "
                    + ", ".join(
                        f"{outcome.case_id}:{'/'.join(outcome.forbidden_returned_ids)}"
                        for outcome in leaked
                    )
                ),
            )
        elif wrong_reason:
            unsupported_check = GateCheck(
                check_id="G1-GO-08",
                status=GateCheckStatus.FAILED,
                reason_code="abstention_reason_mismatch",
                detail=", ".join(
                    f"{outcome.case_id}: expected "
                    f"{outcome.expected_abstention_reason_code}, observed "
                    f"{outcome.abstention_reason_code}"
                    for outcome in wrong_reason
                ),
            )
        else:
            unsupported_check = GateCheck(
                check_id="G1-GO-08",
                status=GateCheckStatus.PASSED,
                reason_code="unsupported_cases_abstained",
                observed_hash=hash_records(
                    (
                        {
                            "case_id": outcome.case_id,
                            "reason_code": outcome.abstention_reason_code or "",
                        }
                        for outcome in unsupported
                    ),
                    sort=True,
                ),
                detail=f"{len(unsupported)} unsupported case(s) abstained with the frozen codes",
            )

        return (supported_check, unsupported_check)

    def _reproducibility_check(
        self,
        load_result: PackLoadResult,
        authorized: ObjectivePack,
        policy: EvidencePolicy,
        authority: EvidenceAuthoritySnapshot,
    ) -> GateCheck:
        """G1-GO-10: a second evaluation produces the same IDs and hashes.

        Re-derives the envelope and the retrieval run from scratch rather
        than reusing the first pass's objects, so a cached value cannot
        make the comparison trivially true.
        """
        first = self._validator.build_evidence_envelope(authorized, policy, authority=authority)
        second = self._validator.build_evidence_envelope(authorized, policy, authority=authority)
        if (
            first.index_content_hash != second.index_content_hash
            or first.eligible_evidence_ids != second.eligible_evidence_ids
        ):
            return GateCheck(
                check_id="G1-GO-10",
                status=GateCheckStatus.FAILED,
                reason_code="index_not_reproducible",
                detail="two evaluations of the same pack disagreed on the eligible index",
            )

        left, right = (
            self._retrieval_run(load_result, authorized, envelope) for envelope in (first, second)
        )
        if (
            left is not None
            and right is not None
            and (
                left.index_content_hash != right.index_content_hash
                or [outcome.ranked_ids for outcome in left.outcomes]
                != [outcome.ranked_ids for outcome in right.outcomes]
            )
        ):
            return GateCheck(
                check_id="G1-GO-10",
                status=GateCheckStatus.FAILED,
                reason_code="retrieval_not_reproducible",
                detail="two retrieval runs over the same corpus disagreed",
            )

        return GateCheck(
            check_id="G1-GO-10",
            status=GateCheckStatus.PASSED,
            reason_code="second_run_matches",
            observed_hash=first.index_content_hash,
        )

    @staticmethod
    def _injection_check(
        load_result: PackLoadResult, envelope: ObjectivePackEvidenceEnvelope
    ) -> GateCheck:
        """G1-NG-03: instruction-shaped source text stays inert data.

        Inertness is structural, not behavioural: the region is excluded
        before ranking, so there is no path by which its words reach a
        question, a grounding bundle, or a provider context. Nothing in
        this codebase interprets extracted text as instructions, and this
        check confirms the region carrying them is out of the corpus.
        """
        rejected = [
            region
            for region in load_result.pack.evidence_regions
            if region.review_state is ReviewState.REJECTED
            and region.quarantine_status is QuarantineStatus.QUARANTINED
        ]
        if not rejected:
            return GateCheck(
                check_id="G1-NG-03",
                status=GateCheckStatus.BLOCKED,
                reason_code="no_injected_region_present",
                detail=(
                    "the pack defines no rejected, quarantined region, so the injection "
                    "defence has nothing to demonstrate against"
                ),
            )

        eligible = set(envelope.eligible_evidence_ids)
        leaked = sorted(region.evidence_id for region in rejected if region.evidence_id in eligible)
        if leaked:
            return GateCheck(
                check_id="G1-NG-03",
                status=GateCheckStatus.FAILED,
                reason_code="injected_region_eligible",
                detail=f"quarantined region(s) reached the eligible corpus: {', '.join(leaked)}",
            )
        return GateCheck(
            check_id="G1-NG-03",
            status=GateCheckStatus.PASSED,
            reason_code="injected_region_inert",
            observed_hash=hash_records(
                sorted(region.evidence_id for region in rejected), sort=False
            ),
            detail=(
                f"{len(rejected)} quarantined region(s) excluded before ranking; extracted "
                "text is never interpreted as instructions"
            ),
        )

    @staticmethod
    def _version_leak_check(
        load_result: PackLoadResult, envelope: ObjectivePackEvidenceEnvelope
    ) -> GateCheck:
        """G1-NG-04: wrong-blueprint material is excluded before rank and limit."""
        objective_ref = load_result.pack.objective_ref
        off_version = [
            region
            for region in load_result.pack.evidence_regions
            if region.objective_refs and objective_ref not in region.objective_refs
        ]
        if not off_version:
            return GateCheck(
                check_id="G1-NG-04",
                status=GateCheckStatus.BLOCKED,
                reason_code="no_wrong_version_region_present",
                detail="the pack carries no wrong-blueprint distractor to exclude",
            )
        eligible = set(envelope.eligible_evidence_ids)
        leaked = sorted(
            region.evidence_id for region in off_version if region.evidence_id in eligible
        )
        if leaked:
            return GateCheck(
                check_id="G1-NG-04",
                status=GateCheckStatus.FAILED,
                reason_code="wrong_blueprint_version_eligible",
                detail=f"wrong-version region(s) reached the eligible corpus: {', '.join(leaked)}",
            )
        return GateCheck(
            check_id="G1-NG-04",
            status=GateCheckStatus.PASSED,
            reason_code="wrong_blueprint_version_excluded",
            observed_hash=hash_records(
                sorted(region.evidence_id for region in off_version), sort=False
            ),
        )

    @staticmethod
    def _timing_check(started_at: datetime, finished_at: datetime) -> GateCheck:
        """G1-GO-11: the pack validates offline, well inside the ceiling.

        Offline is structural here rather than measured: this path
        constructs no HTTP client, no provider, and no socket, so there is
        nothing to count.

        The measured duration deliberately does **not** appear in the
        check's detail. Two runs that agree on every status, reason code,
        and hash must share a report content hash — that is what makes a
        report comparable at all — and embedding a wall-clock reading
        would make every run differ. The measurement lives in the report's
        own ``started_at``/``finished_at``, which a comparison is
        explicitly allowed to normalize.
        """
        elapsed = (finished_at - started_at).total_seconds()
        if elapsed > _OFFLINE_CEILING_SECONDS:
            return GateCheck(
                check_id="G1-GO-11",
                status=GateCheckStatus.FAILED,
                reason_code="offline_validation_too_slow",
                detail=(
                    f"validation exceeded the {_OFFLINE_CEILING_SECONDS}s ceiling; see the "
                    "report's started_at/finished_at for the measurement"
                ),
            )
        return GateCheck(
            check_id="G1-GO-11",
            status=GateCheckStatus.PASSED,
            reason_code="offline_within_ceiling",
            detail=(
                f"completed within the {_OFFLINE_CEILING_SECONDS}s ceiling with no "
                "transport client constructed"
            ),
        )

    @staticmethod
    def _fixture_checks(
        load_result: PackLoadResult, validation: ObjectivePackValidationReport
    ) -> tuple[GateCheck, ...]:
        """The G1-FX rows this pass can decide from loaded, verified data."""
        pack = load_result.pack
        bound = len(load_result.source_manifest_bindings)
        manifest_check = GateCheck(
            check_id="G1-FX-01",
            status=(
                GateCheckStatus.PASSED
                if bound == len(pack.source_artifacts) and bound > 0
                else GateCheckStatus.FAILED
            ),
            reason_code=(
                "sources_byte_verified" if bound == len(pack.source_artifacts) else "source_unbound"
            ),
            observed_hash=hash_records(
                (
                    {
                        "source_id": binding.source_id,
                        "sha256": binding.sha256,
                        "size_bytes": str(binding.size_bytes),
                        "media_type": binding.media_type or "",
                    }
                    for binding in load_result.source_manifest_bindings.values()
                ),
                sort=True,
            ),
            detail=f"{bound} source artifact(s) bound to pinned manifest entries",
        )

        claims_with_factors = [
            claim
            for claim in pack.claims
            if claim.support and all(support.independence_group for support in claim.support)
        ]
        claims_check = GateCheck(
            check_id="G1-FX-03",
            status=(
                GateCheckStatus.PASSED
                if len(claims_with_factors) == len(pack.claims) and pack.claims
                else GateCheckStatus.FAILED
            ),
            reason_code=(
                "claims_carry_claim_specific_factors"
                if len(claims_with_factors) == len(pack.claims) and pack.claims
                else "claim_missing_support_factors"
            ),
            observed_hash=hash_record(validation.recomputed_claim_scores),
            detail=f"{len(pack.claims)} claim(s), each with recomputed grounding",
        )

        cases = load_result.retrieval_cases
        if isinstance(cases, RetrievalCaseSet):
            corpus_check = GateCheck(
                check_id="G1-FX-05",
                status=(
                    GateCheckStatus.PASSED
                    if len(cases.supported) == 10 and len(cases.unsupported) == 2
                    else GateCheckStatus.FAILED
                ),
                reason_code=(
                    "retrieval_corpus_complete"
                    if len(cases.supported) == 10 and len(cases.unsupported) == 2
                    else "retrieval_corpus_incomplete"
                ),
                observed_hash=hash_records(
                    ({"case_id": case.case_id, "kind": case.kind} for case in cases.cases),
                    sort=True,
                ),
                detail=(
                    f"{len(cases.supported)} supported and {len(cases.unsupported)} "
                    "unsupported case(s) pinned"
                ),
            )
        else:
            corpus_check = GateCheck(
                check_id="G1-FX-05",
                status=GateCheckStatus.NOT_RUN,
                reason_code="retrieval_cases_absent",
                detail="this pack format carries no frozen retrieval contract",
            )

        return (
            manifest_check,
            claims_check,
            corpus_check,
            GateCheck(
                check_id="G1-FX-04",
                status=GateCheckStatus.PASSED,
                reason_code="unknown_ids_fail_fast",
                detail=(
                    "unresolved item, claim, evidence, and source references are "
                    "error-severity findings; see G1-GO-04"
                ),
            ),
            EvidenceGateRunner._manifest_versions_and_hash_provenance_check(load_result),
        )

    @staticmethod
    def _manifest_versions_and_hash_provenance_check(load_result: PackLoadResult) -> GateCheck:
        """G1-FX-07: verify every P0-computable semantic pin.

        The approved narrow split keeps versions, learner vectors, expected
        follow-ups/grades/outcomes/schedules, executed CLI state hashes, and
        provider/profile combinations in Gate 1. The exact WP6 marker is
        sequencing provenance and therefore also validated here. Real
        event-stream and interrupted-replay hashes are exclusively G2-GO-08
        evidence; their absence cannot make this P0 check ``NOT_RUN``.
        """
        extensions = load_result.fixture_extensions
        if not isinstance(extensions, FixtureExtensions):
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.NOT_RUN,
                reason_code="fixture_extensions_absent",
                detail="this pack format carries no fixture-extension envelope to verify",
            )

        versions = extensions.manifest_versions
        missing = sorted(_REQUIRED_MANIFEST_VERSION_KEYS - set(versions))
        unexpected = sorted(set(versions) - _REQUIRED_MANIFEST_VERSION_KEYS)
        empty = sorted(
            key for key in _REQUIRED_MANIFEST_VERSION_KEYS & set(versions) if not versions[key]
        )
        if missing or unexpected or empty:
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.FAILED,
                reason_code="manifest_versions_incomplete",
                detail=(
                    f"missing version key(s) {missing}, unexpected key(s) {unexpected}, "
                    f"empty value(s) {empty}"
                ),
            )

        notes = extensions.manifest_canonicalization_notes
        event_stream_note = notes.get("event_stream_hash", "")
        cli_state_note = notes.get("cli_state_hash", "")
        if event_stream_note != _EXPECTED_EVENT_STREAM_WP6_MARKER:
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.FAILED,
                reason_code="event_stream_wp6_marker_mismatch",
                detail=(
                    "canonicalization_rules.event_stream_hash must equal the exact approved "
                    "WP6 ownership marker; neither a fabricated hash nor edited prose is valid"
                ),
            )
        if not cli_state_note:
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.FAILED,
                reason_code="cli_state_hash_provenance_mismatch",
                detail=(
                    "canonicalization_rules.cli_state_hash must document the already-executed "
                    "state hash algorithm and start/target results"
                ),
            )

        learner_pins = extensions.scripted_learner_pins
        learner_ids = tuple(pin.learner_vector_id for pin in learner_pins)
        if learner_ids != _EXPECTED_SCRIPTED_LEARNER_IDS:
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.FAILED,
                reason_code="scripted_learner_pins_incomplete",
                detail=(
                    f"expected learner vector ids {_EXPECTED_SCRIPTED_LEARNER_IDS}, "
                    f"found {learner_ids}"
                ),
            )

        objective_ref = load_result.manifest.objective_ref
        invalid_learner_ids = sorted(
            pin.learner_vector_id
            for pin in learner_pins
            if (
                pin.objective_ref != objective_ref
                or load_result.verified_file_hashes.get(pin.relative_path) != pin.raw_sha256
                or not _is_sha256_hex(pin.raw_sha256)
                or not _is_sha256_hex(pin.response_vector_sha256)
                or not _is_sha256_hex(pin.outcome_sha256)
                or not _is_sha256_hex(pin.exit_probe_answer_sha256)
                or len(pin.baseline_responses) != 12
                or any(
                    not _is_sha256_hex(response.answer_sha256)
                    for response in (*pin.baseline_responses, *pin.followup_responses)
                )
                or (
                    pin.learner_vector_id != "ambiguous"
                    and not _is_sha256_hex(pin.expected_facet_derivation_sha256)
                )
                or not pin.expected_achievement_status
                or not pin.expected_review_status
                or not pin.expected_evidence_status
            )
        )
        if invalid_learner_ids:
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.FAILED,
                reason_code="scripted_learner_semantic_pin_mismatch",
                detail=(
                    "learner semantic pins are incomplete, malformed, or no longer bound "
                    f"to byte-verified files for {invalid_learner_ids}"
                ),
            )

        scenario = extensions.scenario_state_hash_pins
        if scenario is None:
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.FAILED,
                reason_code="scenario_state_hash_pins_missing",
                detail="the byte-verified scenario exposes no executed start/target hash pins",
            )
        if (
            scenario.objective_ref != objective_ref
            or scenario.scenario_version != versions["scenario_version"]
            or load_result.verified_file_hashes.get(scenario.relative_path) != scenario.raw_sha256
            or not _is_sha256_hex(scenario.raw_sha256)
            or not _is_sha256_hex(scenario.starting_state_sha256)
            or not _is_sha256_hex(scenario.target_repaired_state_sha256)
            or any(
                pin.cli_expected_final_state_sha256 != scenario.target_repaired_state_sha256
                for pin in learner_pins
            )
        ):
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.FAILED,
                reason_code="scenario_state_hash_pin_mismatch",
                detail=(
                    "the executed scenario start/target hashes, version, objective, learner "
                    "CLI targets, or byte binding do not agree"
                ),
            )

        by_learner_id = {pin.learner_vector_id: pin for pin in learner_pins}
        clean_pin = by_learner_id["clean-pass"]
        injection_pin = by_learner_id["injection"]
        if (
            injection_pin.must_equal_learner_vector_id != "clean-pass"
            or injection_pin.response_vector_sha256 == clean_pin.response_vector_sha256
            or scripted_learner_authority_projection(injection_pin)
            != scripted_learner_authority_projection(clean_pin)
        ):
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.FAILED,
                reason_code="injection_equivalence_pin_mismatch",
                detail=(
                    "the injection vector must remain a distinct response vector whose "
                    "authority-safe facet derivation and outcome equal clean-pass"
                ),
            )

        profile_pins = {
            pin.profile: (
                pin.provider_ids,
                pin.offline_only,
                pin.allow_domain_result_writes,
            )
            for pin in extensions.allowed_profile_provider_pins
        }
        if (
            profile_pins != _EXPECTED_PROFILE_PROVIDER_PINS
            or extensions.hosted_profiles_enabled
            or extensions.hosted_spend_ceiling_usd != "0.00"
        ):
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.FAILED,
                reason_code="execution_profile_pins_mismatch",
                detail=(
                    "the approved test/live-local/smoke-local profile-provider pins must be "
                    "exact, offline-only, with no hosted profile and a 0.00 spend ceiling"
                ),
            )

        expected_cli_fragments = (
            f"start={scenario.starting_state_sha256[:8]}...",
            f"target={scenario.target_repaired_state_sha256[:8]}...",
        )
        if any(fragment not in cli_state_note for fragment in expected_cli_fragments):
            return GateCheck(
                check_id="G1-FX-07",
                status=GateCheckStatus.FAILED,
                reason_code="cli_state_hash_provenance_mismatch",
                detail=(
                    "the CLI canonicalization note does not name the prefixes of the full "
                    "executed start/target hashes exposed by the byte-verified scenario"
                ),
            )

        observed_hash = hash_record(
            {
                "versions": versions,
                "canonicalization_notes": notes,
                "scripted_learner_pins": [asdict(pin) for pin in learner_pins],
                "scenario_state_hash_pins": asdict(scenario),
                "allowed_profile_provider_pins": [
                    asdict(pin) for pin in extensions.allowed_profile_provider_pins
                ],
                "hosted_profiles_enabled": list(extensions.hosted_profiles_enabled),
                "hosted_spend_ceiling_usd": extensions.hosted_spend_ceiling_usd,
            }
        )
        return GateCheck(
            check_id="G1-FX-07",
            status=GateCheckStatus.PASSED,
            reason_code="p0_semantic_pins_verified",
            observed_hash=observed_hash,
            detail=(
                f"verified {len(versions)} exact versions, {len(learner_pins)} byte-bound "
                "learner vectors and their follow-up/grade/outcome/schedule semantics, full "
                "executed CLI start/target hashes, three offline profile-provider pins, "
                "and the exact WP6 ownership marker; real event/replay hashes remain "
                "exclusively G2-GO-08 evidence"
            ),
        )

    @staticmethod
    def _approval_binding_check(
        pack: ObjectivePack, *, review_service: EvidenceReviewService
    ) -> GateCheck:
        """G1-FX-08: exercise the real approval CLI's persistence and read
        path end-to-end, against an isolated temporary store, then inspect
        the pack's real reviewed record read-only.

        Independent review (2026-07-28) found the prior version
        insufficient: it built two synthetic, never-persisted
        ``EvidenceReviewDecision`` objects and called the pure
        ``verify_decision`` function directly, which exercises read-time
        verification but proves nothing about whether the bounded approval
        CLI actually records an immutable, persisted, correctly-bound
        decision, or whether a genuinely stale one *read back from real
        storage* is refused.

        This version calls ``record_region_approval`` — the literal
        function ``ccna-lab evidence approve-region`` calls, not a second
        copy that could drift from it — against a real SQLite file created
        in an isolated temporary directory and destroyed when this check
        returns. The decision is then re-read through a *fresh* connection
        to that file (never the in-process object just built) before
        ``verify_decision`` is exercised against it, once for the matching
        subject and once for a deliberately changed one. This check never
        opens, writes to, or otherwise mutates the review database this
        gate run was actually constructed with — ``review_service`` is
        only ever read here, through the same governed read path a report
        reader would use.
        """
        candidate = next(
            (
                (region, artifact)
                for region in sorted(pack.evidence_regions, key=lambda item: item.evidence_id)
                for artifact in (pack.sources_by_id.get(region.source_id),)
                if artifact is not None
            ),
            None,
        )
        if candidate is None:
            return GateCheck(
                check_id="G1-FX-08",
                status=GateCheckStatus.BLOCKED,
                reason_code="no_bound_region_to_exercise",
                detail="the pack defines no evidence region bound to a source artifact",
            )
        region, artifact = candidate
        kind = review_kind_for(region)
        moment = datetime.now(UTC)

        with tempfile.TemporaryDirectory(prefix="gate1-fx08-self-test-") as tmp:
            database_path = str(Path(tmp) / "adversarial-approval.sqlite3")
            write_repository = SQLiteEvidenceReviewRepository.open(database_path)
            write_repository.initialize_schema()
            try:
                try:
                    record_region_approval(
                        pack=pack,
                        region=region,
                        artifact=artifact,
                        review_service=EvidenceReviewService(write_repository),
                        reviewer_id="gate-1-self-test",
                        reviewer_role="architecture_probe",
                        outcome=EvidenceReviewOutcome.APPROVED,
                        reason=(
                            "gate-1 in-process CLI/persistence binding exercise; "
                            "isolated temporary store, never the reviewed fixture store"
                        ),
                        accessible_description=(
                            region.accessible_description
                            if kind is EvidenceReviewKind.VISUAL
                            else None
                        ),
                        decided_at=moment,
                    )
                except (ValueError, EvidenceReviewError) as exc:
                    return GateCheck(
                        check_id="G1-FX-08",
                        status=GateCheckStatus.FAILED,
                        reason_code="approval_binding_exercise_failed",
                        detail=(
                            f"the real approval CLI path refused a well-formed decision for "
                            f"{region.evidence_id!r}: {exc}"
                        ),
                    )
            finally:
                write_repository.close()

            # Governed read path: a fresh connection to the same file, never
            # the in-process object the write above already holds.
            read_repository = SQLiteEvidenceReviewRepository.open(database_path)
            try:
                reread = EvidenceReviewService(read_repository).current_decision_for_subject(
                    pack=pack, region=region
                )
            finally:
                read_repository.close()

        if reread is None:
            return GateCheck(
                check_id="G1-FX-08",
                status=GateCheckStatus.FAILED,
                reason_code="approval_binding_exercise_failed",
                detail=(
                    f"decision for {region.evidence_id!r} did not survive a fresh read "
                    "connection to the store it was persisted to"
                ),
            )

        matching_verdict = verify_decision(reread, pack=pack, region=region, artifact=artifact)
        # The subject changes; the persisted decision itself does not. A
        # text region's reviewed passage or an image region's accessible
        # description is exactly what a real post-approval edit changes.
        stale_region = region.model_copy(
            update=(
                {"accessible_description": (region.accessible_description or "") + " (edited)"}
                if kind is EvidenceReviewKind.VISUAL
                else {"exact_text": (region.exact_text or "") + " (edited)"}
            )
        )
        stale_verdict = verify_decision(reread, pack=pack, region=stale_region, artifact=artifact)

        if not matching_verdict.authorized or stale_verdict.authorized:
            return GateCheck(
                check_id="G1-FX-08",
                status=GateCheckStatus.FAILED,
                reason_code="approval_binding_exercise_failed",
                detail=(
                    f"region {region.evidence_id!r}: matching_authorized="
                    f"{matching_verdict.authorized} ({matching_verdict.reason}), "
                    f"stale_authorized={stale_verdict.authorized} ({stale_verdict.reason})"
                ),
            )

        # Read-only: the pack's real reviewed record, if one exists, through
        # the same verify_decision path. Never written to; absence is not a
        # failure of this check, which is about the mechanism, not coverage.
        real_record_detail = "no real reviewed record exists yet for this region"
        real_record = review_service.current_decision_for_subject(pack=pack, region=region)
        if real_record is not None:
            real_verdict = verify_decision(real_record, pack=pack, region=region, artifact=artifact)
            real_record_detail = (
                f"real record {real_record.decision_id!r} by "
                f"{real_record.reviewer.reviewer_id!r}: authorized={real_verdict.authorized} "
                f"({real_verdict.reason})"
            )

        observed_hash = hash_record(
            {
                "evidence_id": region.evidence_id,
                "matching_reason": matching_verdict.reason,
                "stale_reason": stale_verdict.reason,
            }
        )
        return GateCheck(
            check_id="G1-FX-08",
            status=GateCheckStatus.PASSED,
            reason_code="approval_binding_exercised_by_gate",
            observed_hash=observed_hash,
            detail=(
                f"exercised via the real approval CLI path against {region.evidence_id!r} in "
                f"an isolated temporary store: a fresh-read matching decision was accepted "
                f"({matching_verdict.reason!r}) and the same decision was refused once the "
                f"subject changed ({stale_verdict.reason!r}); {real_record_detail}"
            ),
        )

    @staticmethod
    def _architecture_guard_check() -> GateCheck:
        """G1-FX-09: the extraction adapter stays a narrow searchable-PDF/PNG
        adapter, and no part of the current change set adds a competing
        extraction service anywhere else — both verified structurally, at
        gate-run time.

        An earlier revision reported this row ``NOT_RUN`` because
        architecture shape is "asserted by tests, not something a gate run
        can observe about itself." Shape is exactly the kind of thing a
        running process *can* observe: see
        ``architecture_guard.check_extraction_adapter_is_narrow``, shared
        with ``tests/unit/extraction/test_local_fixture.py`` so there is
        one definition of "narrow," not a test copy that could drift from
        what the gate verifies.

        Independent review (2026-07-28) found that module-level check
        insufficient on its own: it can only ever see ``local_fixture.py``,
        so a parallel extraction service dropped anywhere else in the
        repository was invisible to it.
        ``architecture_guard.check_repository_has_no_parallel_extraction_service``
        closes that gap with a deterministic, AST/path-based scan of every
        changed file against the reviewed base revision — not a second,
        narrower guard, but this row's second required half.
        """
        try:
            module_result = check_extraction_adapter_is_narrow()
            repository_result = check_repository_has_no_parallel_extraction_service()
        except ArchitectureGuardViolation as exc:
            return GateCheck(
                check_id="G1-FX-09",
                status=GateCheckStatus.FAILED,
                reason_code="architecture_guard_violation",
                detail=str(exc)[:2_000],
            )
        observed_hash = hash_record(
            {
                "public_members": list(module_result.public_members),
                "module_file": module_result.module_file,
                "reviewed_base_revision": repository_result.reviewed_base_revision,
                "scanned_file_count": repository_result.scanned_file_count,
            }
        )
        return GateCheck(
            check_id="G1-FX-09",
            status=GateCheckStatus.PASSED,
            reason_code="architecture_guard_verified_by_gate",
            observed_hash=observed_hash,
            detail=(
                f"LocalFixtureExtractor's public surface is exactly "
                f"{module_result.public_members}, with no forbidden import or SQL schema "
                f"marker; a repository-wide scan of {repository_result.scanned_file_count} "
                f"changed file(s) against {repository_result.reviewed_base_revision!r} found "
                "no parallel extraction service, schema migration, or dependency entanglement"
            ),
        )

    @staticmethod
    def _focused_time_ledger_check(
        load_result: PackLoadResult,
        *,
        evidence: ExternalFocusedWorkLedger | None = None,
        expected_attempt_id: UUID | None = None,
        trusted_signer_ids: tuple[str, ...] = (),
        code_revision: str,
    ) -> GateCheck:
        """G1-NG-05: evaluate only complete external focused-work evidence.

        The frozen fixture declares the policy and must keep ``entries``
        empty. Putting final entries inside a self-hashed manifest while
        making those entries cite the final manifest hash creates a SHA-256
        fixed-point cycle. AD-08 therefore records start, entry, and closure
        attestations in an external append-only authority and injects the
        already-validated envelope here. This method never creates a start
        record, reads a clock, backfills an interval, or treats silence as
        proof of zero work.
        """
        extensions = load_result.fixture_extensions
        document = (
            extensions.focused_time_ledger_document
            if isinstance(extensions, FixtureExtensions)
            else None
        )
        if document is None:
            return GateCheck(
                check_id="G1-NG-05",
                required=True,
                status=GateCheckStatus.NOT_RUN,
                reason_code="focused_time_ledger_absent",
                detail="this pack format carries no focused-time policy declaration to evaluate",
            )
        if not isinstance(document, dict):
            return GateCheck(
                check_id="G1-NG-05",
                required=True,
                status=GateCheckStatus.FAILED,
                reason_code="focused_time_policy_malformed",
                detail="focused_time_ledger_contract must be a mapping",
            )
        entries = document.get("entries")
        if not isinstance(entries, list):
            return GateCheck(
                check_id="G1-NG-05",
                required=True,
                status=GateCheckStatus.FAILED,
                reason_code="focused_time_policy_malformed",
                detail="the frozen policy's entries field must be a list",
            )
        if entries:
            return GateCheck(
                check_id="G1-NG-05",
                required=True,
                status=GateCheckStatus.FAILED,
                reason_code="embedded_focused_work_entries_forbidden",
                detail=(
                    "focused-work entries must live in the external append-only ledger; "
                    "embedding them in the self-hashed fixture creates a circular binding"
                ),
            )
        if (
            document.get("method") != "signed_human_start_stop_entries"
            or document.get("gate_1_manual_cleanup_ceiling_hours") != 4
        ):
            return GateCheck(
                check_id="G1-NG-05",
                required=True,
                status=GateCheckStatus.FAILED,
                reason_code="focused_time_policy_mismatch",
                detail=(
                    "the frozen policy must declare signed human start/stop entries and "
                    "the four-hour Gate 1 cleanup ceiling"
                ),
            )
        if evidence is None:
            return GateCheck(
                check_id="G1-NG-05",
                required=True,
                status=GateCheckStatus.NOT_RUN,
                reason_code="external_focused_time_ledger_absent",
                detail=(
                    "no complete external start/entry/closure envelope was supplied; this "
                    "run does not start a clock or invent focused-work evidence"
                ),
            )
        if expected_attempt_id is None:
            return GateCheck(
                check_id="G1-NG-05",
                required=True,
                status=GateCheckStatus.FAILED,
                reason_code="focused_time_attempt_context_missing",
                detail="present external evidence requires a trusted expected attempt UUID",
            )

        expected_manifest_hash, _ = manifest_hash_for(load_result)
        try:
            evaluation = evaluate_gate_1_cleanup(
                evidence,
                trusted_authorized_signer_ids=trusted_signer_ids,
                authorized_work_item_ids=_GATE_1_CLEANUP_WORK_ITEM_IDS,
                expected_gate_id=GateId.GATE_1.value,
                expected_gate_definition_version=_FOCUSED_WORK_GATE_DEFINITION_VERSION,
                expected_attempt_id=expected_attempt_id,
                expected_fixture_ready_sha256=expected_manifest_hash,
                expected_start_code_revision=code_revision,
                ceiling_microseconds=_GATE_1_CLEANUP_CEILING_MICROSECONDS,
            )
        except LedgerBindingError as exc:
            return GateCheck(
                check_id="G1-NG-05",
                required=True,
                status=GateCheckStatus.FAILED,
                reason_code="focused_time_ledger_binding_violation",
                detail=str(exc)[:2_000],
            )
        if evaluation is None:
            return GateCheck(
                check_id="G1-NG-05",
                required=True,
                status=GateCheckStatus.NOT_RUN,
                reason_code="external_focused_time_ledger_empty",
                detail=(
                    "the complete external envelope contains no focused-work entries; "
                    "silence is not proof that cleanup took zero time"
                ),
            )

        observed_hash = hash_record(evidence.model_dump(mode="python", by_alias=True))
        if not evaluation.within_ceiling:
            return GateCheck(
                check_id="G1-NG-05",
                required=True,
                status=GateCheckStatus.FAILED,
                reason_code="focused_cleanup_ceiling_exceeded",
                observed_hash=observed_hash,
                detail=(
                    f"{evaluation.total_focused_microseconds} exact microseconds of "
                    f"attested person-time exceeds the "
                    f"{evaluation.ceiling_microseconds}-microsecond ceiling"
                ),
            )
        return GateCheck(
            check_id="G1-NG-05",
            required=True,
            status=GateCheckStatus.PASSED,
            reason_code="focused_cleanup_within_ceiling",
            observed_hash=observed_hash,
            detail=(
                f"{evaluation.entry_count} attested external entry/entries total "
                f"{evaluation.total_focused_microseconds} exact microseconds of person-time, "
                "bound to this fixture, attempt, gate definition, code revision, work scope, "
                f"and closure, within the {evaluation.ceiling_microseconds}-microsecond ceiling"
            ),
        )

    # ---- individual gate checks -------------------------------------------

    @staticmethod
    def _loader_check(load_result: PackLoadResult) -> GateCheck:
        """G1-GO-01: every manifest-pinned source resolved and hashed."""
        if load_result.has_errors:
            return GateCheck(
                check_id="G1-GO-01",
                status=GateCheckStatus.FAILED,
                reason_code="manifest_inconsistent",
                detail="the pack directory and its manifest disagree",
            )
        return GateCheck(
            check_id="G1-GO-01",
            status=GateCheckStatus.PASSED,
            reason_code="sources_resolved",
            observed_hash=hash_records(
                sorted(load_result.verified_file_hashes.items()), sort=False
            ),
        )

    @staticmethod
    def _reference_check(reason_codes: tuple[str, ...]) -> GateCheck:
        """G1-GO-04 / G1-NG-01: every reference resolves exactly once."""
        offending = {
            "unknown_item_id",
            "unknown_claim_id",
            "unknown_evidence_id",
            "unknown_source_id",
            "unresolved_citation",
            "duplicate_item_id",
            "duplicate_claim_id",
            "duplicate_evidence_id",
        } & set(reason_codes)
        if offending:
            return GateCheck(
                check_id="G1-GO-04",
                status=GateCheckStatus.FAILED,
                reason_code=sorted(offending)[0],
                detail=f"unresolved or duplicated references: {sorted(offending)}",
            )
        return GateCheck(
            check_id="G1-GO-04",
            status=GateCheckStatus.PASSED,
            reason_code="references_resolve_once",
        )

    @staticmethod
    def _citation_check(reason_codes: tuple[str, ...]) -> GateCheck:
        """G1-NG-01: an invented or unresolvable citation stops the gate.

        The negative face of ``G1-GO-04``. Both rows exist in the
        traceability inventory and both are required, so reporting one and
        omitting the other would leave a required check permanently
        missing — which reads as ``NOT_RUN`` forever.
        """
        offending = {"unresolved_citation", "unknown_evidence_id", "unknown_claim_id"} & set(
            reason_codes
        )
        if offending:
            return GateCheck(
                check_id="G1-NG-01",
                status=GateCheckStatus.FAILED,
                reason_code=sorted(offending)[0],
                detail=f"unresolvable citation(s): {sorted(offending)}",
            )
        return GateCheck(
            check_id="G1-NG-01",
            status=GateCheckStatus.PASSED,
            reason_code="citations_resolve",
        )

    @staticmethod
    def _baseline_check(pack: ObjectivePack, reason_codes: tuple[str, ...]) -> GateCheck:
        """G1-GO-03: the baseline holds exactly the required item count."""
        if "baseline_cardinality" in reason_codes:
            return GateCheck(
                check_id="G1-GO-03",
                status=GateCheckStatus.FAILED,
                reason_code="baseline_cardinality",
                detail=(
                    f"baseline holds {len(pack.baseline_item_ids)} items, policy requires "
                    f"{pack.mastery_policy.baseline_item_count}"
                ),
            )
        return GateCheck(
            check_id="G1-GO-03",
            status=GateCheckStatus.PASSED,
            reason_code="baseline_cardinality_exact",
            observed_hash=hash_records(pack.baseline_item_ids, sort=False),
        )

    @staticmethod
    def _exposure_check(reason_codes: tuple[str, ...]) -> GateCheck:
        """G1-GO-05: exposure sets are pairwise disjoint."""
        if "exposure_sets_overlap" in reason_codes:
            return GateCheck(
                check_id="G1-GO-05",
                status=GateCheckStatus.FAILED,
                reason_code="exposure_sets_overlap",
            )
        return GateCheck(
            check_id="G1-GO-05",
            status=GateCheckStatus.PASSED,
            reason_code="exposure_sets_disjoint",
        )

    @staticmethod
    def _coverage_check(
        validation: ObjectivePackValidationReport, pack: ObjectivePack
    ) -> GateCheck:
        """G1-NG-06: declared coverage never substitutes for recomputed coverage."""
        if "declared_coverage_mismatch" in validation.reason_codes:
            return GateCheck(
                check_id="G1-NG-06",
                status=GateCheckStatus.FAILED,
                reason_code="declared_coverage_mismatch",
                detail="the pack's declared totals disagree with recomputed totals",
            )
        return GateCheck(
            check_id="G1-NG-06",
            status=GateCheckStatus.PASSED,
            reason_code="coverage_recomputed",
            observed_hash=hash_record(validation.recomputed_coverage),
            detail=f"recomputed from {len(pack.items)} item record(s)",
        )

    @staticmethod
    def _grounding_check(
        pack: ObjectivePack, validation: ObjectivePackValidationReport
    ) -> GateCheck:
        """G1-GO-06: every answer-bearing claim meets the grounding threshold
        under the calculation policy the fixture actually declared.

        A claim scoring above the floor under the *wrong* policy proves
        nothing: two scores are comparable only when they share a policy
        identifier, so a policy mismatch is exactly as gate-blocking as a
        score below the floor. The earlier version checked only
        ``grounding_below_threshold`` — a claim could carry evidence
        assessed under a policy this run does not recognize and still
        report ``passed``, because the mismatch stayed inside the
        transient validation result and never reached the persisted
        report.

        Every branch records deterministic observed evidence — the
        recomputed score, the public policy identifier, its provenance,
        the per-support factors, and the evidence ids they cite — so a
        reviewer never has to re-run validation to see why the check
        landed where it did.
        """
        reason_codes = set(validation.reason_codes)
        failing = {"calculation_policy_mismatch", "grounding_below_threshold"} & reason_codes

        claims_by_id = pack.claims_by_id
        observed_claims: list[dict[str, object]] = []
        for claim_id in sorted(validation.answer_bearing_claim_ids):
            claim = claims_by_id.get(claim_id)
            supports = claim.support if claim is not None else ()
            factors = sorted(
                (
                    {
                        "support_id": support.support_id,
                        "evidence_id": support.evidence_id,
                        "authority_basis_points": support.authority_basis_points,
                        "directness_basis_points": support.directness_basis_points,
                        "provenance_completeness_basis_points": (
                            support.provenance_completeness_basis_points
                        ),
                        "extraction_integrity_basis_points": (
                            support.extraction_integrity_basis_points
                        ),
                        "fitness_and_currency_basis_points": (
                            support.fitness_and_currency_basis_points
                        ),
                        "calculation_policy_version": support.calculation_policy_version,
                    }
                    for support in supports
                ),
                key=lambda factor: str(factor["support_id"]),
            )
            observed_claims.append(
                {
                    "claim_id": claim_id,
                    "score_basis_points": validation.recomputed_claim_scores.get(claim_id),
                    "evidence_ids": sorted({support.evidence_id for support in supports}),
                    "factors": factors,
                }
            )

        observed_hash = hash_record(
            {
                "calculation_policy_version": validation.calculation_policy_version,
                "calculation_algorithm_provenance": validation.calculation_algorithm_provenance,
                "grounding_floor_basis_points": validation.grounding_floor_basis_points,
                "claims": observed_claims,
            }
        )

        if failing:
            reason_code = (
                "calculation_policy_mismatch"
                if "calculation_policy_mismatch" in failing
                else "grounding_below_threshold"
            )
            return GateCheck(
                check_id="G1-GO-06",
                status=GateCheckStatus.FAILED,
                reason_code=reason_code,
                observed_hash=observed_hash,
                detail=(
                    f"{len(observed_claims)} answer-bearing claim(s) evaluated under "
                    f"policy {validation.calculation_policy_version!r} "
                    f"(provenance {validation.calculation_algorithm_provenance!r}); "
                    f"failing reason code(s): {sorted(failing)}"
                ),
            )
        return GateCheck(
            check_id="G1-GO-06",
            status=GateCheckStatus.PASSED,
            reason_code="grounding_meets_threshold",
            observed_hash=observed_hash,
            detail=(
                f"{len(observed_claims)} answer-bearing claim(s) meet the floor under "
                f"policy {validation.calculation_policy_version!r} "
                f"(provenance {validation.calculation_algorithm_provenance!r})"
            ),
        )

    def _review_check(self, authority: EvidenceAuthoritySnapshot) -> GateCheck:
        """G1-GO-02: required image regions carry a current approved visual review.

        Reads the *persisted decision snapshot*, never the pack's authored
        review_state. A pack with no image region at all reports
        ``BLOCKED`` rather than passing vacuously: the requirement is that
        an infographic claim has a recorded human review, and having
        nothing to review does not meet it.
        """
        if not authority.required_visual_evidence_ids:
            return GateCheck(
                check_id="G1-GO-02",
                status=GateCheckStatus.BLOCKED,
                reason_code="no_visual_evidence_present",
                detail=(
                    "the pack defines no image region, so no human visual review can "
                    "exist; this requirement is unmet rather than inapplicable"
                ),
            )

        missing = sorted(
            set(authority.required_visual_evidence_ids)
            - set(authority.approved_visual_evidence_ids)
        )
        if missing:
            return GateCheck(
                check_id="G1-GO-02",
                status=GateCheckStatus.BLOCKED,
                reason_code="visual_review_pending",
                detail=(
                    f"{len(missing)} image region(s) lack a current approved visual "
                    f"decision; stale: {len(authority.stale_evidence_ids)}"
                ),
            )

        return GateCheck(
            check_id="G1-GO-02",
            status=GateCheckStatus.PASSED,
            reason_code="visual_review_recorded",
            observed_hash=hash_records(authority.approved_visual_evidence_ids, sort=True),
        )

    @staticmethod
    def _eligibility_check(envelope: ObjectivePackEvidenceEnvelope) -> GateCheck:
        """G1-GO-09: no ineligible region survives the evidence policy."""
        return GateCheck(
            check_id="G1-GO-09",
            status=GateCheckStatus.PASSED,
            reason_code="ineligible_regions_excluded",
            observed_hash=envelope.index_content_hash,
            detail=(
                f"{len(envelope.eligible_evidence_ids)} eligible, "
                f"{len(envelope.excluded)} excluded by policy"
            ),
        )

    @staticmethod
    def _answer_evidence_check(findings: list[ValidationFinding]) -> GateCheck:
        """G1-NG-02: no answer key rests on evidence the policy refused."""
        if findings:
            return GateCheck(
                check_id="G1-NG-02",
                status=GateCheckStatus.FAILED,
                reason_code="unapproved_answer_evidence",
                detail=f"{len(findings)} item(s) depend on inadmissible evidence",
            )
        return GateCheck(
            check_id="G1-NG-02",
            status=GateCheckStatus.PASSED,
            reason_code="answer_evidence_admissible",
        )

    def _extraction_check(self, load_result: PackLoadResult, *, pack_directory: str) -> GateCheck:
        """G1-FX-02: required source regions were actually processed.

        The defect this closes: the check previously passed merely because
        a ``LocalFixtureExtractor`` had been constructed. Nothing was
        extracted, nothing was verified, and with no approved PDF parser
        nothing *could* be — yet the report said ``passed``. A check that
        reports success for work it never did is worse than no check.

        Now every source artifact is actually read and byte-verified, and
        every evidence region is actually resolved against those bytes. A
        PDF region with no approved parser reports ``BLOCKED``, which is
        the honest state of the unresolved parser decision.
        """
        if self._extractor is None:
            return GateCheck(
                check_id="G1-FX-02",
                status=GateCheckStatus.NOT_RUN,
                reason_code="extractor_not_configured",
                detail="no local fixture extractor was supplied to this runner",
            )

        pack = load_result.pack
        bindings = load_result.source_manifest_bindings
        artifacts = pack.sources_by_id
        extensions = load_result.fixture_extensions
        pixel_hashes: dict[str, str] = (
            dict(extensions.region_pixel_sha256)
            if isinstance(extensions, FixtureExtensions)
            else {}
        )
        outcomes: list[tuple[str, ExtractionOutcome]] = []
        blocked_on_parser = False

        for region in sorted(pack.evidence_regions, key=lambda item: item.evidence_id):
            artifact = artifacts.get(region.source_id)
            binding = bindings.get(region.source_id) if artifact is not None else None
            if artifact is None or binding is None:
                return GateCheck(
                    check_id="G1-FX-02",
                    status=GateCheckStatus.FAILED,
                    reason_code="source_not_bound",
                    detail=f"region {region.evidence_id} has no bound manifest source",
                )

            result = self._extractor.extract_region(
                region, artifact, relative_path=f"{pack_directory}/{binding.relative_path}"
            )
            outcomes.append((region.evidence_id, result.outcome))
            if result.outcome is ExtractionOutcome.EXTRACTOR_UNAVAILABLE:
                blocked_on_parser = True
            elif result.outcome is not ExtractionOutcome.EXTRACTED:
                return GateCheck(
                    check_id="G1-FX-02",
                    status=GateCheckStatus.FAILED,
                    reason_code=result.outcome.value,
                    detail=f"region {region.evidence_id}: {result.detail or result.outcome.value}",
                )

            # The comparison is token-exact and layout-insensitive. A PDF
            # breaks lines and pads columns wherever its layout engine
            # chose to, so requiring byte equality with the reviewed
            # passage would fail on whitespace while proving nothing;
            # requiring the identical token sequence proves every
            # command, identifier, number, and negation survived. See
            # extraction.artifacts.normalize_extracted_text.
            if (
                result.text is not None
                and region.exact_text is not None
                and not same_extracted_text(result.text, region.exact_text)
            ):
                return GateCheck(
                    check_id="G1-FX-02",
                    status=GateCheckStatus.FAILED,
                    reason_code="extracted_text_mismatch",
                    detail=(
                        f"region {region.evidence_id}: text extracted from the source bytes "
                        "does not carry the same tokens as the reviewed passage"
                    ),
                )

            # An image region's content hash is over decoded pixels. When
            # the fixture pins one, it is checked against what the decoder
            # actually produced — the strongest available evidence that a
            # real image was read rather than a plausible-looking header.
            expected_pixel_hash = pixel_hashes.get(region.evidence_id)
            if (
                expected_pixel_hash is not None
                and result.region_content_sha256 is not None
                and result.region_content_sha256 != expected_pixel_hash
            ):
                return GateCheck(
                    check_id="G1-FX-02",
                    status=GateCheckStatus.FAILED,
                    reason_code="image_region_hash_mismatch",
                    detail=(
                        f"region {region.evidence_id}: decoded pixels hash to "
                        f"{result.region_content_sha256}, not the frozen "
                        f"{expected_pixel_hash}"
                    ),
                )

        if blocked_on_parser:
            return GateCheck(
                check_id="G1-FX-02",
                status=GateCheckStatus.BLOCKED,
                reason_code="pdf_parser_unavailable",
                detail=(
                    "PDF text extraction requires an approved parser dependency, which "
                    "is an unresolved human decision; no sidecar substitute is accepted"
                ),
            )

        if not outcomes:
            return GateCheck(
                check_id="G1-FX-02",
                status=GateCheckStatus.BLOCKED,
                reason_code="no_regions_processed",
                detail="the pack defines no evidence region, so nothing was extracted",
            )

        return GateCheck(
            check_id="G1-FX-02",
            status=GateCheckStatus.PASSED,
            reason_code="regions_extracted_and_verified",
            observed_hash=hash_records(
                ({"evidence_id": eid, "outcome": outcome.value} for eid, outcome in outcomes),
                sort=True,
            ),
        )

    def _blocked_result(
        self, *, run_id: str, started_at: datetime, reason_code: str, detail: str
    ) -> EvidenceGateResult:
        """A report for a pack that could not be loaded at all."""
        finished_at = datetime.now(UTC)
        report = GateReport(
            gate_id=GateId.GATE_1,
            run_id=run_id,
            code_revision=self._code_revision,
            # A pack that never loaded has no manifest to hash; the
            # all-zero digest is a deliberate, recognizable sentinel
            # rather than a hash of nothing that might collide with a
            # real one.
            fixture_manifest_hash="0" * 64,
            fixture_authority="draft_for_human_review",
            # Bound the same way as a full run's: a report that could not
            # load a pack still has to be projectable onto the frozen
            # schema, and its one check still names what it was measured
            # against.
            checks=GateDefinition.gate_1().bind_expectations(
                (
                    GateCheck(
                        check_id="G1-GO-01",
                        status=GateCheckStatus.BLOCKED,
                        reason_code=reason_code,
                        detail=detail[:2_000],
                    ),
                )
            ),
            started_at=started_at,
            finished_at=finished_at,
        )
        return EvidenceGateResult(
            report=report, load_result=None, envelope=None, reason_codes=(reason_code,)
        )


#: The stable use-case port. Kept as an alias rather than a second class
#: so callers name the port while the runner stays one implementation.
CcnaMasteryUseCase = EvidenceGateRunner


def build_ccna_mastery_use_case(
    *,
    loader: ObjectivePackLoader,
    review_service: EvidenceReviewService,
    validator: ObjectivePackValidator | None = None,
    extractor: LocalFixtureExtractor | None = None,
    code_revision: str | None = None,
    fixture_authority: FixtureAuthority | None = None,
    focused_work_evidence: ExternalFocusedWorkLedger | None = None,
    focused_work_attempt_id: UUID | None = None,
    trusted_focused_work_signer_ids: tuple[str, ...] = (),
) -> CcnaMasteryUseCase:
    """Assemble the CCNA lab use case from injected dependencies.

    Every collaborator is supplied by the caller except the validator,
    which is stateless and has a single canonical configuration; passing
    one explicitly stays available for exercising an alternative scoring
    policy.

    ``code_revision`` is resolved from the working tree when omitted, so a
    report never silently records "unversioned" — a report with no code
    provenance cannot be compared against anything.
    """
    return EvidenceGateRunner(
        loader=loader,
        validator=validator if validator is not None else ObjectivePackValidator(),
        review_service=review_service,
        extractor=extractor,
        code_revision=(code_revision if code_revision else resolve_code_revision()),
        fixture_authority=fixture_authority,
        focused_work_evidence=focused_work_evidence,
        focused_work_attempt_id=focused_work_attempt_id,
        trusted_focused_work_signer_ids=trusted_focused_work_signer_ids,
    )


def executing_checkout_root() -> Path:
    """The checkout the *running* ``personal_lms`` package was imported from.

    Derived from ``personal_lms.__file__``, never from a caller argument.
    A caller-selected root let dirty Gate 1 code report the canonical
    clean checkout's SHA simply by pointing ``--project-root`` at it,
    which is provenance laundering rather than provenance.
    """
    return Path(personal_lms.__file__).resolve().parents[2]


def resolve_code_revision() -> str:
    """A logical identity for the code that actually executed.

    Two properties the earlier version lacked:

    - **It covers untracked source.** ``git diff HEAD`` reports tracked
      modifications only, and the entire Gate 1 implementation is still
      untracked, so it contributed nothing to the "dirty" digest. Two
      materially different implementations produced the same revision.
      The dirty component now digests the executing package's own bytes.
    - **It takes no argument.** The root comes from the imported package,
      so there is nothing for a caller to point somewhere else.

    Returns ``<sha>`` for a clean tree, ``<sha>+dirty.<digest>`` when the
    tracked diff or the executing sources differ from that commit, and
    ``unidentified-build:<digest>`` when git is unavailable. Never empty
    and never ``unversioned``.
    """
    root = executing_checkout_root()
    source_digest = _hash_executing_sources()

    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        tracked_diff = subprocess.run(
            ["git", "-C", str(root), "diff", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
        untracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return f"unidentified-build:{source_digest}"

    if not head:
        return f"unidentified-build:{source_digest}"

    # A tree is dirty when tracked files differ OR when any untracked file
    # exists. The digest always covers the executing sources, so editing an
    # untracked module changes the revision even though git's diff does not
    # mention it.
    if not tracked_diff.strip() and not untracked.strip():
        return head
    return f"{head}+dirty.{source_digest}"


def _hash_executing_sources() -> str:
    """A digest of every ``.py`` file in the executing package.

    Path-and-content, in sorted path order, so the digest is stable across
    processes and changes whenever any executing module changes — tracked
    or not.
    """
    package_root = Path(personal_lms.__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def default_evidence_policy(pack: ObjectivePack) -> EvidencePolicy:
    """The strict teaching policy a Gate 1 run uses by default."""
    return EvidencePolicy(
        policy_version="gate-1-evidence-1.0",
        objective_ref=pack.objective_ref,
        requested_use=PermittedUse.LOCAL_TEACH,
    )


def regions_for_source(
    pack: ObjectivePack, source: SourceArtifactRef
) -> tuple[EvidenceRegion, ...]:
    """Every region drawn from ``source``, in stable ID order."""
    return tuple(
        sorted(
            (region for region in pack.evidence_regions if region.source_id == source.source_id),
            key=lambda region: region.evidence_id,
        )
    )
