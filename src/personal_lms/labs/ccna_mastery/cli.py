"""Nested ``ccna-lab`` command surface.

Registered onto the existing root parser rather than shipping a second
console entry point — ``personal-lms`` stays the one executable, and
``personal_lms.cli`` keeps every behavior it already had.

Command handlers here do argument marshalling, dependency assembly, and
result printing. Every decision they report comes from an application
service (``EvidenceGateRunner``, ``EvidenceReviewService``,
``ObjectivePackValidator``); no domain logic lives in a handler.

Safety properties this surface preserves:

- **Nothing here starts a clock, locks a lab, or approves a fixture.**
- **``validate``, ``gate``, and ``report`` are read-only with respect to
  approved goldens.** They construct an unauthorized
  ``GoldenArtifactGuard``, so a write into the expected tree raises
  rather than succeeding.
- **``evidence approve-region`` records a decision a human supplies.** It
  requires an explicit reviewer identity, outcome, and reason, and the
  service refuses the decision if the region's bytes have changed since.
  It cannot invent an approval, and it is not wired to any automated
  path.
- **The reviewer-only golden command is deliberately absent.** Its exact
  contract needs the operator's approval (AD-03); shipping a
  placeholder would be worse than shipping nothing.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from personal_lms.domain.evidence_review import (
    EvidenceReviewDecision,
    EvidenceReviewKind,
    EvidenceReviewOutcome,
    ReviewerIdentity,
    derive_decision_id,
)
from personal_lms.evidence_review.authority import subject_digest_for
from personal_lms.evidence_review.errors import EvidenceReviewError
from personal_lms.evidence_review.service import EvidenceReviewService
from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
from personal_lms.extraction.local_fixture import LocalFixtureExtractor
from personal_lms.labs.ccna_mastery.gates import (
    GateArtifactPaths,
    GateStatus,
    GoldenArtifactGuard,
    GoldenWriteRefusedError,
    ObservedGateReportStore,
)
from personal_lms.labs.ccna_mastery.wiring import (
    EvidenceGateRunner,
    build_ccna_mastery_use_case,
    resolve_code_revision,
)
from personal_lms.objective_packs.errors import ObjectivePackError
from personal_lms.objective_packs.loader import ObjectivePackLoader, PackFileReader
from personal_lms.objective_packs.validation import ObjectivePackValidator

__all__ = [
    "handle_ccna_lab_command",
    "register_ccna_lab_commands",
    "resolve_persistent_review_database",
]

# Artifact roots are derived from a trusted project root rather than
# accepted individually, so a normal caller cannot redefine where the real
# golden tree lives. See GateArtifactPaths.for_project_root.


def register_ccna_lab_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Attach the ``ccna-lab`` parser and its subcommands to the root parser."""
    lab_parser = subparsers.add_parser(
        "ccna-lab",
        help="Deterministic CCNA mastery lab: pack validation, evidence review, and gates.",
    )
    lab_subparsers = lab_parser.add_subparsers(dest="lab_command")

    validate_parser = lab_subparsers.add_parser(
        "validate", help="Load and validate one Objective Pack. Never writes anything."
    )
    _add_pack_arguments(validate_parser)

    gate_parser = lab_subparsers.add_parser(
        "gate", help="Run a deterministic gate and write an observed report."
    )
    gate_subparsers = gate_parser.add_subparsers(dest="gate_command")

    evidence_gate_parser = gate_subparsers.add_parser(
        "evidence", help="Run the Gate 1 evidence checks. Read-only with respect to goldens."
    )
    _add_pack_arguments(evidence_gate_parser)
    evidence_gate_parser.add_argument(
        "--run-id",
        default=None,
        help="Identifier for this run's observed output directory. Defaults to a UTC timestamp.",
    )
    evidence_gate_parser.add_argument(
        "--attempt-id",
        default=None,
        help=(
            "Non-authoritative attempt id, required to re-run an existing run id "
            "rather than overwriting its evidence."
        ),
    )
    evidence_gate_parser.add_argument(
        "--project-root",
        default=".",
        help=(
            "Project root. Canonical artifact roots are derived from it: observed "
            "output under var/ccna-mastery/gates, expected artifacts under "
            "tests/linchpin/expected. Neither root is individually selectable."
        ),
    )
    evidence_gate_parser.add_argument(
        "--review-database",
        required=True,
        help=(
            "Path to the persistent evidence-review database the reviewer command "
            "writes to. Required: an in-memory substitute would silently hide every "
            "recorded approval."
        ),
    )

    evidence_parser = lab_subparsers.add_parser(
        "evidence", help="Reviewer-only evidence decisions."
    )
    evidence_subparsers = evidence_parser.add_subparsers(dest="evidence_command")

    approve_parser = evidence_subparsers.add_parser(
        "approve-region",
        help=(
            "Record one reviewer decision about one evidence region. Requires an "
            "explicit reviewer identity, outcome, and reason."
        ),
    )
    _add_pack_arguments(approve_parser)
    approve_parser.add_argument("--evidence-id", required=True)
    approve_parser.add_argument("--reviewer-id", required=True, help="Opaque local reviewer id.")
    approve_parser.add_argument("--reviewer-role", default="content_reviewer")
    approve_parser.add_argument(
        "--outcome",
        required=True,
        choices=[outcome.value for outcome in EvidenceReviewOutcome],
        help="The decision. There is no default: a reviewer must state it.",
    )
    approve_parser.add_argument("--reason", required=True)
    approve_parser.add_argument(
        "--accessible-description",
        default=None,
        help="Required when approving an image region; this gate claims no OCR.",
    )
    approve_parser.add_argument(
        "--review-database", required=True, help="Path to the evidence-review SQLite database."
    )


