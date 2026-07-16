from __future__ import annotations

from pydantic import Field

from personal_lms.domain.base import StrictModel


class SourceCitation(StrictModel):
    """A reference to a specific location within a source.

    Presence of a citation does not imply the cited source has been
    retrieved, verified, or promoted to canonical status — see ``approved``.
    """

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str | None = Field(
        default=None,
        description="Page, timestamp, section, or URL fragment within the source.",
    )
    approved: bool = Field(
        default=False,
        description="Whether the cited source has completed human review/promotion.",
    )
