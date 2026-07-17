from __future__ import annotations

from personal_lms.vault import FakeObsidianVault, ObsidianVault


def test_fake_obsidian_vault_satisfies_protocol() -> None:
    vault = FakeObsidianVault()
    try:
        assert isinstance(vault, ObsidianVault)
    finally:
        vault.close()


def test_object_missing_commit_write_does_not_satisfy_protocol() -> None:
    class _NotAVault:
        def read_note(self, request: object) -> object: ...
        def list_notes(self, request: object = None) -> object: ...
        def prepare_note_write(self, request: object) -> object: ...
        def prepare_attachment_association(self, request: object) -> object: ...
        def reject_write(
            self, plan: object, *, reason: str, rejected_by: object = None
        ) -> object: ...
        def close(self) -> None: ...

    assert not isinstance(_NotAVault(), ObsidianVault)
