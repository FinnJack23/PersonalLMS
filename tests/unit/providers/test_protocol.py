from __future__ import annotations

import asyncio

from personal_lms.domain.models import ModelRequest, ModelResult
from personal_lms.providers import FakeHostedProvider, FakeLocalProvider
from personal_lms.providers.protocol import ModelProvider


def test_fake_local_provider_satisfies_protocol() -> None:
    assert isinstance(FakeLocalProvider(), ModelProvider)


def test_fake_hosted_provider_satisfies_protocol() -> None:
    assert isinstance(FakeHostedProvider(), ModelProvider)


def test_object_missing_generate_does_not_satisfy_protocol() -> None:
    class _NotAProvider:
        provider_id = "x"
        capability_profiles: tuple[object, ...] = ()
        is_local = True

    assert not isinstance(_NotAProvider(), ModelProvider)


def test_generate_is_awaitable_and_returns_model_result() -> None:
    provider = FakeLocalProvider()
    request = ModelRequest(capability_profile="fake_local_general", prompt="Explain LPM.")

    result = asyncio.run(provider.generate(request))

    assert isinstance(result, ModelResult)
    assert result.request_id == request.request_id
    assert result.is_local is True


def test_hosted_provider_declares_hosted_capability() -> None:
    provider = FakeHostedProvider()
    assert provider.is_local is False
    assert all(not profile.is_local for profile in provider.capability_profiles)
