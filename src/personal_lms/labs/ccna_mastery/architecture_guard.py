"""Runtime architecture guard for ``G1-FX-09``.

The traceability requirement — "the local fixture extractor remains a
narrow searchable-PDF/PNG adapter and does not introduce a broad ingestion
schema migration or parallel extraction service" — was previously reported
``NOT_RUN`` unconditionally, with the reason that architecture shape is
"asserted by tests, not something a gate run can observe about itself."
That is true of *taste*; it is not true of *shape*. A module's public
surface, its imports, and whether it defines a competing schema migration
are all things a running process can introspect deterministically, without
executing any of the adapter's actual extraction logic.

This module is that introspection, shared by the test named in
``LINCHPIN_TRACEABILITY.md`` (``test_adapter_uses_existing_extraction_contracts``)
and by the Gate 1 runtime check, so there is exactly one definition of
"narrow" rather than a test copy that could drift from what the gate
actually verifies.

``check_extraction_adapter_is_narrow`` only ever inspected
``local_fixture.py`` itself. Independent review (2026-07-28) found that
insufficient: it can catch a widened public surface or a forbidden import
*inside that one module*, but a parallel extraction service, a broad
ingestion schema migration, or a dependency entanglement dropped anywhere
else in the change set was invisible to it.
``check_repository_has_no_parallel_extraction_service`` closes that gap —
deterministically, from git state and AST/path inspection, never from
prose — by scanning every ``.py`` file that differs from the reviewed base
revision (tracked diff plus untracked files) or newly exists in the
``extraction`` package, and applying the same narrow signals to each one
that looks extraction-shaped.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from dataclasses import dataclass
from pathlib import Path

import personal_lms
from personal_lms.extraction import local_fixture

__all__ = [
    "ArchitectureGuardResult",
    "ArchitectureGuardViolation",
    "EXPECTED_LOCAL_FIXTURE_EXTRACTOR_PUBLIC_MEMBERS",
    "FORBIDDEN_IMPORT_MODULES",
    "FORBIDDEN_IMPORTED_NAMES",
    "FORBIDDEN_SOURCE_TOKENS",
    "RepositoryArchitectureScanResult",
    "check_extraction_adapter_is_narrow",
    "check_repository_has_no_parallel_extraction_service",
]

#: The exact public surface a narrow searchable-PDF/PNG adapter needs.
#: Sorted, so a new public method — the concrete shape "broad ingestion
#: service" would take — changes this set and is caught rather than
#: silently accepted because *some* of the old methods still exist.
EXPECTED_LOCAL_FIXTURE_EXTRACTOR_PUBLIC_MEMBERS: tuple[str, ...] = (
    "extract_region",
    "extractor_id",
    "extractor_version",
    "limits",
    "read_and_verify",
    "supports",
)

#: An actual dependency on the pre-existing, general-purpose
#: ``ExtractionQueue`` (``extraction/sqlite.py``: ``extraction_jobs``,
#: ``extracted_artifacts``, ``extraction_job_events``) would mean the
#: fixture adapter and the general ingestion pipeline have become one
#: entangled thing instead of two independent ones. Checked as *imports*
#: via the AST, not as a raw substring search — this module's own
#: docstring names ``ExtractionQueue`` specifically to say the adapter
#: does *not* use it, and a text search would flag that sentence as a
#: violation of the very property it is documenting.
FORBIDDEN_IMPORT_MODULES: tuple[str, ...] = ("personal_lms.extraction.sqlite",)
FORBIDDEN_IMPORTED_NAMES: tuple[str, ...] = ("ExtractionQueue",)

#: Raw SQL schema markers. Unlike the import check above, these are safe
#: to search for as plain text: a narrow PDF/PNG adapter's own docstrings
#: have no legitimate reason to contain literal DDL syntax, so a match
#: here means the module defines a competing persistence schema.
FORBIDDEN_SOURCE_TOKENS: tuple[str, ...] = ("CREATE TABLE", "schema_migrations")


class ArchitectureGuardViolation(Exception):
    """The extraction adapter's shape no longer matches the narrow contract."""


@dataclass(frozen=True, slots=True)
class ArchitectureGuardResult:
    """Deterministic, auditable evidence the guard actually inspected something."""

    public_members: tuple[str, ...]
    module_file: str
    source_length: int


def _forbidden_imports(source: str) -> list[str]:
    """Actual ``import``/``from ... import`` statements naming a forbidden
    module or symbol — never a prose mention in a comment or docstring."""
    tree = ast.parse(source)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORT_MODULES:
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in FORBIDDEN_IMPORT_MODULES:
                violations.append(f"from {module} import ...")
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORTED_NAMES:
                    violations.append(f"from {module} import {alias.name}")
    return violations