def _add_pack_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments every pack-reading command shares."""
    parser.add_argument(
        "--pack-root",
        required=True,
        help="Configured root directory. No path outside it is readable.",
    )
    parser.add_argument(
        "--pack-directory",
        required=True,
        help="Pack directory, relative to --pack-root.",
    )


#: SQLite treats these as in-memory databases rather than files. Passing
#: one to a command that promises persistence produces a store that
#: vanishes at exit — and, for the URI forms, ``sqlite3.connect`` without
#: ``uri=True`` treats the whole string as a *filename*, so a review probe
#: actually created ``file:memdb1?mode=memory&cache=shared`` in the
#: worktree. Both outcomes are refused here.
_NON_PERSISTENT_PREFIXES = (":memory:", "file::memory:", "file:memdb")


def resolve_persistent_review_database(candidate: str, *, must_exist: bool = True) -> Path:
    """The resolved path of a durable review database file.

    ``must_exist`` is ``True`` for readers (the gate), which must fail
    closed rather than silently read an empty store, and ``False`` for the
    reviewer command, which legitimately creates the store when recording
    the first decision. Both reject in-memory and URI forms either way.

    Fails closed on every form that does not name a durable file, and does
    so *before* opening anything, so a refusal never creates a file named
    after the rejected string.

    In-memory composition stays available to unit tests through
    ``SQLiteEvidenceReviewRepository.open`` directly; the prohibition is a
    property of this CLI boundary, which promises the reviewer command and
    the gate command share one store.
    """
    stripped = candidate.strip()
    if not stripped:
        raise ValueError("a review database path is required and must not be empty")

    lowered = stripped.lower()
    if lowered.startswith(_NON_PERSISTENT_PREFIXES) or lowered.startswith("file:"):
        raise ValueError(
            f"{stripped!r} names an in-memory or URI SQLite database; this command "
            "requires a durable file so the reviewer and gate commands share one store"
        )

    path = Path(stripped)
    if path.is_dir():
        raise ValueError(f"{stripped!r} is a directory, not a review database file")
    if must_exist and not path.exists():
        raise ValueError(
            f"the review database does not exist: {path}. Record a reviewer decision "
            "first; this command never substitutes an in-memory store."
        )
    if not must_exist and not path.parent.exists():
        raise ValueError(f"the review database's directory does not exist: {path.parent}")
    return path.resolve()


def _build_loader(pack_root: str) -> ObjectivePackLoader:
    return ObjectivePackLoader(PackFileReader(roots=[Path(pack_root)]))


def _build_review_service(
    database_path: str,
) -> tuple[EvidenceReviewService, SQLiteEvidenceReviewRepository]:
    """A review service plus the repository the caller must close."""
    repository = SQLiteEvidenceReviewRepository.open(database_path)
    repository.initialize_schema()
    return EvidenceReviewService(repository), repository


def handle_ccna_lab_command(args: argparse.Namespace) -> int:
    """Dispatch one ``ccna-lab`` invocation. Returns a process exit code."""
    lab_command = getattr(args, "lab_command", None)
    if lab_command == "validate":
        return _validate_command(args)
    if lab_command == "gate":
        if getattr(args, "gate_command", None) == "evidence":
            return _gate_evidence_command(args)
        print("FAIL specify a gate to run, e.g. 'ccna-lab gate evidence'")
        return 2
    if lab_command == "evidence":
        if getattr(args, "evidence_command", None) == "approve-region":
            return _approve_region_command(args)
        print("FAIL specify an evidence command, e.g. 'ccna-lab evidence approve-region'")
        return 2

    print("FAIL specify a ccna-lab command: validate, gate, or evidence")
    return 2


def _validate_command(args: argparse.Namespace) -> int:
    """Load and validate a pack, printing findings. Writes nothing."""
    try:
        loader = _build_loader(args.pack_root)
        load_result = loader.load(pack_directory=args.pack_directory)
    except ObjectivePackError as exc:
        print(f"FAIL {exc.reason_code}: {exc}")
        return 1

    report = ObjectivePackValidator().validate(load_result.pack)

    print(f"pack:      {report.pack_id} v{report.pack_version}")
    print(f"objective: {report.objective_ref}")
    print(f"authority: {load_result.manifest.fixture_status}")
    print(f"hash:      {report.canonical_pack_hash}")

    for finding in (*load_result.findings, *report.findings):
        print(f"  [{finding.severity.value}] {finding.reason_code.value} {finding.subject_id}")
        print(f"      {finding.message}")

    if load_result.has_errors or not report.is_valid:
        print("FAIL pack validation reported errors")
        return 1

    print("OK pack validated")
    return 0


def _gate_evidence_command(args: argparse.Namespace) -> int:
    """Run Gate 1 evidence checks and write an observed report under var/.

    Fails closed when the review store is missing. Silently substituting
    an in-memory database made the reviewer command and the gate command
    use different stores, so every recorded approval was invisible and the
    gate's "nothing approved" result looked like a fixture problem rather
    than a wiring bug.
    """
    try:
        database = resolve_persistent_review_database(args.review_database)
    except ValueError as exc:
        print(f"FAIL {exc}")
        return 2

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    try:
        paths = GateArtifactPaths.for_project_root(
            args.project_root, require_executing_checkout=True
        )
    except ValueError as exc:
        print(f"FAIL artifact paths are not usable: {exc}")
        return 2
    paths.observed_root.mkdir(parents=True, exist_ok=True)

    guard = GoldenArtifactGuard(paths=paths, reviewer_authorized=False)
    review_service, repository = _build_review_service(str(database))

    try:
        runner: EvidenceGateRunner = build_ccna_mastery_use_case(
            loader=_build_loader(args.pack_root),
            review_service=review_service,
            extractor=LocalFixtureExtractor(PackFileReader(roots=[Path(args.pack_root)])),
            code_revision=resolve_code_revision(),
        )
        result = runner.run(pack_directory=args.pack_directory, run_id=run_id)

        try:
            destination = ObservedGateReportStore(guard=guard).write(
                result.report, attempt_id=args.attempt_id
            )
        except GoldenWriteRefusedError as exc:
            print(f"FAIL {exc}")
            return 1

        print(f"gate:     {result.report.gate_id.value}")
        print(f"status:   {result.report.status.value}")
        print(f"revision: {result.report.code_revision}")
        print(f"report:   {destination}")
        for check in result.report.checks:
            print(f"  {check.check_id:<12} {check.status.value:<10} {check.reason_code}")

        missing = result.report.missing_required_check_ids
        if missing:
            print(f"missing required checks: {', '.join(missing)}")
        blocking = result.report.blocking_check_ids
        if blocking:
            print(f"blocking checks: {', '.join(blocking)}")
        return 0 if result.report.status is GateStatus.PASSED else 1
    finally:
        repository.close()


def _approve_region_command(args: argparse.Namespace) -> int:
    """Record one reviewer decision, bound to the region's current subject.

    The decision is built from the *recomputed* review subject — pack
    identity and version, objective version, source bytes, selector, and
    resolved content — so a reviewer's approval can never be re-scoped to
    something they did not look at.
    """
    try:
        load_result = _build_loader(args.pack_root).load(pack_directory=args.pack_directory)
    except ObjectivePackError as exc:
        print(f"FAIL {exc.reason_code}: {exc}")
        return 1

    pack = load_result.pack
    region = pack.evidence_by_id.get(args.evidence_id)
    if region is None:
        print(f"FAIL the pack defines no evidence region {args.evidence_id!r}")
        return 1
    artifact = pack.sources_by_id.get(region.source_id)
    if artifact is None:
        print(f"FAIL region {args.evidence_id!r} cites a source the pack does not define")
        return 1

    decided_at = _clock()
    kind = (
        EvidenceReviewKind.VISUAL
        if region.selector.kind == "image_region"
        else EvidenceReviewKind.TEXT
    )
    digest = subject_digest_for(pack, region, artifact)

    try:
        # The reviewer command may create the store; it is the writer.
        database = resolve_persistent_review_database(args.review_database, must_exist=False)
    except ValueError as exc:
        print(f"FAIL {exc}")
        return 2

    review_service, repository = _build_review_service(str(database))
    try:
        supersedes = None
        current = review_service.current_decision(region.evidence_id)
        if current is not None:
            # A correction always names the current leaf, so history stays
            # a single linear chain rather than a fork.
            supersedes = current.decision_id

        try:
            decision = EvidenceReviewDecision(
                decision_id=derive_decision_id(
                    evidence_id=region.evidence_id,
                    subject_digest=digest,
                    reviewer_id=args.reviewer_id,
                    decided_at=decided_at.isoformat(),
                ),
                evidence_id=region.evidence_id,
                source_id=artifact.source_id,
                pack_id=pack.manifest.pack_id,
                pack_version=pack.manifest.pack_version,
                objective_ref=pack.objective_ref,
                kind=kind,
                outcome=EvidenceReviewOutcome(args.outcome),
                subject_digest=digest,
                source_sha256=artifact.sha256,
                reviewer=ReviewerIdentity(reviewer_id=args.reviewer_id, role=args.reviewer_role),
                reason=args.reason,
                accessible_description=(
                    args.accessible_description if kind is EvidenceReviewKind.VISUAL else None
                ),
                decided_at=decided_at,
                supersedes_decision_id=supersedes,
            )
        except ValueError as exc:
            print(f"FAIL the decision is not well-formed: {exc}")
            return 2

        try:
            recorded = review_service.record_decision(
                decision, pack=pack, region=region, artifact=artifact
            )
        except EvidenceReviewError as exc:
            print(f"FAIL {exc.reason_code}: {exc}")
            return 1
    finally:
        repository.close()

    print(f"recorded decision {recorded.decision_id}")
    print(f"  region:     {recorded.evidence_id}")
    print(f"  outcome:    {recorded.outcome.value}")
    print(f"  reviewer:   {recorded.reviewer.reviewer_id}")
    print(f"  supersedes: {recorded.supersedes_decision_id or '(root decision)'}")
    return 0


def _clock() -> datetime:
    """The single point where this surface reads wall time.

    Isolated so a caller can substitute it in a test, and so the rest of
    the module keeps the explicit-clock convention the domain follows.
    """
    return datetime.now(UTC)
