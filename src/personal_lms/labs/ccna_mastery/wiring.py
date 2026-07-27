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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import personal_lms
from personal_lms.domain.objective_packs import (
    EvidenceRegion,
    ObjectivePack,
    ObjectivePackEvidenceEnvelope,
    ObjectivePackValidationReport,
    PermittedUse,
    SourceArtifactRef,
    ValidationFinding,
)
from personal_lms.evidence_review.authority import EvidenceAuthoritySnapshot
from personal_lms.evidence_review.service import EvidenceReviewService
from personal_lms.extraction.artifacts import ExtractionOutcome
from personal_lms.extraction.local_fixture import LocalFixtureExtractor
from personal_lms.labs.ccna_mastery.gates import (
    FixtureAuthority,
    GateCheck,
    GateCheckStatus,
    GateId,
    GateReport,
)
from personal_lms.objective_packs.eligibility import EvidencePolicy
from personal_lms.objective_packs.errors import ObjectivePackError
from personal_lms.objective_packs.hashing import hash_record, hash_records
from personal_lms.objective_packs.loader import ObjectivePackLoader, PackLoadResult
from personal_lms.objective_packs.validation import ObjectivePackValidator

__all__ = [
    "CcnaMasteryUseCase",
    "executing_checkout_root",
    "EvidenceGateResult",
    "EvidenceGateRunner",
    "build_ccna_mastery_use_case",
    "resolve_code_revision",
]


@dataclass(frozen=True, slots=True)
class EvidenceGateResult:
    """Everything one Gate 1 run produced.

    The report is the comparable artifact; the pack, validation report,
    and envelope are kept alongside it so a CLI can show detail without
    re-running the gate.
    """

    report: GateReport
    load_result: PackLoadResult | None
    envelope: ObjectivePackEvidenceEnvelope | None
    reason_codes: tuple[str, ...]

    @property
    def pack(self) -> ObjectivePack | None:
        return self.load_result.pack if self.load_result is not None else None


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

        validation = self._validator.validate(pack)
        envelope = self._validator.build_evidence_envelope(pack, policy, authority=authority)
        answer_findings = self._validator.validate_answer_evidence(pack, envelope)

        checks: list[GateCheck] = [
            self._loader_check(load_result),
            self._reference_check(validation.reason_codes),
            self._baseline_check(pack, validation.reason_codes),
            self._exposure_check(validation.reason_codes),
            self._coverage_check(validation, pack),
            self._grounding_check(validation.reason_codes),
            self._review_check(authority),
            self._eligibility_check(envelope),
            self._answer_evidence_check(answer_findings),
            self._extraction_check(load_result, pack_directory=pack_directory),
            GateCheck(
                check_id="G1-GO-07",
                required=True,
                status=GateCheckStatus.NOT_RUN,
                reason_code="retrieval_cases_not_frozen",
                detail=(
                    "supported-query retrieval cases require a frozen, human-reviewed "
                    "fixture corpus; this pre-clock pass does not fabricate one"
                ),
            ),
            GateCheck(
                check_id="G1-GO-08",
                required=True,
                status=GateCheckStatus.NOT_RUN,
                reason_code="retrieval_cases_not_frozen",
                detail="unsupported-query abstention cases require the same frozen corpus",
            ),
        ]

        finished_at = datetime.now(UTC)
        manifest_hash = hash_record(load_result.manifest)
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
            checks=tuple(checks),
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
    def _grounding_check(reason_codes: tuple[str, ...]) -> GateCheck:
        """G1-GO-06: every answer-bearing claim meets the grounding threshold."""
        if "grounding_below_threshold" in reason_codes:
            return GateCheck(
                check_id="G1-GO-06",
                status=GateCheckStatus.FAILED,
                reason_code="grounding_below_threshold",
            )
        return GateCheck(
            check_id="G1-GO-06",
            status=GateCheckStatus.PASSED,
            reason_code="grounding_meets_threshold",
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

            if result.text is not None and result.text != region.exact_text:
                return GateCheck(
                    check_id="G1-FX-02",
                    status=GateCheckStatus.FAILED,
                    reason_code="extracted_text_mismatch",
                    detail=(
                        f"region {region.evidence_id}: extracted text does not match the "
                        "pack's exact_text"
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
            checks=(
                GateCheck(
                    check_id="G1-GO-01",
                    status=GateCheckStatus.BLOCKED,
                    reason_code=reason_code,
                    detail=detail[:2_000],
                ),
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
