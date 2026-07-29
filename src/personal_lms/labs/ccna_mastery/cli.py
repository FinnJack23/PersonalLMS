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
  approved goldens.** Accepted reports live outside the exact frozen
  ``tests/linchpin`` tree. These commands construct an unauthorized
  ``GoldenArtifactGuard``, so a write into the accepted-golden tree raises
  rather than succeeding.
- **``evidence approve-region`` records a decision a human supplies.** It
  requires an explicit reviewer identity, outcome, and reason, and the
  service refuses the decision if the region's bytes have changed since.
  It cannot invent an approval, and it is not wired to any automated
  path.
- **The reviewer-only golden command implements its approved contract
  and is not invoked here.** ``gate accept-goldens`` requires an explicit
  acceptance flag, an explicit reviewer identity, and exact report,
  manifest, and code-revision pins. Every other command constructs an
  *unauthorized* guard, so nothing else can reach the expected tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from personal_lms.domain.evidence_review import EvidenceReviewOutcome, ReviewerIdentity
from personal_lms.evidence_review.authority import record_region_approval
from personal_lms.evidence_review.errors import EvidenceReviewError
from personal_lms.evidence_review.service import EvidenceReviewService
from personal_lms.evidence_review.sqlite import SQLiteEvidenceReviewRepository
from personal_lms.extraction.artifacts import PdfTextExtractor, PngPixelDecoder
from personal_lms.extraction.local_fixture import (
    LocalFixtureExtractor,
    PdfMinerTextExtractor,
    PillowPngDecoder,
)
from personal_lms.labs.ccna_mastery.fixture_authority_store import (
    FixtureAuthorityDecision,
    SQLiteFixtureAuthorityRepository,
    current_fixture_authority,
)
from personal_lms.labs.ccna_mastery.gates import (
    GateArtifactPaths,
    GateId,
    GateStatus,
    GoldenArtifactGuard,
    GoldenWriteRefusedError,
    ObservedGateReportStore,
    assert_safe_path_segment,
    provenance_path_for_primary,
    write_immutable_artifact,
)
from personal_lms.labs.ccna_mastery.report_schema import (
    ReportProvenanceError,
    report_from_bound_provenance,
    validate_against_frozen_schema,
)
from personal_lms.labs.ccna_mastery.wiring import (
    EvidenceGateRunner,
    build_ccna_mastery_use_case,
    manifest_hash_for,
    resolve_code_revision,
)
from personal_lms.objective_packs.errors import ObjectivePackError
from personal_lms.objective_packs.linchpin_fixture import load_frozen_fixture
from personal_lms.objective_packs.loader import (
    ObjectivePackLoader,
    PackFileReader,
    PackLoadResult,
)
from personal_lms.objective_packs.validation import ObjectivePackValidator

__all__ = [
    "handle_ccna_lab_command",
    "register_ccna_lab_commands",
    "resolve_persistent_review_database",
]

