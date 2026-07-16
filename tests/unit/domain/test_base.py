from __future__ import annotations

import pytest
from pydantic import ValidationError

from personal_lms.domain.base import StrictModel


class _Sample(StrictModel):
    name: str
    count: int = 0


def test_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _Sample(name="a", unexpected="nope")  # type: ignore[call-arg]


def test_strips_surrounding_whitespace() -> None:
    sample = _Sample(name="  padded  ")
    assert sample.name == "padded"


def test_validates_on_assignment() -> None:
    sample = _Sample(name="a")
    with pytest.raises(ValidationError):
        sample.count = "not-a-number"  # type: ignore[assignment]
