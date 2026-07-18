from __future__ import annotations

import pytest
from pydantic import ValidationError

from personal_lms.domain import SourceCitation


def test_source_citation_valid_construction() -> None:
    citation = SourceCitation(source_id="src-00001234", title="Routing Concepts Module 14")
    assert citation.approved is False
    assert citation.location is None


def test_source_citation_rejects_empty_title() -> None:
    with pytest.raises(ValidationError):
        SourceCitation(source_id="src-1", title="")


def test_source_citation_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SourceCitation(source_id="src-1", title="Module 14", url="https://example.com")  # type: ignore[call-arg]


def test_source_citation_json_round_trip() -> None:
    citation = SourceCitation(source_id="src-1", title="Module 14", location="p.12", approved=True)
    restored = SourceCitation.model_validate_json(citation.model_dump_json())
    assert restored == citation
