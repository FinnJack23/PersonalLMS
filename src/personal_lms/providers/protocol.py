from __future__ import annotations

from typing import Protocol, runtime_checkable

from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest, ModelResult


@runtime_checkable
class ModelProvider(Protocol):
    """Structural contract for any inference backend: local, hosted, or fake.

    Provider identity lives here for registry and audit purposes only (see
    ADR-0002) — agent identities, capability requests, and domain schemas
    stay vendor-neutral. Nothing outside the provider layer should import a
    concrete provider class or a vendor name.
    """

    @property
    def provider_id(self) -> str: ...

    @property
    def capability_profiles(self) -> tuple[ModelCapabilityProfile, ...]: ...

    @property
    def is_local(self) -> bool: ...

    async def generate(self, request: ModelRequest) -> ModelResult: ...
