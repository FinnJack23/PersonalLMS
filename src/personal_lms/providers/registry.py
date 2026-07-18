from __future__ import annotations

from dataclasses import dataclass

from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelCapabilityProfile
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.providers.errors import ProviderAlreadyRegisteredError, ProviderNotFoundError
from personal_lms.providers.protocol import ModelProvider


def _rank[EnumT: (LatencyClass, CostClass, PrivacyClassification)](
    enum_cls: type[EnumT], member: EnumT
) -> int:
    """Position of ``member`` in its enum's declaration order.

    All three ordered enums (``LatencyClass``, ``CostClass``,
    ``PrivacyClassification``) are declared least-to-most restrictive/costly,
    so declaration order doubles as the comparison order here.
    """
    return list(enum_cls).index(member)


@dataclass(frozen=True, slots=True)
class CapabilityFilter:
    """Criteria for narrowing candidate providers.

    Identifies compatible candidates only — it does not rank or choose among
    them. That decision belongs to the router (Commit 4).
    """

    requires_reasoning: bool = False
    requires_vision: bool = False
    min_context_tokens: int = 0
    local_only: bool = False
    privacy_classification: PrivacyClassification | None = None
    latency_class: LatencyClass | None = None
    cost_class: CostClass | None = None


def _profile_matches(profile: ModelCapabilityProfile, criteria: CapabilityFilter) -> bool:
    if criteria.requires_reasoning and not profile.supports_reasoning:
        return False
    if criteria.requires_vision and not profile.supports_vision:
        return False
    if profile.max_context_tokens < criteria.min_context_tokens:
        return False
    if criteria.privacy_classification is not None and _rank(
        PrivacyClassification, profile.max_privacy_classification
    ) < _rank(PrivacyClassification, criteria.privacy_classification):
        return False
    if criteria.latency_class is not None and _rank(LatencyClass, profile.latency_class) > _rank(
        LatencyClass, criteria.latency_class
    ):
        return False
    if criteria.cost_class is None:
        return True
    return _rank(CostClass, profile.cost_class) <= _rank(CostClass, criteria.cost_class)


def _provider_matches(provider: ModelProvider, criteria: CapabilityFilter) -> bool:
    if criteria.local_only and not provider.is_local:
        return False
    return any(_profile_matches(profile, criteria) for profile in provider.capability_profiles)


class ProviderRegistry:
    """In-memory provider directory.

    Independent of routing policy, CrewAI, and dependency-injection
    frameworks. It identifies candidates; it never decides which one
    executes a request — that is Commit 4's job.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        if provider.provider_id in self._providers:
            raise ProviderAlreadyRegisteredError(provider.provider_id)
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> ModelProvider:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise ProviderNotFoundError(provider_id) from None

    def list_providers(self) -> tuple[ModelProvider, ...]:
        return tuple(sorted(self._providers.values(), key=lambda p: p.provider_id))

    def find(self, criteria: CapabilityFilter) -> tuple[ModelProvider, ...]:
        return tuple(p for p in self.list_providers() if _provider_matches(p, criteria))
