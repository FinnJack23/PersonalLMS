"""Typed errors for Obsidian vault access.

Messages carry only plan/approval identifiers, a path, and a short
reason — never note body content or citation text — mirroring
``ProviderError``'s messaging conventions.
"""

from __future__ import annotations

from uuid import UUID


class VaultError(Exception):
    """Base class for all Obsidian vault access errors."""


class InvalidApprovalError(VaultError):
    """Raised when an approval does not authorize committing a specific plan."""

    def __init__(self, plan_id: UUID, reason: str) -> None:
        super().__init__(f"Approval does not authorize plan {plan_id!r}: {reason}")
        self.plan_id = plan_id
        self.reason = reason


class OverwriteNotConfirmedError(VaultError):
    """Raised when a plan requires overwrite but its approval did not confirm it."""

    def __init__(self, plan_id: UUID) -> None:
        super().__init__(
            f"Plan {plan_id!r} requires overwrite, but its approval did not "
            "set overwrite_confirmed=True"
        )
        self.plan_id = plan_id


class NotePathConflictError(VaultError):
    """Raised when committing would silently replace existing content
    without explicit overwrite intent."""

    def __init__(self, relative_path: str) -> None:
        super().__init__(
            f"A note already exists at {relative_path!r}; set overwrite=True on "
            "the write plan (and confirm it in the approval) to replace it"
        )
        self.relative_path = relative_path