def check_extraction_adapter_is_narrow() -> ArchitectureGuardResult:
    """Verify the fixture extractor's shape, or raise.

    Three checks, each structural rather than behavioural:

    1. ``LocalFixtureExtractor``'s public surface is exactly the expected
       set — no more, no fewer.
    2. It does not *import* the pre-existing general extraction queue, and
       its source contains no raw SQL schema-migration marker.
    3. The class actually has a docstring-declared narrow purpose (a
       structural sanity check that this is still the module being
       inspected, not an empty stub that would trivially "pass").
    """
    extractor_cls = local_fixture.LocalFixtureExtractor
    public_members = tuple(
        sorted(
            name
            for name, member in vars(extractor_cls).items()
            if not name.startswith("_")
            and (inspect.isfunction(member) or isinstance(member, property))
        )
    )
    if public_members != EXPECTED_LOCAL_FIXTURE_EXTRACTOR_PUBLIC_MEMBERS:
        raise ArchitectureGuardViolation(
            "LocalFixtureExtractor's public surface changed: expected "
            f"{EXPECTED_LOCAL_FIXTURE_EXTRACTOR_PUBLIC_MEMBERS}, found {public_members}"
        )

    source = inspect.getsource(local_fixture)

    forbidden_imports = _forbidden_imports(source)
    if forbidden_imports:
        raise ArchitectureGuardViolation(
            f"local_fixture.py imports the general extraction pipeline: {forbidden_imports}; "
            "a narrow fixture adapter must not depend on the pre-existing ExtractionQueue"
        )

    sql_markers = [token for token in FORBIDDEN_SOURCE_TOKENS if token in source]
    if sql_markers:
        raise ArchitectureGuardViolation(
            f"local_fixture.py contains SQL schema marker(s) {sql_markers}; a narrow "
            "fixture adapter must not define its own persistence schema"
        )

    if not extractor_cls.__doc__:
        raise ArchitectureGuardViolation(
            "LocalFixtureExtractor has no class docstring; the guard cannot confirm "
            "it is inspecting a documented, reviewed adapter"
        )

    return ArchitectureGuardResult(
        public_members=public_members,
        module_file=str(inspect.getfile(local_fixture)),
        source_length=len(source),
    )


#: The extraction package's own file set, as of this repair. A ``.py`` file
#: appearing here that is not in this set was added without the review this
#: guard exists to stand in for — flagged unconditionally, before any
#: content inspection.
_REVIEWED_EXTRACTION_PACKAGE_FILES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "artifacts.py",
        "errors.py",
        "fake.py",
        "local_fixture.py",
        "protocol.py",
        "sqlite.py",
    }
)

#: Third-party library roots this project declares only for PDF/PNG
#: extraction (see ``pyproject.toml``'s ``ccna-lab`` extra). A *new* file
#: importing either, outside the one reviewed adapter, is exactly the
#: "parallel extraction service" shape this scan exists to catch.
_EXTRACTION_LIBRARY_MODULE_ROOTS: tuple[str, ...] = ("pdfminer", "PIL")

#: Files this repository-wide scan does not apply text/import signals to.
#: ``local_fixture.py`` is the one adapter reviewed and allowed to import
#: the extraction libraries and mention ``ExtractionQueue``. This module
#: itself necessarily names every forbidden token and forbidden import in
#: its own constants and docstrings to define them — scanning its own
#: source would flag the guard for defining what it guards against, the
#: same class of false positive the module-level check above was fixed to
#: avoid.
_FILES_EXEMPT_FROM_REPOSITORY_SCAN: frozenset[str] = frozenset(
    {
        "personal_lms/extraction/local_fixture.py",
        "personal_lms/labs/ccna_mastery/architecture_guard.py",
    }
)


@dataclass(frozen=True, slots=True)
class RepositoryArchitectureScanResult:
    """Deterministic, auditable evidence of what the repository scan covered."""

    reviewed_base_revision: str
    scanned_file_count: int
    violations: tuple[str, ...] = ()


def _package_checkout_root() -> Path:
    """The checkout the *running* ``personal_lms`` package was imported from.

    Duplicated from ``wiring.executing_checkout_root`` rather than imported
    from it: ``wiring.py`` already imports this module, so importing back
    would be circular. Both derive the root the same way, from
    ``personal_lms.__file__`` — never from a caller argument, for the same
    reason ``wiring.py``'s version gives: a caller-selected root would let
    a dirty checkout report a clean one's state.
    """
    return Path(personal_lms.__file__).resolve().parents[2]


