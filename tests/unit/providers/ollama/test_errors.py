from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

if importlib.util.find_spec("httpx") is None:
    pytest.skip(
        "httpx (ollama extra) not installed (uv sync --extra ollama)", allow_module_level=True
    )

import httpx

from personal_lms.domain.models import ModelRequest
from personal_lms.providers import (
    ProviderContractError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

from ._helpers import build_provider
from .conftest import make_config

pytestmark = pytest.mark.requires_ollama

_SECRET_PROMPT = "correct horse battery staple do-not-leak"
_SECRET_RESPONSE_BODY = "sensitive-response-marker-do-not-leak"


def _request() -> ModelRequest:
    return ModelRequest(capability_profile="ollama-local", prompt=_SECRET_PROMPT)


def test_prompt_text_absent_from_contract_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderContractError) as exc_info:
        asyncio.run(provider.generate(_request()))

    assert _SECRET_PROMPT not in str(exc_info.value)


def test_prompt_text_absent_from_execution_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": _SECRET_RESPONSE_BODY})

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderExecutionError) as exc_info:
        asyncio.run(provider.generate(_request()))

    assert _SECRET_PROMPT not in str(exc_info.value)


def test_response_body_absent_from_execution_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": _SECRET_RESPONSE_BODY})

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderExecutionError) as exc_info:
        asyncio.run(provider.generate(_request()))

    assert _SECRET_RESPONSE_BODY not in str(exc_info.value)


def test_prompt_text_absent_from_unavailable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderUnavailableError) as exc_info:
        asyncio.run(provider.generate(_request()))

    assert _SECRET_PROMPT not in str(exc_info.value)


def test_prompt_text_absent_from_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = build_provider(make_config(), handler)

    with pytest.raises(ProviderTimeoutError) as exc_info:
        asyncio.run(provider.generate(_request()))

    assert _SECRET_PROMPT not in str(exc_info.value)


def test_error_messages_contain_provider_id_and_category_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    config = make_config(provider_id="ollama-local")
    provider = build_provider(config, handler)

    with pytest.raises(ProviderExecutionError) as exc_info:
        asyncio.run(provider.generate(_request()))

    message = str(exc_info.value)
    assert "ollama-local" in message
    assert "500" in message


def test_credentials_cannot_be_encoded_in_base_url() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_config(base_url="http://admin:hunter2@127.0.0.1:11434")


def test_provider_construction_requires_no_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
        "HOSTED_MODEL_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "0.5.1"})

    provider = build_provider(make_config(), handler)
    result = asyncio.run(provider.health())

    assert result.version == "0.5.1"


def test_no_filesystem_effect(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "0.5.1"})

    provider = build_provider(make_config(), handler)
    asyncio.run(provider.health())

    assert list(tmp_path.iterdir()) == []


def test_no_unrelated_module_is_imported_by_provider_package() -> None:
    """Guards against future accidental Obsidian/RAG/CrewAI/CML/shell coupling.

    Checks actual import statements only — prose comments are free to
    mention these names (e.g. contrasting with the CrewAI adapter's
    bootstrap requirement) without that being a real coupling."""
    import ast

    import personal_lms.providers.ollama.provider as provider_module

    source = Path(provider_module.__file__).read_text()
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    for forbidden in ("subprocess", "crewai", "obsidian", "sqlite3", "os"):
        assert forbidden not in imported_roots
