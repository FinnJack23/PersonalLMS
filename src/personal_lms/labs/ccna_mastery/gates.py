"""Gate checks, reports, and the guard that protects approved golden artifacts.

Three ideas carry the whole module:

1. **A gate status is derived, never asserted.** ``GateReport.status`` is
   computed from its checks by a fixed rule. There is no way to construct
   a passing report that contains a failing required check, so "the gate
   passed" always means the same thing.

2. **Observed output and approved expectations are different trees.**
   Approved goldens live under a reviewed expected/ directory and are
   read-only to every normal run. Observed output is written beneath a
   configured runtime directory (``var/`` by convention, which is
   ignored by Git). ``GoldenArtifactGuard`` enforces the separation
   structurally rather than by convention.

3. **Hashes come from canonical logical records.** Never from SQLite
   database bytes, WAL segments, or any other storage representation —
   those vary with page layout and vacuum state and would make an
   evidence hash meaningless. See ``objective_packs.hashing``.

Authority status is explicit. A report whose fixture authority is still
``draft_for_human_review`` cannot claim ``PASSED``: it reports
``UNAPPROVED_AUTHORITY`` instead, so a green-looking run over unreviewed
fixtures is impossible to mistake for a real result.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from personal_lms.domain.base import StrictModel
from personal_lms.objective_packs.hashing import canonical_json, hash_record

__all__ = [
    "FixtureAuthority",
    "GateArtifactPaths",
    "GateCheck",
    "GateCheckStatus",
    "GateDefinition",
    "GateId",
    "GateReport",
    "GateStatus",
    "GoldenArtifactGuard",
    "GoldenWriteRefusedError",
    "ObservedGateReportStore",
]

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: Check IDs permitted to report ``DEFERRED``. Everything else must reach
#: a real outcome — deferral is not a way to make an inconvenient check
#: disappear. Kept deliberately tiny and explicit.
_DEFERRABLE_CHECK_IDS: frozenset[str] = frozenset({"G3-RI-QWEN-01", "G3-RI-QWEN-02"})


class GateId(StrEnum):
    """The gates a report may describe."""

    GATE_1 = "gate-1"
    GATE_2 = "gate-2"
    GATE_3 = "gate-3"


class GateCheckStatus(StrEnum):
    """One check's outcome.

    ``NOT_RUN`` is distinct from ``BLOCKED``: the first means the check
    never executed, the second means it executed and refused to produce a
    result because a precondition was unmet. Collapsing them would hide
    which gate actually stopped.
    """

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"
    DEFERRED = "deferred"


class GateStatus(StrEnum):
    """A whole gate's derived status.

    ``UNAPPROVED_AUTHORITY`` is a first-class outcome rather than a
    variant of failure: the checks may all have passed, but over fixtures
    no human has approved, so the run proves nothing about readiness.
    """

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"
    UNAPPROVED_AUTHORITY = "unapproved_authority"


@dataclass(frozen=True, slots=True)
class GateDefinition:
    """The trusted inventory of checks a gate requires.

    Lives in code rather than in fixture data, deliberately. If the
    required-check list were an input, a run could shrink it to whatever
    it happened to produce, and "the gate passed" would mean nothing.
    Check IDs match ``LINCHPIN_TRACEABILITY.md`` so a report and the plan
    can be read side by side.

    ``definition_version`` is recorded in comparisons: a report judged
    against one inventory is not comparable with one judged against
    another.
    """

    gate_id: GateId
    definition_version: str
    required_check_ids: tuple[str, ...]

    @classmethod
    def gate_1(cls) -> GateDefinition:
        """Gate 1's complete required inventory: all 26 traceability rows.

        Every ``G1-GO``, ``G1-NG``, and ``G1-FX`` row from
        ``LINCHPIN_TRACEABILITY.md``, not the subset this pass can
        currently evaluate. Shortening the inventory to what happens to be
        implemented would silently redefine the gate — a run over 12
        checks that reports ``passed`` is answering a different and much
        easier question than the one the plan asks.

        Checks with no implementation report ``NOT_RUN`` or ``BLOCKED``,
        which is what keeps a pre-clock run honest.
        """
        return cls(
            gate_id=GateId.GATE_1,
            definition_version="gate-1-linchpin-traceability-1.0",
            required_check_ids=(
                # Fixture-level requirements.
                "G1-FX-01",
                "G1-FX-02",
                "G1-FX-03",
                "G1-FX-04",
                "G1-FX-05",
                "G1-FX-06",
                "G1-FX-07",
                "G1-FX-08",
                "G1-FX-09",
                # GO traceability.
                "G1-GO-01",
                "G1-GO-02",
                "G1-GO-03",
                "G1-GO-04",
                "G1-GO-05",
                "G1-GO-06",
                "G1-GO-07",
                "G1-GO-08",
                "G1-GO-09",
                "G1-GO-10",
                "G1-GO-11",
                # NO-GO traceability.
                "G1-NG-01",
                "G1-NG-02",
                "G1-NG-03",
                "G1-NG-04",
                "G1-NG-05",
                "G1-NG-06",
            ),
        )

    def __post_init__(self) -> None:
        ids = self.required_check_ids
        if len(ids) != len(set(ids)):
            raise ValueError("a gate definition must not name the same check twice")
        if list(ids) != sorted(ids):
            raise ValueError("required_check_ids must be sorted for stable comparison")

    def defines(self, check_id: str) -> bool:
        return check_id in self.required_check_ids

    @classmethod
    def for_gate(cls, gate_id: GateId) -> GateDefinition:
        """The definition for ``gate_id``.

        Gates 2 and 3 are out of scope for this pass and deliberately
        carry an empty inventory, which makes any report claiming them
        ``NOT_RUN`` rather than accidentally passable.
        """
        if gate_id is GateId.GATE_1:
            return cls.gate_1()
        return cls(gate_id=gate_id, definition_version="not-yet-defined", required_check_ids=())


@dataclass(frozen=True, slots=True)
class FixtureAuthority:
    """Whether a fixture tree has actually been approved by a human.

    The defect this closes: ``fixture_status`` lived inside the pack
    manifest, so an authoring file could grant itself reviewed status by
    writing one word. A manifest claim is evidence of intent, never of
    review. Real authority is an *external* reviewer decision pinned to
    the exact manifest hash it approved, so it cannot drift onto a
    modified fixture tree.
    """

    manifest_hash: str
    reviewer_id: str | None = None
    decided_at: datetime | None = None
    claimed_status: str | None = None

    @classmethod
    def from_manifest_claim(cls, *, claimed_status: str, manifest_hash: str) -> FixtureAuthority:
        """Record what a manifest *claims*. Never authoritative."""
        return cls(manifest_hash=manifest_hash, claimed_status=claimed_status)

    @classmethod
    def from_reviewer_decision(
        cls, *, manifest_hash: str, reviewer_id: str, decided_at: datetime
    ) -> FixtureAuthority:
        """Record an external reviewer decision pinned to one manifest hash."""
        if not reviewer_id:
            raise ValueError("a fixture approval requires an explicit reviewer identity")
        return cls(manifest_hash=manifest_hash, reviewer_id=reviewer_id, decided_at=decided_at)

    @property
    def is_authoritative(self) -> bool:
        """True only for an external decision with a named reviewer."""
        return self.reviewer_id is not None and self.decided_at is not None

    def applies_to(self, manifest_hash: str) -> bool:
        """Whether this authority covers exactly ``manifest_hash``."""
        return self.is_authoritative and self.manifest_hash == manifest_hash

    def resolved_status(self, manifest_hash: str) -> Literal["draft_for_human_review", "reviewed"]:
        """The authority value a report should carry for this manifest."""
        return "reviewed" if self.applies_to(manifest_hash) else "draft_for_human_review"


class GateCheck(StrictModel):
    """One traceability row's observed result.

    ``check_id`` matches the ID used in
    ``docs/plans/ccna-mastery-micro-lab/LINCHPIN_TRACEABILITY.md`` (e.g.
    ``G1-GO-03``) so a report and the plan can be read side by side.

    ``observed_hash`` is a canonical logical-record hash of whatever the
    check actually observed — never a file digest of a database.
    """

    check_id: str = Field(min_length=1, max_length=64)
    required: bool = True
    status: GateCheckStatus
    reason_code: str = Field(min_length=1, max_length=64)
    expected_ref: str | None = Field(default=None, min_length=1)
    observed_hash: str | None = None
    detail: str | None = Field(default=None, min_length=1, max_length=2_000)

    @field_validator("observed_hash")
    @classmethod
    def _hash_is_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _SHA256_HEX_PATTERN.fullmatch(value):
            raise ValueError("observed_hash must be exactly 64 lowercase hex characters")
        return value

    @model_validator(mode="after")
    def _only_named_checks_may_defer(self) -> Self:
        if self.status is GateCheckStatus.DEFERRED and self.check_id not in _DEFERRABLE_CHECK_IDS:
            raise ValueError(
                f"check {self.check_id} may not report 'deferred'; only "
                f"{sorted(_DEFERRABLE_CHECK_IDS)} are deferrable, and a required "
                "evidence, grading, state, route-safety, or authority check never is"
            )
        return self


class GateReport(StrictModel):
    """One gate run's complete, comparable result.

    Timestamps are the only fields a comparison may normalize — a report
    differing in a status, check ID, reason code, hash, or revision is a
    genuinely different result, and normalizing any of those away would
    defeat the purpose of freezing an expectation.
    """

    schema_version: Literal["1.0"] = "1.0"
    gate_id: GateId
    run_id: str = Field(min_length=1, max_length=128)
    code_revision: str = Field(min_length=1, max_length=128)
    fixture_manifest_hash: str
    fixture_authority: Literal["draft_for_human_review", "reviewed"] = "draft_for_human_review"
    checks: tuple[GateCheck, ...] = Field(default_factory=tuple)
    started_at: AwareDatetime
    finished_at: AwareDatetime

    @field_validator("fixture_manifest_hash")
    @classmethod
    def _hash_is_valid(cls, value: str) -> str:
        if not _SHA256_HEX_PATTERN.fullmatch(value):
            raise ValueError("fixture_manifest_hash must be exactly 64 lowercase hex characters")
        return value

    @model_validator(mode="after")
    def _finished_not_before_started(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self

    @model_validator(mode="after")
    def _check_ids_are_unique(self) -> Self:
        ids = [check.check_id for check in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("a report must not contain the same check_id twice")
        return self

    @property
    def elapsed_milliseconds(self) -> int:
        """Elapsed wall time as exact integer milliseconds.

        Integer rather than float so the value has a canonical form: a
        float has no unambiguous decimal representation, and the canonical
        hasher refuses one rather than silently choosing a rendering.
        """
        delta = self.finished_at - self.started_at
        return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000

    @property
    def required_checks(self) -> tuple[GateCheck, ...]:
        """Deprecated view: what the *report* claims is required.

        Kept only so the distinction from ``effective_required_checks``
        stays visible. Status derivation must never use this — a report
        that marks its own checks optional is exactly the attack.
        """
        return tuple(check for check in self.checks if check.required)

    @property
    def effective_required_checks(self) -> tuple[GateCheck, ...]:
        """Checks that are required because the *definition* says so.

        Requiredness is a property of the gate, not of the report. The
        report's own ``required`` flag may add to this set (an extra check
        a run chose to treat as blocking) but can never remove from it.
        Without that rule a caller demotes every defined check to
        ``required=False``, lets them all fail, and the gate reports
        ``passed`` — which is precisely the bypass this closes.
        """
        definition = self.definition
        return tuple(
            check for check in self.checks if definition.defines(check.check_id) or check.required
        )

    @property
    def unknown_check_ids(self) -> tuple[str, ...]:
        """Reported checks the definition does not name, sorted.

        Never a substitute for a defined check: padding a report with
        unknown passing checks cannot offset a defined failure or a
        missing row.
        """
        definition = self.definition
        return tuple(
            sorted(
                {check.check_id for check in self.checks if not definition.defines(check.check_id)}
            )
        )

    @property
    def definition(self) -> GateDefinition:
        """The trusted check inventory this report is judged against."""
        return GateDefinition.for_gate(self.gate_id)

    @property
    def missing_required_check_ids(self) -> tuple[str, ...]:
        """Required checks the definition names but this report omits.

        The defect this closes: a report could previously pass while
        simply *not containing* most required checks, so the cheapest way
        to make a gate green was to report one passing check and stop.
        Absence must never read as success.
        """
        present = {check.check_id for check in self.checks}
        return tuple(sorted(set(self.definition.required_check_ids) - present))

    @property
    def status(self) -> GateStatus:
        """The gate's status, derived from its checks and fixture authority.

        Fixed precedence, strictest first:

        1. any required check ``FAILED``                -> ``FAILED``
        2. any required check ``BLOCKED``               -> ``BLOCKED``
        3. a required check is missing, or any ``NOT_RUN`` -> ``NOT_RUN``
        4. fixtures not externally approved             -> ``UNAPPROVED_AUTHORITY``
        5. otherwise                                    -> ``PASSED``

        Authority is checked *after* the failure cases so a genuinely
        failing run still reports the failure rather than hiding it
        behind a fixture-approval complaint.
        """
        required = self.effective_required_checks
        if any(check.status is GateCheckStatus.FAILED for check in required):
            return GateStatus.FAILED
        if any(check.status is GateCheckStatus.BLOCKED for check in required):
            return GateStatus.BLOCKED
        if (
            not required
            or self.missing_required_check_ids
            or any(check.status is GateCheckStatus.NOT_RUN for check in required)
            # A deferral on a check the definition requires is not a real
            # outcome. The allowlist keeps this unreachable for Gate 1
            # today; the guard stays so a future allowlist change cannot
            # silently turn a required check into a pass.
            or any(check.status is GateCheckStatus.DEFERRED for check in required)
        ):
            return GateStatus.NOT_RUN
        if self.fixture_authority != "reviewed":
            return GateStatus.UNAPPROVED_AUTHORITY
        return GateStatus.PASSED

    @property
    def deferred_check_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                check.check_id for check in self.checks if check.status is GateCheckStatus.DEFERRED
            )
        )

    @property
    def blocking_check_ids(self) -> tuple[str, ...]:
        """Required checks that stopped the gate, sorted."""
        return tuple(
            sorted(
                check.check_id
                for check in self.effective_required_checks
                if check.status
                in (
                    GateCheckStatus.FAILED,
                    GateCheckStatus.BLOCKED,
                    GateCheckStatus.NOT_RUN,
                    GateCheckStatus.DEFERRED,
                )
            )
        )

    def to_canonical_json(self) -> str:
        """Canonical serialized form, including the derived fields.

        Derived values are materialized here so a stored report is
        self-describing: a reader never has to re-run the derivation rule
        to know what the gate concluded.
        """
        payload = self.model_dump(mode="json")
        payload["status"] = self.status.value
        payload["elapsed_milliseconds"] = self.elapsed_milliseconds
        payload["definition_version"] = self.definition.definition_version
        payload["deferred_checks"] = list(self.deferred_check_ids)
        payload["blocking_checks"] = list(self.blocking_check_ids)
        payload["missing_required_checks"] = list(self.missing_required_check_ids)
        payload["unknown_checks"] = list(self.unknown_check_ids)
        return canonical_json(payload)

    @property
    def content_hash(self) -> str:
        """Canonical logical hash of this report, excluding runtime timing.

        Timestamps and elapsed time are dropped before hashing because
        they are exactly the fields a comparison is allowed to normalize.
        Two runs that agree on every status, check, reason code, and hash
        therefore share a content hash even though they ran at different
        times.
        """
        payload = self.model_dump(mode="json")
        payload.pop("started_at", None)
        payload.pop("finished_at", None)
        payload["status"] = self.status.value
        payload["definition_version"] = self.definition.definition_version
        return hash_record(payload)


class GoldenWriteRefusedError(Exception):
    """A write to the approved-golden tree was refused.

    Raised by ``GoldenArtifactGuard`` for every write a normal gate run
    attempts. Replacing a golden requires an explicitly authorized
    reviewer command, never a validate, gate, or report run.
    """

    reason_code = "golden_write_refused"


@dataclass(frozen=True, slots=True)
class GateArtifactPaths:
    """The two artifact roots, from trusted application configuration.

    The defect this closes: a CLI caller previously chose *both* roots, so
    a normal run could point ``--golden-root`` at an empty directory and
    then write anywhere it liked. Canonical roots are now derived from the
    project root, and the observed tree is always beneath
    ``var/ccna-mastery/gates`` — an ignored runtime location that can
    never be mistaken for reviewed expectations.

    The roots must be disjoint. Overlap in either direction would let
    observed output land inside the expected tree, which is precisely the
    confusion the separation exists to prevent.
    """

    expected_root: Path
    observed_root: Path

    def __post_init__(self) -> None:
        expected = self.expected_root.resolve()
        observed = self.observed_root.resolve()
        object.__setattr__(self, "expected_root", expected)
        object.__setattr__(self, "observed_root", observed)

        if expected == observed or _is_within(observed, expected) or _is_within(expected, observed):
            raise ValueError(
                "the expected and observed roots must be disjoint; an observed report "
                "written inside the expected tree could be mistaken for an approved golden"
            )

    @classmethod
    def for_project_root(
        cls, project_root: Path | str, *, require_executing_checkout: bool = False
    ) -> GateArtifactPaths:
        """The canonical roots for a project checkout.

        ``require_executing_checkout`` is what a real CLI run passes: it
        requires the root to be the checkout the running ``personal_lms``
        package was imported from, so artifact paths and code provenance
        describe the same tree. Without it a caller could write a report
        about *this* code into some other project's directories. Tests
        needing only a scratch layout leave it off.
        """
        root = Path(project_root).resolve()
        if require_executing_checkout:
            from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

            executing = executing_checkout_root()
            if root != executing:
                raise ValueError(
                    "gate artifact roots must belong to the executing PersonalLMS "
                    f"checkout ({executing}); refusing to use {root}"
                )
        return cls(
            expected_root=root / "tests" / "linchpin" / "expected",
            observed_root=root / "var" / "ccna-mastery" / "gates",
        )


def _is_within(candidate: Path, parent: Path) -> bool:
    """Whether ``candidate`` resolves inside ``parent``."""
    return parent in candidate.parents


class GoldenArtifactGuard:
    """Keeps approved goldens read-only and observed output out of their tree.

    Constructed from ``GateArtifactPaths``, so the roots come from trusted
    configuration rather than from whatever a caller passed on the command
    line. Containment is checked against *resolved* locations, so a
    relative path or a symlink cannot smuggle a write into the golden
    tree.

    ``reviewer_authorized`` defaults to ``False`` and is the only way a
    golden write is ever permitted. It is a constructor argument rather
    than a method parameter, so a guard handed to a gate runner physically
    cannot be talked into authorizing anything.
    """

    def __init__(
        self,
        *,
        paths: GateArtifactPaths,
        reviewer_authorized: bool = False,
        reviewer_id: str | None = None,
    ) -> None:
        if reviewer_authorized and not reviewer_id:
            raise ValueError(
                "an authorized golden guard requires an explicit reviewer_id; "
                "an anonymous approval is not an approval"
            )
        self._paths = paths
        self._reviewer_authorized = reviewer_authorized
        self._reviewer_id = reviewer_id

    @property
    def paths(self) -> GateArtifactPaths:
        return self._paths

    @property
    def golden_root(self) -> Path:
        return self._paths.expected_root

    @property
    def observed_root(self) -> Path:
        return self._paths.observed_root

    @property
    def reviewer_authorized(self) -> bool:
        return self._reviewer_authorized

    @property
    def reviewer_id(self) -> str | None:
        return self._reviewer_id

    def is_golden_path(self, path: Path | str) -> bool:
        """Whether ``path`` resolves inside the approved-golden tree."""
        candidate = Path(path).resolve()
        return candidate == self.golden_root or _is_within(candidate, self.golden_root)

    def assert_write_authorized(self, path: Path | str) -> None:
        """Permit a write to ``path``, or refuse it."""
        if not self.is_golden_path(path):
            return
        if self._reviewer_authorized:
            return
        raise GoldenWriteRefusedError(
            "refusing to write an approved golden artifact: normal validate, gate, "
            "and report runs are read-only with respect to the expected tree. "
            "Replacing a golden requires the reviewer-only accept-goldens command "
            "with an explicit reviewer identity."
        )

    def assert_observed_path(self, path: Path | str) -> None:
        """Require that observed output lands under the observed root.

        Resolution happens first, so a symlinked run directory pointing
        outside ``var/`` is caught here rather than followed.
        """
        candidate = Path(path).resolve()
        if candidate != self.observed_root and not _is_within(candidate, self.observed_root):
            raise GoldenWriteRefusedError(
                "observed gate output must be written beneath the configured observed "
                "root; writing it anywhere else risks overwriting reviewed expectations"
            )


class ObservedGateReportStore:
    """Writes observed gate reports beneath the configured runtime directory.

    Write safety, in order:

    1. The run id is validated as a single safe path segment, so it cannot
       traverse.
    2. The destination is resolved and required to be inside the observed
       root — a symlinked run directory escaping ``var/`` is refused, not
       followed.
    3. The temporary file is created with ``mkstemp`` in the destination
       directory: an unpredictable name, opened exclusively, never
       following a symlink. The earlier predictable ``.json.tmp`` name
       could be pre-created as a symlink pointing at a golden, turning an
       ordinary run into an overwrite of approved output.
    4. ``os.replace`` swaps it into place atomically, so an interrupted
       run never leaves a half-written report a comparison would read as
       real.
    5. An existing report for the same run is *not* silently overwritten;
       a re-run must supply an explicit ``attempt_id``.
    """

    def __init__(self, *, guard: GoldenArtifactGuard) -> None:
        self._guard = guard

    def path_for(self, report: GateReport, *, attempt_id: str | None = None) -> Path:
        """``<observed_root>/<run_id>/<gate_id>[.<attempt_id>].json``."""
        _assert_safe_segment(report.run_id, "run_id")
        name = report.gate_id.value
        if attempt_id is not None:
            _assert_safe_segment(attempt_id, "attempt_id")
            name = f"{name}.{attempt_id}"
        return self._guard.observed_root / report.run_id / f"{name}.json"

    def write(
        self,
        report: GateReport,
        *,
        attempt_id: str | None = None,
        destination_override: Path | None = None,
    ) -> Path:
        """Publish ``report`` immutably and return the path written.

        Order matters and closes two reproduced defects:

        1. **Containment is decided before anything is created.** The
           earlier version called ``mkdir(parents=True)`` and only then
           checked the destination, so a refused write still left a
           directory outside the observed root.
        2. **Publication is create-if-absent.** ``os.replace`` is atomic
           but overwrites, so a reused ``attempt_id`` silently replaced a
           previous run's evidence. ``os.link`` fails when the target
           exists, which makes "first writer wins" a property of the
           filesystem rather than of a prior existence check that two
           racing writers could both pass.
        """
        destination = (
            Path(destination_override)
            if destination_override is not None
            else self.path_for(report, attempt_id=attempt_id)
        )

        # Decide admissibility on the *lexical* path first — nothing has
        # been created yet, so a refusal here leaves no trace.
        self._guard.assert_write_authorized(destination)
        self._assert_lexically_contained(destination)

        run_directory = destination.parent
        run_directory.mkdir(parents=True, exist_ok=True)

        # Re-check after creation: a pre-existing symlinked run directory
        # is detected here rather than followed.
        resolved_parent = run_directory.resolve()
        self._guard.assert_observed_path(resolved_parent)
        resolved = resolved_parent / destination.name
        self._guard.assert_write_authorized(resolved)

        handle, temporary_name = tempfile.mkstemp(
            dir=resolved_parent, prefix=".gate-report-", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(report.to_canonical_json())
            temporary.chmod(0o600)
            try:
                # Create-if-absent. Refuses when the target exists, and
                # refuses for a hardlinked or symlinked target too.
                os.link(temporary, resolved)
            except FileExistsError as exc:
                raise GoldenWriteRefusedError(
                    f"an observed report already exists at {resolved.name}; supply a "
                    "distinct non-authoritative attempt id rather than overwriting a "
                    "previous run's evidence"
                ) from exc
        finally:
            temporary.unlink(missing_ok=True)
        return resolved

    def _assert_lexically_contained(self, destination: Path) -> None:
        """Refuse a destination outside the observed root, before any mkdir.

        Uses the *unresolved* path deliberately: resolution can require
        the parent to exist, and this check must happen while nothing has
        been created.
        """
        observed = self._guard.observed_root
        candidate = destination if destination.is_absolute() else (observed / destination)
        try:
            candidate.relative_to(observed)
        except ValueError as exc:
            raise GoldenWriteRefusedError(
                "observed gate output must be written beneath the configured observed "
                "root; writing it anywhere else risks overwriting reviewed expectations"
            ) from exc
        if any(part == ".." for part in candidate.parts):
            raise GoldenWriteRefusedError("an observed path must not traverse upward")


def _assert_safe_segment(value: str, field_name: str) -> None:
    """One path segment, with no traversal and no separators."""
    if not value or value in (".", ".."):
        raise GoldenWriteRefusedError(f"{field_name} must be a non-trivial path segment")
    if "/" in value or "\\" in value or "\x00" in value:
        raise GoldenWriteRefusedError(
            f"{field_name} must be a single path segment with no separators"
        )
