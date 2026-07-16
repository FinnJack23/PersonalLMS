from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


def utcnow() -> datetime:
    """Timezone-aware current time, used as a default factory across the domain."""
    return datetime.now(UTC)


class StrictModel(BaseModel):
    """Shared base for all Personal LMS domain schemas.

    Extra fields are forbidden and assignments are re-validated so that
    schemas stay safe to pass between agents, flows, and provider adapters
    without silently accepting unknown or malformed data.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
    )
