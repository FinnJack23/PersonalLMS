from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_lms.domain import SourceCitation, VaultNoteDraft


def test_vault_note_draft_valid_construction() -> None:
    draft = VaultNoteDraft(
        title="Longest Prefix Match",
        relative_path="03-Concepts/longest-prefix-match.md",
        frontmatter={"topics": ["routing-table", "longest-prefix-match"]},
        body_markdown="# Longest Prefix Match\n\nThe router selects the most specific match.",
        citations=[SourceCitation(source_id="src-1", title="Module 14")],
    )
    assert draft.created_at.tzinfo is not None


def test_vault_note_draft_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        VaultNoteDraft(title="x", relative_path="/etc/passwd", body_markdown="content")


def test_vault_note_draft_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        VaultNoteDraft(
            title="x",
            relative_path="../../etc/passwd",
            body_markdown="content",
        )


def test_vault_note_draft_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        VaultNoteDraft(
            title="x",
            relative_path="01-Inbox/x.md",
            body_markdown="content",
            vault_path="/home/alan/vault",  # type: ignore[call-arg]
        )


def test_vault_note_draft_has_no_filesystem_effect(tmp_path: Path) -> None:
    draft = VaultNoteDraft(title="x", relative_path="01-Inbox/x.md", body_markdown="content")

    draft.model_dump_json()
    draft.model_dump()

    assert list(tmp_path.iterdir()) == []
    assert not hasattr(draft, "write")
    assert not hasattr(draft, "save")


def test_vault_note_draft_json_round_trip() -> None:
    draft = VaultNoteDraft(
        title="Longest Prefix Match",
        relative_path="03-Concepts/longest-prefix-match.md",
        frontmatter={"course": "D419", "accuracy": 0.8, "topics": ["longest-prefix-match"]},
        body_markdown="# Longest Prefix Match",
    )
    restored = VaultNoteDraft.model_validate_json(draft.model_dump_json())
    assert restored == draft
