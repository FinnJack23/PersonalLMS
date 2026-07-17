from __future__ import annotations

import pytest
from pydantic import ValidationError

from personal_lms.providers.ollama import OllamaProviderConfig

from .conftest import make_config

# No httpx import anywhere in this file — OllamaProviderConfig has no
# transport dependency, so these tests run in every installation mode,
# including core-only.


def test_default_config_uses_loopback_http() -> None:
    config = make_config()
    assert config.base_url == "http://127.0.0.1:11434"
    assert config.allow_non_loopback is False


def test_https_loopback_is_accepted() -> None:
    config = make_config(base_url="https://127.0.0.1:11434")
    assert config.base_url == "https://127.0.0.1:11434"


def test_localhost_hostname_is_accepted() -> None:
    config = make_config(base_url="http://localhost:11434")
    assert config.base_url == "http://localhost:11434"


def test_embedded_credentials_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(base_url="http://user:pass@127.0.0.1:11434")


def test_query_string_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(base_url="http://127.0.0.1:11434?debug=true")


def test_fragment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(base_url="http://127.0.0.1:11434#section")


def test_non_http_scheme_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(base_url="ftp://127.0.0.1:11434")


def test_non_loopback_host_is_rejected_by_default() -> None:
    with pytest.raises(ValidationError):
        make_config(base_url="http://192.168.1.50:11434")


def test_non_loopback_host_is_accepted_when_explicitly_allowed() -> None:
    config = make_config(base_url="http://192.168.1.50:11434", allow_non_loopback=True)
    assert config.base_url == "http://192.168.1.50:11434"


def test_empty_model_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(model="")


def test_whitespace_only_model_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(model="   ")


def test_empty_provider_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(provider_id="")


def test_non_positive_timeout_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(timeout_seconds=0)
    with pytest.raises(ValidationError):
        make_config(timeout_seconds=-1.0)


def test_non_positive_max_context_tokens_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(max_context_tokens=0)
    with pytest.raises(ValidationError):
        make_config(max_context_tokens=-100)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(vendor="openai")  # type: ignore[call-arg]


def test_config_json_round_trip() -> None:
    config = make_config(keep_alive="5m", seed=42, temperature=0.2)
    restored = OllamaProviderConfig.model_validate_json(config.model_dump_json())
    assert restored == config


def test_config_url_error_message_does_not_leak_the_url() -> None:
    secret_looking_host = "http://internal-secret-host.example:11434"
    with pytest.raises(ValidationError) as exc_info:
        make_config(base_url=secret_looking_host)
    assert secret_looking_host not in str(exc_info.value)
    assert "internal-secret-host" not in str(exc_info.value)
