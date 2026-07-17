from __future__ import annotations

import asyncio
import importlib.util
import json

import pytest

if importlib.util.find_spec("httpx") is None:
    pytest.skip(
        "httpx (ollama extra) not installed (uv sync --extra ollama)", allow_module_level=True
    )

import httpx
from pydantic import ValidationError

from personal_lms.providers import (
    ProviderContractError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from personal_lms.providers.ollama.smoke_test import (
    _content_matches_expected,
    build_arg_parser,
    build_config,
    main,
    run_smoke_test,
)

from ._helpers import counting_handler

pytestmark = pytest.mark.requires_ollama

_EXPECTED_CONTENT = "PERSONAL_LMS_PROVIDER_OK"


def _chat_response(content: str = _EXPECTED_CONTENT, **overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 5,
        "eval_count": 3,
        "total_duration": 1_000_000_000,
    }
    defaults.update(overrides)
    return defaults


def _client(handler: httpx.MockTransport | None = None, *, base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


def _config(**overrides: object):
    from personal_lms.providers.ollama import OllamaProviderConfig

    defaults: dict[str, object] = {
        "provider_id": "ollama-smoke-test",
        "model": "qwen2.5:7b",
        "max_context_tokens": 4096,
    }
    defaults.update(overrides)
    return OllamaProviderConfig.model_validate(defaults)


# --- argument parsing / config construction ---------------------------------


def test_build_arg_parser_reads_base_url_and_model_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")

    args = build_arg_parser().parse_args([])

    assert args.base_url == "http://127.0.0.1:11434"
    assert args.model == "qwen2.5:7b"


def test_cli_flags_override_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "from-env")

    args = build_arg_parser().parse_args(["--model", "from-cli"])

    assert args.model == "from-cli"


def test_build_config_requires_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    args = build_arg_parser().parse_args([])

    with pytest.raises(ValueError, match="--model is required"):
        build_config(args)


def test_build_config_fixes_temperature_and_seed_to_zero() -> None:
    args = build_arg_parser().parse_args(["--model", "qwen2.5:7b"])

    config = build_config(args)

    assert config.temperature == 0.0
    assert config.seed == 0


def test_build_config_rejects_non_loopback_host_without_explicit_flag() -> None:
    args = build_arg_parser().parse_args(
        ["--model", "qwen2.5:7b", "--base-url", "http://172.25.16.1:11434"]
    )

    with pytest.raises(ValidationError):
        build_config(args)


def test_build_config_allows_non_loopback_host_with_explicit_flag() -> None:
    args = build_arg_parser().parse_args(
        [
            "--model",
            "qwen2.5:7b",
            "--base-url",
            "http://172.25.16.1:11434",
            "--allow-non-loopback",
        ]
    )

    config = build_config(args)

    assert config.base_url == "http://172.25.16.1:11434"


# --- run_smoke_test behavior --------------------------------------------------


def test_run_smoke_test_passes_on_exact_expected_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    client = _client(handler, base_url="http://127.0.0.1:11434")
    outcome = asyncio.run(run_smoke_test(_config(), client=client))

    assert outcome.passed is True
    assert "PASS" in outcome.message
    assert "OllamaProvider" in outcome.message
    assert "qwen2.5:7b" in outcome.message
    assert "http://127.0.0.1:11434" in outcome.message
    assert _EXPECTED_CONTENT in outcome.message


def test_run_smoke_test_sends_exactly_one_deterministic_chat_request() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response())

    wrapped, calls = counting_handler(handler)
    client = _client(wrapped, base_url="http://127.0.0.1:11434")
    asyncio.run(run_smoke_test(_config(), client=client))

    assert len(calls) == 1
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/chat"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["stream"] is False
    assert body["think"] is False
    assert body["options"] == {"temperature": 0.0, "seed": 0}


def test_run_smoke_test_never_calls_tags_version_or_pull_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path not in {"/api/tags", "/api/version", "/api/pull"}
        return httpx.Response(200, json=_chat_response())

    wrapped, calls = counting_handler(handler)
    client = _client(wrapped, base_url="http://127.0.0.1:11434")
    asyncio.run(run_smoke_test(_config(), client=client))

    assert all(call.url.path == "/api/chat" for call in calls)


def test_run_smoke_test_fails_on_content_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(content="something else entirely"))

    client = _client(handler, base_url="http://127.0.0.1:11434")
    outcome = asyncio.run(run_smoke_test(_config(), client=client))

    assert outcome.passed is False
    assert "mismatch" in outcome.message.lower()


# --- _content_matches_expected: exact, unnormalized comparison --------------
#
# These test the comparison directly, bypassing OllamaProvider.generate()
# entirely: OllamaChatMessage (schemas.py) sets str_strip_whitespace=True,
# so a wire-level trailing newline or leading space on `content` never
# survives response parsing to reach this function through the full HTTP
# path. Testing at this level proves the comparison itself is exact and
# unnormalized, independent of that upstream (out-of-scope) behavior.


def test_content_matches_expected_on_exact_string() -> None:
    assert _content_matches_expected("PERSONAL_LMS_PROVIDER_OK") is True


def test_content_matches_expected_fails_on_trailing_newline() -> None:
    assert _content_matches_expected("PERSONAL_LMS_PROVIDER_OK\n") is False


def test_content_matches_expected_fails_on_leading_whitespace() -> None:
    assert _content_matches_expected(" PERSONAL_LMS_PROVIDER_OK") is False


def test_content_matches_expected_fails_on_other_mismatch() -> None:
    assert _content_matches_expected("something else entirely") is False


def test_run_smoke_test_passes_on_exact_string_with_no_surrounding_whitespace() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(content=_EXPECTED_CONTENT))

    client = _client(handler, base_url="http://127.0.0.1:11434")
    outcome = asyncio.run(run_smoke_test(_config(), client=client))

    assert outcome.passed is True


def test_run_smoke_test_raises_timeout_error_on_read_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = _client(handler, base_url="http://127.0.0.1:11434")

    with pytest.raises(ProviderTimeoutError):
        asyncio.run(run_smoke_test(_config(), client=client))


def test_run_smoke_test_raises_unavailable_error_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = _client(handler, base_url="http://127.0.0.1:11434")

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(run_smoke_test(_config(), client=client))


def test_run_smoke_test_raises_contract_error_on_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = _client(handler, base_url="http://127.0.0.1:11434")

    with pytest.raises(ProviderContractError):
        asyncio.run(run_smoke_test(_config(), client=client))


def test_run_smoke_test_raises_execution_error_on_http_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _client(handler, base_url="http://127.0.0.1:11434")

    with pytest.raises(ProviderExecutionError):
        asyncio.run(run_smoke_test(_config(), client=client))


# --- main() exit codes -------------------------------------------------------


def test_main_returns_nonzero_without_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    exit_code = main([])

    assert exit_code != 0


def test_main_returns_nonzero_for_non_loopback_host_without_flag() -> None:
    exit_code = main(["--model", "qwen2.5:7b", "--base-url", "http://172.25.16.1:11434"])

    assert exit_code != 0
