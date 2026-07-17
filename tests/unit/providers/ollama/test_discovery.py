from __future__ import annotations

import asyncio
import importlib.util

import pytest

if importlib.util.find_spec("httpx") is None:
    pytest.skip(
        "httpx (ollama extra) not installed (uv sync --extra ollama)", allow_module_level=True
    )

import httpx

from personal_lms.providers import (
    ProviderContractError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

from ._helpers import build_provider
from .conftest import make_config

pytestmark = pytest.mark.requires_ollama


def test_health_returns_typed_version_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/version"
        return httpx.Response(200, json={"version": "0.5.1"})

    provider = build_provider(make_config(), handler)
    result = asyncio.run(provider.health())

    assert result.version == "0.5.1"


def test_health_rejects_malformed_version_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderContractError):
        asyncio.run(provider.health())


def test_health_maps_connection_error_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(provider.health())


def test_health_maps_timeout_to_provider_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderTimeoutError):
        asyncio.run(provider.health())


def test_list_models_sorts_deterministically() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "zeta:latest"},
                    {"name": "alpha:latest"},
                    {"name": "mid:latest"},
                ]
            },
        )

    provider = build_provider(make_config(), handler)
    models = asyncio.run(provider.list_models())

    assert [m.name for m in models] == ["alpha:latest", "mid:latest", "zeta:latest"]


def test_list_models_tolerates_missing_optional_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "bare:latest"}]})

    provider = build_provider(make_config(), handler)
    models = asyncio.run(provider.list_models())

    assert len(models) == 1
    assert models[0].name == "bare:latest"
    assert models[0].digest is None
    assert models[0].size_bytes is None
    assert models[0].parameter_size is None
    assert models[0].quantization_level is None
    assert models[0].family is None


def test_list_models_preserves_present_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "qwen2.5:7b",
                        "digest": "sha256:abcdef",
                        "size": 4_800_000_000,
                        "details": {
                            "family": "qwen2",
                            "parameter_size": "7.6B",
                            "quantization_level": "Q4_K_M",
                        },
                    }
                ]
            },
        )

    provider = build_provider(make_config(), handler)
    models = asyncio.run(provider.list_models())

    assert models[0].name == "qwen2.5:7b"
    assert models[0].digest == "sha256:abcdef"
    assert models[0].size_bytes == 4_800_000_000
    assert models[0].parameter_size == "7.6B"
    assert models[0].quantization_level == "Q4_K_M"
    assert models[0].family == "qwen2"


def test_list_models_rejects_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"digest": "no-name-field"}]})

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderContractError):
        asyncio.run(provider.list_models())


def test_list_models_empty_list_is_not_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    provider = build_provider(make_config(), handler)
    models = asyncio.run(provider.list_models())

    assert models == ()


def test_is_model_installed_true_when_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})

    provider = build_provider(make_config(model="qwen2.5:7b"), handler)
    assert asyncio.run(provider.is_model_installed()) is True


def test_is_model_installed_false_when_absent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3:8b"}]})

    provider = build_provider(make_config(model="qwen2.5:7b"), handler)
    assert asyncio.run(provider.is_model_installed()) is False


def test_is_model_installed_checks_an_explicit_model_argument() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3:8b"}]})

    provider = build_provider(make_config(model="qwen2.5:7b"), handler)
    assert asyncio.run(provider.is_model_installed("llama3:8b")) is True
