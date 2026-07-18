from personal_lms.vault.errors import (
    InvalidApprovalError,
    NotePathConflictError,
    OverwriteNotConfirmedError,
    VaultError,
)
from personal_lms.vault.fake import FakeObsidianVault
from personal_lms.vault.protocol import (
    ObsidianVault,
    compute_approval_digest,
    compute_content_hash,
)

__all__ = [
    "FakeObsidianVault",
    "InvalidApprovalError",
    "NotePathConflictError",
    "ObsidianVault",
    "OverwriteNotConfirmedError",
    "VaultError",
    "compute_approval_digest",
    "compute_content_hash",
]