def _changed_python_files(*, repo_root: Path, base_revision: str) -> list[str]:
    """Every ``src/personal_lms/**/*.py`` path that differs from
    ``base_revision`` or is untracked, as repo-relative POSIX paths.

    Two git invocations, matching the exact pattern ``resolve_code_revision``
    already uses elsewhere in this codebase: a tracked diff misses new
    files entirely, so untracked files are enumerated separately. Reads
    git's own state only; nothing here executes any file it names.
    """
    try:
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "diff",
                "--name-only",
                "--diff-filter=ACMR",
                base_revision,
                "--",
                "src/personal_lms",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
        untracked = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "--others",
                "--exclude-standard",
                "--",
                "src/personal_lms",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ArchitectureGuardViolation(
            f"could not determine the changed file set against {base_revision!r}: {exc}"
        ) from exc

    paths = {line for line in (*tracked.splitlines(), *untracked.splitlines()) if line}
    return sorted(path for path in paths if path.endswith(".py"))


def _looks_extraction_shaped(relative_path: str, source: str) -> bool:
    """Whether a changed file is plausibly an extraction/ingestion module.

    Narrow on purpose: everything under ``extraction/`` counts by
    location; anything else counts only if it actually imports a
    PDF/PNG-parsing library or the forbidden general extraction pipeline.
    A file with no such signal — the overwhelming majority of any real
    diff — is left alone entirely, including for the SQL-marker check,
    so a legitimate new SQLite-backed store elsewhere in the codebase
    (unrelated to extraction) is never flagged for defining its own,
    properly reviewed schema.
    """
    if relative_path.startswith("personal_lms/extraction/"):
        return True
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.split(".")[0] in _EXTRACTION_LIBRARY_MODULE_ROOTS for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            module_root = (node.module or "").split(".")[0]
            if module_root in _EXTRACTION_LIBRARY_MODULE_ROOTS:
                return True
    return bool(_forbidden_imports(source))


def check_repository_has_no_parallel_extraction_service(
    *, repo_root: Path | None = None, base_revision: str = "HEAD"
) -> RepositoryArchitectureScanResult:
    """Verify no part of the current change set adds a competing extraction
    pipeline anywhere in the repository, or raise.

    Two checks, both structural:

    1. The ``extraction`` package's own file set is exactly the reviewed
       one — a new file there is flagged before any content is even read.
    2. Every other changed ``.py`` file under ``src/personal_lms`` that
       looks extraction-shaped (see ``_looks_extraction_shaped``) carries
       no forbidden import and no SQL schema marker.

    ``base_revision`` defaults to ``HEAD`` rather than a hardcoded commit:
    the entire Gate 1 implementation is currently uncommitted working-tree
    state, so "the change set" is everything not yet part of the last real
    commit — precisely what ``git diff``/``git ls-files`` against ``HEAD``
    already means elsewhere in this codebase (see
    ``wiring.resolve_code_revision``). Once this repair is committed,
    ``HEAD`` simply becomes the new baseline, and the guard keeps working
    against whatever changes next.
    """
    root = repo_root if repo_root is not None else _package_checkout_root()

    package_dir = root / "src" / "personal_lms" / "extraction"
    actual_files = {path.name for path in package_dir.glob("*.py")}
    unexpected_package_files = sorted(actual_files - _REVIEWED_EXTRACTION_PACKAGE_FILES)

    violations: list[str] = []
    if unexpected_package_files:
        violations.append(
            f"unreviewed file(s) present in the extraction package: {unexpected_package_files}"
        )

    changed = _changed_python_files(repo_root=root, base_revision=base_revision)
    for relative_path in changed:
        if relative_path in _FILES_EXEMPT_FROM_REPOSITORY_SCAN:
            continue
        absolute = root / "src" / relative_path
        if not absolute.is_file():
            continue  # deleted since base_revision; nothing left to scan
        source = absolute.read_text(encoding="utf-8")
        if not _looks_extraction_shaped(relative_path, source):
            continue

        forbidden_imports = _forbidden_imports(source)
        if forbidden_imports:
            violations.append(
                f"{relative_path} imports the general extraction pipeline: {forbidden_imports}"
            )
        sql_markers = [token for token in FORBIDDEN_SOURCE_TOKENS if token in source]
        if sql_markers:
            violations.append(f"{relative_path} contains SQL schema marker(s) {sql_markers}")

    if violations:
        raise ArchitectureGuardViolation(
            f"the repository change set against {base_revision!r} introduces a parallel "
            f"extraction service or schema migration: {violations}"
        )

    return RepositoryArchitectureScanResult(
        reviewed_base_revision=base_revision,
        scanned_file_count=len(changed),
        violations=(),
    )
