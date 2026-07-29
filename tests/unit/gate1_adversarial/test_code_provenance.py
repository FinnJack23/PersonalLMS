"""Code provenance: the revision must identify the code that actually ran.

Covers red items 7–9.

Two reproduced defects:

- ``git diff HEAD`` covers tracked modifications only, so the whole Gate 1
  implementation — every file still untracked — contributed *nothing* to
  the dirty digest. Two materially different implementations produced the
  same revision string while both claiming to be "dirty".
- ``--project-root`` was caller-selected and used for both artifact paths
  and revision resolution, so pointing it at the canonical clean checkout
  made dirty Gate 1 code report ``main``'s clean SHA.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import personal_lms
from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root, resolve_code_revision

EXECUTING_PACKAGE_ROOT = Path(personal_lms.__file__).resolve().parent


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _find_other_main_worktree() -> Path | None:
    """A sibling worktree checked out on ``main``, if one exists here.

    Located through ``git worktree list --porcelain`` rather than a
    hardcoded path, so this test is portable across machines and CI —
    where no such sibling worktree may exist at all, in which case the
    caller should skip rather than fail.
    """
    executing = executing_checkout_root()
    output = git("worktree", "list", "--porcelain", cwd=executing)

    current_path: Path | None = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree "))
        elif (
            line == "branch refs/heads/main"
            and current_path is not None
            and current_path.resolve() != executing
        ):
            return current_path
    return None


class TestRevisionCoversExecutingSource:
    def test_untracked_executing_files_change_the_revision(self, tmp_path: Path) -> None:
        """The Gate 1 implementation is entirely untracked; it must count."""
        baseline = resolve_code_revision()

        scratch = EXECUTING_PACKAGE_ROOT / "_provenance_probe.py"
        scratch.write_text("# temporary provenance probe\n", encoding="utf-8")
        try:
            perturbed = resolve_code_revision()
        finally:
            scratch.unlink(missing_ok=True)

        assert perturbed != baseline

    def test_editing_an_untracked_executing_file_changes_the_revision(self) -> None:
        target = EXECUTING_PACKAGE_ROOT / "objective_packs" / "scoring.py"
        original = target.read_text(encoding="utf-8")
        baseline = resolve_code_revision()

        target.write_text(original + "\n# provenance probe\n", encoding="utf-8")
        try:
            perturbed = resolve_code_revision()
        finally:
            target.write_text(original, encoding="utf-8")

        assert perturbed != baseline

    def test_the_revision_is_never_the_unversioned_sentinel(self) -> None:
        revision = resolve_code_revision()

        assert revision
        assert revision != "unversioned"

    def test_a_dirty_tree_is_marked_dirty(self) -> None:
        """An untracked file in the executing package must mark the tree dirty.

        Creates and cleans up its own probe rather than assuming ambient
        dirty state, so this test also passes once the repository is
        committed and the working tree is clean.
        """
        scratch = EXECUTING_PACKAGE_ROOT / "_dirty_marker_probe.py"
        scratch.write_text("# temporary dirty-marker probe\n", encoding="utf-8")
        try:
            assert "dirty" in resolve_code_revision()
        finally:
            scratch.unlink(missing_ok=True)


class TestProvenanceCannotBeSpoofed:
    def test_resolve_code_revision_takes_no_caller_chosen_root(self) -> None:
        """A caller-selected root let dirty code claim a clean SHA."""
        import inspect

        signature = inspect.signature(resolve_code_revision)

        assert not signature.parameters, (
            "resolve_code_revision must derive its root from the executing package, "
            f"not accept one; got parameters {list(signature.parameters)}"
        )

    def test_the_revision_reflects_this_checkout_not_a_sibling_main_checkout(self) -> None:
        """The original defect: pointing resolution at another checkout
        let dirty code claim that checkout's clean revision.

        Skips when no sibling ``main`` worktree exists (a fresh single-tree
        CI checkout, for example) rather than failing — the property under
        test is "this resolves against my own tree, not some other one",
        which needs another tree to actually exist to be exercised.
        """
        other = _find_other_main_worktree()
        if other is None:
            pytest.skip("no sibling main worktree present to compare against")

        other_head = git("rev-parse", "HEAD", cwd=other)

        assert resolve_code_revision() != other_head

    def test_the_resolved_root_contains_the_executing_package(self) -> None:
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        root = executing_checkout_root()

        assert EXECUTING_PACKAGE_ROOT.is_relative_to(root)


class TestArtifactRootBelongsToTheExecutingCheckout:
    def test_an_artifact_root_outside_the_executing_checkout_is_refused(
        self, tmp_path: Path
    ) -> None:
        from personal_lms.labs.ccna_mastery.gates import GateArtifactPaths

        with pytest.raises(ValueError, match="executing"):
            GateArtifactPaths.for_project_root(tmp_path, require_executing_checkout=True)

    def test_the_executing_checkout_is_accepted(self) -> None:
        from personal_lms.labs.ccna_mastery.gates import GateArtifactPaths
        from personal_lms.labs.ccna_mastery.wiring import executing_checkout_root

        paths = GateArtifactPaths.for_project_root(
            executing_checkout_root(), require_executing_checkout=True
        )

        assert paths.observed_root.is_relative_to(executing_checkout_root())
