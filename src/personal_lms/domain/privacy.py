from __future__ import annotations

from enum import StrEnum


class PrivacyClassification(StrEnum):
    """Bounded privacy tiers that gate whether data may reach a hosted provider.

    ``restricted_local_only`` must never be eligible for hosted routing;
    enforcing that rule is the router's job, not this enum's.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
    RESTRICTED_LOCAL_ONLY = "restricted_local_only"
