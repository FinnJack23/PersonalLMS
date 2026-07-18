from __future__ import annotations

import asyncio
from collections.abc import Sequence
from decimal import Decimal

from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest, ModelResult
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.providers.errors import ProviderError


def _default_local_profile() -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        profile_id="fake_local_general",
        supports_reasoning=True,
        supports_vision=False,
        max_context_tokens=8192,
        is_local=True,
        max_privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
        latency_class=LatencyClass.STANDARD,
        cost_class=CostClass.FREE,
    )


def _default_hosted_profile() -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        profile_id="fake_hosted_reasoning",
        supports_reasoning=True,
        supports_vision=True,
        max_context_tokens=128_000,
        is_local=False,
        max_privacy_classification=PrivacyClassification.PUBLIC,
        latency_class=LatencyClass.STANDARD,
        cost_class=CostClass.MEDIUM,
    )


def _validate_common(
    provider_id: str,
    capability_profiles: tuple[ModelCapabilityProfile, ...],
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    cost_per_call_usd: Decimal,
) -> None:
    if not provider_id:
        raise ValueError("provider_id must not be empty")
    if not capability_profiles:
        raise ValueError("capability_profiles must not be empty")
    if input_tokens < 0:
        raise ValueError("input_tokens must not be negative")
    if output_tokens < 0:
        raise ValueError("output_tokens must not be negative")
    if latency_ms < 0:
        raise ValueError("latency_ms must not be negative")
    if cost_per_call_usd < 0:
        raise ValueError("cost_per_call_usd must not be negative")


async def _fake_generate(
    request: ModelRequest,
    *,
    capability_profile_id: str,
    is_local: bool,
    output_text: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    finish_reason: str,
    fail_with: ProviderError | None,
) -> ModelResult:
    if fail_with is not None:
        raise fail_with
    await asyncio.sleep(0)
    return ModelResult(
        request_id=request.request_id,
        capability_profile=capability_profile_id,
        is_local=is_local,
        output_text=output_text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
    )


class FakeLocalProvider:
    """Deterministic local-tier test double.

    No network, filesystem, Obsidian, or environment-variable access. Never
    claims to be a real product such as Qwen or Ollama.
    """

    def __init__(
        self,
        provider_id: str = "fake-local",
        *,
        capability_profiles: Sequence[ModelCapabilityProfile] | None = None,
        output_text: str = "This is a deterministic fake local response.",
        input_tokens: int = 10,
        output_tokens: int = 10,
        latency_ms: float = 5.0,
        finish_reason: str = "stop",
        cost_per_call_usd: Decimal = Decimal("0"),
        fail_with: ProviderError | None = None,
    ) -> None:
        profiles = (
            tuple(capability_profiles) if capability_profiles else (_default_local_profile(),)
        )
        _validate_common(
            provider_id, profiles, input_tokens, output_tokens, latency_ms, cost_per_call_usd
        )

        self._provider_id = provider_id
        self._capability_profiles = profiles
        self.output_text = output_text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.latency_ms = latency_ms
        self.finish_reason = finish_reason
        self.cost_per_call_usd = cost_per_call_usd
        self.fail_with = fail_with

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capability_profiles(self) -> tuple[ModelCapabilityProfile, ...]:
        return self._capability_profiles

    @property
    def is_local(self) -> bool:
        return True

    async def generate(self, request: ModelRequest) -> ModelResult:
        return await _fake_generate(
            request,
            capability_profile_id=self._capability_profiles[0].profile_id,
            is_local=True,
            output_text=self.output_text,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=self.latency_ms,
            finish_reason=self.finish_reason,
            fail_with=self.fail_with,
        )


class FakeHostedProvider:
    """Deterministic hosted-tier test double.

    No network, filesystem, Obsidian, or environment-variable access. Never
    claims to be a real product such as OpenAI, Anthropic, or Gemini.
    """

    def __init__(
        self,
        provider_id: str = "fake-hosted",
        *,
        capability_profiles: Sequence[ModelCapabilityProfile] | None = None,
        output_text: str = "This is a deterministic fake hosted response.",
        input_tokens: int = 10,
        output_tokens: int = 10,
        latency_ms: float = 50.0,
        finish_reason: str = "stop",
        cost_per_call_usd: Decimal = Decimal("0.01"),
        fail_with: ProviderError | None = None,
    ) -> None:
        profiles = (
            tuple(capability_profiles) if capability_profiles else (_default_hosted_profile(),)
        )
        _validate_common(
            provider_id, profiles, input_tokens, output_tokens, latency_ms, cost_per_call_usd
        )

        self._provider_id = provider_id
        self._capability_profiles = profiles
        self.output_text = output_text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.latency_ms = latency_ms
        self.finish_reason = finish_reason
        self.cost_per_call_usd = cost_per_call_usd
        self.fail_with = fail_with

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capability_profiles(self) -> tuple[ModelCapabilityProfile, ...]:
        return self._capability_profiles

    @property
    def is_local(self) -> bool:
        return False

    async def generate(self, request: ModelRequest) -> ModelResult:
        return await _fake_generate(
            request,
            capability_profile_id=self._capability_profiles[0].profile_id,
            is_local=False,
            output_text=self.output_text,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=self.latency_ms,
            finish_reason=self.finish_reason,
            fail_with=self.fail_with,
        )
