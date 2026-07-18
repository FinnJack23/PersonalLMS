from __future__ import annotations

from pydantic import AwareDatetime, Field, field_validator

from personal_lms.domain.base import StrictModel, utcnow
from personal_lms.domain.citations import SourceCitation

FrontmatterValue = str | int | float | bool | list[str] | None
Frontmatter = dict[str, FrontmatterValue]


class VaultNoteDraft(StrictModel):
    """A proposed Obsidian note: Markdown and frontmatter content only.

    This is a pure data object. It has no write method and never touches a
    real vault or the filesystem — see the Obsidian safe-access layer for
    the controlled writer that eventually consumes drafts like this one.
    """

    title: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    frontmatter: Frontmatter = Field(default_factory=dict)
    body_markdown: str = Field(min_length=1)
    citations: list[SourceCitation] = Field(default_factory=list)
    created_at: AwareDatetime = Field(default_factory=utcnow)

    @field_validator("relative_path")
    @classmethod
    def _no_absolute_path_or_traversal(cls, value: str) -> str:
        if value.startswith("/") or value.startswith("~"):
            raise ValueError("relative_path must be relative, not absolute")
        if ".." in value.split("/"):
            raise ValueError("relative_path must not contain '..' segments")
        return value