# Artifact roots are derived from a trusted project root rather than
# accepted individually, so a normal caller cannot redefine where the real
# golden tree lives. See GateArtifactPaths.for_project_root.
_FROZEN_GATE_REPORT_SCHEMA = Path("tests/linchpin/schemas/gate-report.schema.json")


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
            "output under var/ccna-mastery/gates, accepted goldens under "
            "tests/goldens/ccna-mastery. Neither root is individually selectable; "
            "frozen fixture expectations remain under tests/linchpin."
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

    report_parser = gate_subparsers.add_parser(
        "report",
        help=(
            "Render an observed gate report against the frozen schema. Read-only: "
            "reads the primary and provenance sidecar under var/, writes nothing."
        ),
    )
    report_parser.add_argument("--run-id", required=True, help="Observed run to render.")
    report_parser.add_argument("--attempt-id", default=None)
    report_parser.add_argument("--project-root", default=".")
    report_parser.add_argument(
        "--schema",
        default="tests/linchpin/schemas/gate-report.schema.json",
        help="Frozen gate-report schema, relative to --project-root.",
    )

    accept_parser = gate_subparsers.add_parser(
        "accept-goldens",
        help=(
            "REVIEWER ONLY. Accept an observed report as the approved golden. "
            "Requires reviewer identity, exact report/manifest/code pins, and an "
            "explicit confirmation. Refuses otherwise."
        ),
    )
    accept_parser.add_argument("--run-id", required=True)
    accept_parser.add_argument("--attempt-id", default=None)
    accept_parser.add_argument("--project-root", default=".")
    accept_parser.add_argument(
        "--reviewer-id", required=True, help="Opaque local reviewer id. No default."
    )
    accept_parser.add_argument(
        "--expect-report-sha256",
        required=True,
        help="The SHA-256 of the exact primary observed-report file bytes being accepted.",
    )
    accept_parser.add_argument(
        "--expect-manifest-sha256",
        required=True,
        help="The exact fixture manifest self-hash the report must cite.",
    )
    accept_parser.add_argument(
        "--expect-code-revision",
        required=True,
        help="The exact code revision that produced the report.",
    )
    accept_parser.add_argument(
        "--i-am-authorized-to-accept-goldens",
        action="store_true",
        help="Explicit acceptance confirmation. Absent, the command refuses.",
    )
    accept_parser.add_argument(
        "--schema",
        default="tests/linchpin/schemas/gate-report.schema.json",
        help=(
            "Frozen gate-report schema, relative to --project-root. The candidate "
            "golden must validate against it before acceptance."
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

    approve_fixture_parser = evidence_subparsers.add_parser(
        "approve-fixture",
        help=(
            "Record one reviewer decision that an entire frozen fixture manifest is "
            "reviewed and ready for gate use. Binds to the exact manifest self-hash; "
            "requires an explicit reviewer identity, outcome, and reason."
        ),
    )
    _add_pack_arguments(approve_fixture_parser)
    approve_fixture_parser.add_argument(
        "--reviewer-id", required=True, help="Opaque local reviewer id."
    )
    approve_fixture_parser.add_argument("--reviewer-role", default="content_reviewer")
    approve_fixture_parser.add_argument(
        "--outcome",
        required=True,
        choices=[outcome.value for outcome in EvidenceReviewOutcome],
        help="The decision. There is no default: a reviewer must state it.",
    )
    approve_fixture_parser.add_argument("--reason", required=True)
    approve_fixture_parser.add_argument(
        "--review-database",
        required=True,
        help="Path to the evidence-review SQLite database (shared file, separate table).",
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
    parser.add_argument(
        "--pack-format",
        default="json",
        choices=["json", "frozen-fixture"],
        help=(
            "Authoring format. 'json' reads manifest.json plus pack.json; "
            "'frozen-fixture' reads a split-YAML tree pinned by fixture-manifest.yaml. "
            "Stated explicitly rather than sniffed: which loader runs is a caller's "
            "decision, not a consequence of which files happen to be present."
        ),
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


def _build_loader(pack_root: str, pack_format: str = "json") -> ObjectivePackLoader:
    """A loader for the caller's declared authoring format.

    ``frozen-fixture`` returns a thin subclass so the gate runner's single
    loader seam stays one type. The format adapter is reached only by a
    caller that asked for it — nothing infers the format from a filename.
    """
    reader = PackFileReader(roots=[Path(pack_root)])
    if pack_format == "frozen-fixture":
        return _FrozenFixtureLoader(reader)
    return ObjectivePackLoader(reader)


class _FrozenFixtureLoader(ObjectivePackLoader):
    """Loads a split-YAML frozen fixture tree through the same seam."""

    def load(
        self,
        *,
        pack_directory: str,
        manifest_filename: str = "",
        pack_filename: str = "",
    ) -> PackLoadResult:
        return load_frozen_fixture(self.reader, fixture_directory=pack_directory)


def _build_extractor(pack_root: str) -> LocalFixtureExtractor:
    """A fixture extractor wired to the declared optional adapters.

    Both adapters are *declared* dependencies of the ``ccna-lab`` extra,
    not ambient packages. When the extra is absent they are simply not
    injected, and the extractor reports a typed
    ``EXTRACTOR_UNAVAILABLE`` with installation guidance — the gate
    reports honestly rather than silently degrading to a header-only
    check or a sidecar substitute.
    """
    reader = PackFileReader(roots=[Path(pack_root)])
    pdf_text_extractor: PdfTextExtractor | None = None
    png_pixel_decoder: PngPixelDecoder | None = None
    try:
        pdf_text_extractor = PdfMinerTextExtractor()
    except ModuleNotFoundError:
        pdf_text_extractor = None
    try:
        png_pixel_decoder = PillowPngDecoder()
    except ModuleNotFoundError:
        png_pixel_decoder = None
    return LocalFixtureExtractor(
        reader, pdf_text_extractor=pdf_text_extractor, png_pixel_decoder=png_pixel_decoder
    )


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
        gate_command = getattr(args, "gate_command", None)
        if gate_command == "evidence":
            return _gate_evidence_command(args)
        if gate_command == "report":
            return _gate_report_command(args)
        if gate_command == "accept-goldens":
            return _accept_goldens_command(args)
        print("FAIL specify a gate to run, e.g. 'ccna-lab gate evidence'")
        return 2
    if lab_command == "evidence":
        evidence_command = getattr(args, "evidence_command", None)
        if evidence_command == "approve-region":
            return _approve_region_command(args)
        if evidence_command == "approve-fixture":
            return _approve_fixture_command(args)
        print("FAIL specify an evidence command, e.g. 'ccna-lab evidence approve-region'")
        return 2

    print("FAIL specify a ccna-lab command: validate, gate, or evidence")
    return 2


def _validate_command(args: argparse.Namespace) -> int:
    """Load and validate a pack, printing findings. Writes nothing."""
    try:
        loader = _build_loader(args.pack_root, args.pack_format)
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
    fixture_authority_repository = SQLiteFixtureAuthorityRepository.open(str(database))
    fixture_authority_repository.initialize_schema()

    try:
        loader = _build_loader(args.pack_root, args.pack_format)
        # A real gate report can only claim fixture_authority="reviewed"
        # from a persisted decision bound to the exact manifest hash the
        # pack actually loads to. That hash is not known until the pack is
        # loaded, so it is pre-loaded once here purely to resolve
        # authority; EvidenceGateRunner.run loads it again as the run of
        # record. Loading is pure and read-only, so the duplication costs
        # nothing but one extra read.
        try:
            preload = loader.load(pack_directory=args.pack_directory)
        except ObjectivePackError as exc:
            print(f"FAIL {exc.reason_code}: {exc}")
            return 1
        manifest_hash, _ = manifest_hash_for(preload)
        fixture_authority = current_fixture_authority(
            fixture_authority_repository, manifest_hash=manifest_hash
        )

        runner: EvidenceGateRunner = build_ccna_mastery_use_case(
            loader=loader,
            review_service=review_service,
            extractor=_build_extractor(args.pack_root),
            code_revision=resolve_code_revision(),
            fixture_authority=fixture_authority,
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
        print(f"authority: {result.report.fixture_authority}")
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
        fixture_authority_repository.close()


def _observed_report_path(args: argparse.Namespace) -> tuple[GateArtifactPaths, Path]:
    """The observed report a run/attempt names, with its artifact roots.

    ``run_id``/``attempt_id`` are validated as single safe path segments
    and the resolved destination is required to land inside the observed
    root before any filesystem read is attempted. An earlier revision
    joined these caller-controlled strings into a path with no validation
    at all, so a ``--run-id`` of ``"../../../../etc"`` would have this
    command (and the golden-acceptance command, which reuses this helper)
    attempt to read a file anywhere on disk.
    """
    paths = GateArtifactPaths.for_project_root(args.project_root, require_executing_checkout=True)
    assert_safe_path_segment(args.run_id, "run_id")
    name = "gate-1"
    if args.attempt_id is not None:
        assert_safe_path_segment(args.attempt_id, "attempt_id")
        name = f"gate-1.{args.attempt_id}"
    candidate = paths.observed_root / args.run_id / f"{name}.json"
    resolved = candidate.resolve()
    if resolved != paths.observed_root and paths.observed_root not in resolved.parents:
        raise GoldenWriteRefusedError(
            "observed report path escapes the configured observed root; refusing to "
            "follow a run_id/attempt_id that resolves outside var/ccna-mastery/gates"
        )
    return paths, resolved


def _observed_provenance_path(paths: GateArtifactPaths, source: Path) -> Path:
    """The sidecar adjacent to ``source``, still contained by observed root."""
    resolved = provenance_path_for_primary(source).resolve()
    if resolved != paths.observed_root and paths.observed_root not in resolved.parents:
        raise GoldenWriteRefusedError(
            "observed report provenance escapes the configured observed root; refusing "
            "to follow a sidecar outside var/ccna-mastery/gates"
        )
    return resolved


def _frozen_schema_path(args: argparse.Namespace) -> Path:
    """Resolve only the repository's canonical frozen report schema."""
    root = Path(args.project_root).resolve()
    canonical = (root / _FROZEN_GATE_REPORT_SCHEMA).resolve()
    requested = (root / args.schema).resolve()
    if requested != canonical:
        raise ValueError(
            "the report schema is frozen at "
            f"{_FROZEN_GATE_REPORT_SCHEMA}; refusing alternate schema {args.schema!r}"
        )
    return canonical


def _gate_report_command(args: argparse.Namespace) -> int:
    """Validate the exact primary report and its bound provenance. Writes nothing.

    The guard is constructed unauthorized, so even a bug that tried to
    write into the expected tree from here would raise rather than
    succeed. This command only reads.
    """
    try:
        paths, source = _observed_report_path(args)
        provenance_source = _observed_provenance_path(paths, source)
    except (ValueError, GoldenWriteRefusedError) as exc:
        print(f"FAIL artifact paths are not usable: {exc}")
        return 2

    guard = GoldenArtifactGuard(paths=paths, reviewer_authorized=False)
    if guard.is_golden_path(source):
        print("FAIL an observed report never lives in the approved-golden tree")
        return 2
    if not source.is_file():
        print(f"FAIL no observed report at {source}")
        return 1
    if not provenance_source.is_file():
        print(f"FAIL no provenance sidecar at {provenance_source}")
        return 1

    primary_bytes = source.read_bytes()
    try:
        payload = json.loads(primary_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"FAIL the primary observed report is not valid JSON: {exc}")
        return 1
    try:
        schema_path = _frozen_schema_path(args)
    except ValueError as exc:
        print(f"FAIL {exc}")
        return 2
    if not schema_path.is_file():
        print(f"FAIL no schema at {schema_path}")
        return 1

    violations = validate_against_frozen_schema(payload, schema_bytes=schema_path.read_bytes())
    if violations:
        print(f"FAIL the primary report has {len(violations)} frozen-schema violation(s):")
        for violation in violations:
            print(f"  {violation}")
        return 1

    try:
        report = report_from_bound_provenance(
            primary_bytes=primary_bytes,
            provenance_bytes=provenance_source.read_bytes(),
        )
    except ReportProvenanceError as exc:
        print(f"FAIL {exc}")
        return 1
    if report.run_id != args.run_id:
        print(
            f"FAIL provenance run_id {report.run_id!r} does not match the requested "
            f"run {args.run_id!r}"
        )
        return 1

    rendered_checks = payload.get("checks") if isinstance(payload, dict) else None

    print(f"report:   {source}")
    print(f"provenance: {provenance_source}")
    print(f"status:   {report.status.value} (frozen primary: {payload.get('status')})")
    print(f"manifest: {payload.get('fixture_manifest_sha256')}")
    print(f"revision: {payload.get('code_revision')}")
    print(f"checks:   {len(rendered_checks) if isinstance(rendered_checks, list) else 0}")
    print("OK the primary report validates and its provenance binding is intact")
    return 0


def _accept_goldens_command(args: argparse.Namespace) -> int:
    """Accept an observed report as the approved golden. Reviewer only.

    Every precondition is checked *before* anything is written, and each
    one is a separate refusal so a reviewer learns which is missing rather
    than that "something" was wrong:

    1. an explicit acceptance flag — no default, no environment variable;
    2. an explicit reviewer identity;
    3. the exact observed report the reviewer inspected, pinned by its
       content hash;
    4. the exact fixture manifest self-hash that report cites;
    5. the exact code revision that produced it.

    Pins 3-5 are what stop a reviewer's intent from silently transferring:
    approving *a* report for run ``X`` must not accept whatever happens to
    sit at that path now, produced by different code over different
    fixtures.

    This run implements the contract and does not exercise it. Invocation
    and any golden write remain separately unauthorized.
    """
    if not args.i_am_authorized_to_accept_goldens:
        print(
            "FAIL golden acceptance requires the explicit "
            "--i-am-authorized-to-accept-goldens confirmation; "
            "there is no default and no implicit path"
        )
        return 2
    if not args.reviewer_id.strip():
        print("FAIL golden acceptance requires an explicit reviewer identity")
        return 2

    try:
        paths, source = _observed_report_path(args)
        provenance_source = _observed_provenance_path(paths, source)
    except (ValueError, GoldenWriteRefusedError) as exc:
        print(f"FAIL artifact paths are not usable: {exc}")
        return 2
    if not source.is_file():
        print(f"FAIL no observed report at {source}")
        return 1
    if not provenance_source.is_file():
        print(f"FAIL no provenance sidecar at {provenance_source}")
        return 1

    raw_bytes = source.read_bytes()
    observed_hash = hashlib.sha256(raw_bytes).hexdigest()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"FAIL the observed report is not valid JSON: {exc}")
        return 1

    mismatches: list[str] = []
    if observed_hash != args.expect_report_sha256:
        mismatches.append(
            f"report sha256 is {observed_hash}, not the pinned {args.expect_report_sha256}"
        )
    declared_manifest = (
        payload.get("fixture_manifest_sha256") if isinstance(payload, dict) else None
    )
    if declared_manifest != args.expect_manifest_sha256:
        mismatches.append(
            f"report cites manifest {declared_manifest}, not the pinned "
            f"{args.expect_manifest_sha256}"
        )
    declared_revision = payload.get("code_revision") if isinstance(payload, dict) else None
    if declared_revision != args.expect_code_revision:
        mismatches.append(
            f"report was produced by {declared_revision}, not the pinned "
            f"{args.expect_code_revision}"
        )
    if mismatches:
        print("FAIL the observed report is not the one being accepted:")
        for mismatch in mismatches:
            print(f"  {mismatch}")
        return 1

    # The primary artifact itself must satisfy the literal frozen schema.
    # No projected substitute is accepted at review time.
    try:
        schema_path = _frozen_schema_path(args)
    except ValueError as exc:
        print(f"FAIL {exc}")
        return 2
    if not schema_path.is_file():
        print(f"FAIL no schema at {schema_path}")
        return 1
    violations = validate_against_frozen_schema(payload, schema_bytes=schema_path.read_bytes())
    if violations:
        print(f"FAIL the primary report fails the frozen schema, {len(violations)} violation(s):")
        for violation in violations:
            print(f"  {violation}")
        return 1

    # The separate immutable sidecar preserves the richer internal outcome
    # and authority state. Its primary hash and byte-for-byte reprojection
    # prove the two artifacts describe the same run.
    try:
        report = report_from_bound_provenance(
            primary_bytes=raw_bytes,
            provenance_bytes=provenance_source.read_bytes(),
        )
    except ReportProvenanceError as exc:
        print(f"FAIL {exc}")
        return 1
    if report.run_id != args.run_id:
        print(
            f"FAIL provenance run_id {report.run_id!r} does not match the requested "
            f"run {args.run_id!r}"
        )
        return 1

    if report.gate_id is not GateId.GATE_1:
        print(f"FAIL only a gate-1 report may be accepted here, not {report.gate_id.value!r}")
        return 1
    if report.missing_required_check_ids:
        print(
            "FAIL the observed report is missing required check(s): "
            + ", ".join(report.missing_required_check_ids)
        )
        return 1
    if report.status is not GateStatus.PASSED:
        print(
            f"FAIL only a PASSED report may become a golden; observed status is "
            f"{report.status.value}"
        )
        return 1
    if report.fixture_authority != "reviewed":
        print(
            f"FAIL the observed report's fixture authority is {report.fixture_authority!r}, "
            "not 'reviewed'; an unapproved-authority run can never become a golden"
        )
        return 1
    guard = GoldenArtifactGuard(paths=paths, reviewer_authorized=True, reviewer_id=args.reviewer_id)
    accepted_name = f"gate-1-{args.run_id}"
    if args.attempt_id is not None:
        accepted_name = f"{accepted_name}.{args.attempt_id}"
    filename = f"{accepted_name}.json"
    destination = guard.golden_root / filename
    try:
        guard.assert_write_authorized(destination)
    except GoldenWriteRefusedError as exc:
        print(f"FAIL {exc}")
        return 1

    guard.golden_root.mkdir(parents=True, exist_ok=True)
    resolved_golden_root = guard.golden_root.resolve()
    try:
        written = write_immutable_artifact(
            directory=resolved_golden_root, filename=filename, data=raw_bytes
        )
    except GoldenWriteRefusedError:
        print(
            f"FAIL a golden already exists at {destination}; replacing an approved "
            "artifact is a separate, explicitly authorized action"
        )
        return 1

    print(f"accepted golden {written}")
    print(f"  reviewer: {args.reviewer_id}")
    print(f"  report:   {observed_hash}")
    print(f"  manifest: {args.expect_manifest_sha256}")
    return 0


def _approve_region_command(args: argparse.Namespace) -> int:
    """Record one reviewer decision, bound to the region's current subject.

    The decision is built from the *recomputed* review subject — pack
    identity and version, objective version, source bytes, selector, and
    resolved content — so a reviewer's approval can never be re-scoped to
    something they did not look at.
    """
    try:
        load_result = _build_loader(args.pack_root, args.pack_format).load(
            pack_directory=args.pack_directory
        )
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

    try:
        # The reviewer command may create the store; it is the writer.
        database = resolve_persistent_review_database(args.review_database, must_exist=False)
    except ValueError as exc:
        print(f"FAIL {exc}")
        return 2

    review_service, repository = _build_review_service(str(database))
    try:
        try:
            recorded = record_region_approval(
                pack=pack,
                region=region,
                artifact=artifact,
                review_service=review_service,
                reviewer_id=args.reviewer_id,
                reviewer_role=args.reviewer_role,
                outcome=EvidenceReviewOutcome(args.outcome),
                reason=args.reason,
                accessible_description=args.accessible_description,
                decided_at=_clock(),
            )
        except ValueError as exc:
            print(f"FAIL the decision is not well-formed: {exc}")
            return 2
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


def _approve_fixture_command(args: argparse.Namespace) -> int:
    """Record one reviewer decision about an entire frozen fixture manifest.

    Bound to the manifest's own verified self-hash — recomputed from the
    loaded pack, never taken from an argument — so a reviewer's approval
    can never be re-scoped to a manifest they did not actually load and
    inspect.
    """
    try:
        load_result = _build_loader(args.pack_root, args.pack_format).load(
            pack_directory=args.pack_directory
        )
    except ObjectivePackError as exc:
        print(f"FAIL {exc.reason_code}: {exc}")
        return 1

    manifest_hash, manifest_hash_kind = manifest_hash_for(load_result)

    try:
        # The reviewer command may create the store; it is the writer.
        database = resolve_persistent_review_database(args.review_database, must_exist=False)
    except ValueError as exc:
        print(f"FAIL {exc}")
        return 2

    repository = SQLiteFixtureAuthorityRepository.open(str(database))
    repository.initialize_schema()
    try:
        try:
            decision = FixtureAuthorityDecision(
                manifest_hash=manifest_hash,
                reviewer=ReviewerIdentity(reviewer_id=args.reviewer_id, role=args.reviewer_role),
                outcome=EvidenceReviewOutcome(args.outcome),
                reason=args.reason,
                decided_at=_clock(),
            )
        except ValueError as exc:
            print(f"FAIL the decision is not well-formed: {exc}")
            return 2
        recorded = repository.append(decision)
    finally:
        repository.close()

    print("recorded fixture authority decision")
    print(f"  manifest:  {recorded.manifest_hash} ({manifest_hash_kind})")
    print(f"  outcome:   {recorded.outcome.value}")
    print(f"  reviewer:  {recorded.reviewer.reviewer_id}")
    return 0


def _clock() -> datetime:
    """The single point where this surface reads wall time.

    Isolated so a caller can substitute it in a test, and so the rest of
    the module keeps the explicit-clock convention the domain follows.
    """
    return datetime.now(UTC)
