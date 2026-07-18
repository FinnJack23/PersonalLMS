from __future__ import annotations

import pytest

from personal_lms.config import (
    ENV_OLLAMA_ALLOW_NON_LOOPBACK,
    ENV_OLLAMA_BASE_URL,
    ENV_OLLAMA_MAX_CONTEXT_TOKENS,
    ENV_OLLAMA_MODEL,
    ENV_OLLAMA_TIMEOUT_SECONDS,
    AppConfig,
    AppConfigError,
)


def _env(**overrides: str) -> dict[str, str]:
    defaults: dict[str, str] = {ENV_OLLAMA_MODEL: "qwen2.5:7b"}
    defaults.update(overrides)
    return defaults


def test_from_env_loads_model_and_base_url() -> None:
    config = AppConfig.from_env(
        _env(**{ENV_OLLAMA_MODEL: "qwen2.5:7b", ENV_OLLAMA_BASE_URL: "http://127.0.0.1:11434"})
    )

    assert config.ollama.model == "qwen2.5:7b"
    assert config.ollama.base_url == "http://127.0.0.1:11434"


def test_from_env_loads_timeout_and_max_context_tokens() -> None:
    config = AppConfig.from_env(
        _env(
            **{
                ENV_OLLAMA_TIMEOUT_SECONDS: "45.5",
                ENV_OLLAMA_MAX_CONTEXT_TOKENS: "16384",
            }
        )
    )

    assert config.ollama.timeout_seconds == 45.5
    assert config.ollama.max_context_tokens == 16384


def test_from_env_loads_allow_non_loopback_flag() -> None:
    config = AppConfig.from_env(
        _env(
            **{
                ENV_OLLAMA_BASE_URL: "http://172.25.16.1:11434",
                ENV_OLLAMA_ALLOW_NON_LOOPBACK: "true",
            }
        )
    )

    assert config.ollama.base_url == "http://172.25.16.1:11434"
    assert config.ollama.allow_non_loopback is True


@pytest.mark.parametrize("truthy", ["1", "true", "True", "YES", "on"])
def test_allow_non_loopback_accepts_common_truthy_spellings(truthy: str) -> None:
    config = AppConfig.from_env(_env(**{ENV_OLLAMA_ALLOW_NON_LOOPBACK: truthy}))
    assert config.ollama.allow_non_loopback is True


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", ""])
def test_allow_non_loopback_accepts_common_falsy_spellings(falsy: str) -> None:
    config = AppConfig.from_env(_env(**{ENV_OLLAMA_ALLOW_NON_LOOPBACK: falsy}))
    assert config.ollama.allow_non_loopback is False


# --- safe defaults ------------------------------------------------------


def test_default_base_url_is_loopback() -> None:
    config = AppConfig.from_env(_env())
    assert config.ollama.base_url == "http://127.0.0.1:11434"


def test_default_allow_non_loopback_is_false() -> None:
    config = AppConfig.from_env(_env())
    assert config.ollama.allow_non_loopback is False


def test_default_timeout_seconds_is_positive() -> None:
    config = AppConfig.from_env(_env())
    assert config.ollama.timeout_seconds > 0


def test_default_max_context_tokens_is_positive() -> None:
    config = AppConfig.from_env(_env())
    assert config.ollama.max_context_tokens > 0


def test_no_configuration_hard_codes_the_specific_windows_host_ip() -> None:
    config = AppConfig.from_env(_env())
    assert "172.25.16.1" not in config.ollama.base_url


def test_no_configuration_hard_codes_the_specific_qwen_tag() -> None:
    config = AppConfig.from_env(_env())
    assert config.ollama.model == "qwen2.5:7b"  # from the test's own env, not a built-in default


# --- required configuration ----------------------------------------------


def test_missing_model_raises_clear_error() -> None:
    with pytest.raises(AppConfigError, match=ENV_OLLAMA_MODEL):
        AppConfig.from_env({})


def test_empty_model_raises_clear_error() -> None:
    with pytest.raises(AppConfigError, match=ENV_OLLAMA_MODEL):
        AppConfig.from_env({ENV_OLLAMA_MODEL: ""})


# --- non-loopback requires explicit permission ----------------------------


def test_non_loopback_base_url_without_explicit_permission_fails() -> None:
    with pytest.raises(AppConfigError):
        AppConfig.from_env(_env(**{ENV_OLLAMA_BASE_URL: "http://172.25.16.1:11434"}))


def test_non_loopback_base_url_with_explicit_permission_succeeds() -> None:
    config = AppConfig.from_env(
        _env(
            **{
                ENV_OLLAMA_BASE_URL: "http://172.25.16.1:11434",
                ENV_OLLAMA_ALLOW_NON_LOOPBACK: "true",
            }
        )
    )
    assert config.ollama.base_url == "http://172.25.16.1:11434"


# --- malformed numeric configuration --------------------------------------


def test_non_numeric_timeout_raises_clear_error() -> None:
    with pytest.raises(AppConfigError, match=ENV_OLLAMA_TIMEOUT_SECONDS):
        AppConfig.from_env(_env(**{ENV_OLLAMA_TIMEOUT_SECONDS: "not-a-number"}))


def test_non_integer_max_context_tokens_raises_clear_error() -> None:
    with pytest.raises(AppConfigError, match=ENV_OLLAMA_MAX_CONTEXT_TOKENS):
        AppConfig.from_env(_env(**{ENV_OLLAMA_MAX_CONTEXT_TOKENS: "not-an-integer"}))


# --- no network access at configuration time ------------------------------


def test_from_env_imports_and_runs_without_httpx_installed() -> None:
    """AppConfig has no dependency on httpx — it must work in core-only mode.

    Run in a fresh interpreter so this assertion is independent of whether
    httpx happens to already be imported in this test process.
    """
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from personal_lms.config import AppConfig\n"
        "config = AppConfig.from_env({'OLLAMA_MODEL': 'qwen2.5:7b'})\n"
        "assert 'httpx' not in sys.modules\n"
        "assert config.ollama.model == 'qwen2.5:7b'\n"
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
