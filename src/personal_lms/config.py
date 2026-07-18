"""Application configuration for the local Personal LMS runtime.

Loads typed configuration from environment variables. Building an
``AppConfig`` never performs a network request, a filesystem write, or a
model call — it only parses strings and validates them through
``OllamaProviderConfig``. See ``personal_lms.composition`` for the
composition root that turns this configuration into live, connected
objects.

Stdlib plus Pydantic only — no dependency on ``httpx``, so this module (and
``AppConfig.from_env()``) works identically whether or not the optional
``ollama`` extra is installed, matching ``OllamaProviderConfig`` itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import ValidationError

from personal_lms.domain.base import StrictModel
from personal_lms.providers.ollama import OllamaProviderConfig

ENV_OLLAMA_BASE_URL = "OLLAMA_BASE_URL"
ENV_OLLAMA_MODEL = "OLLAMA_MODEL"
ENV_OLLAMA_TIMEOUT_SECONDS = "OLLAMA_TIMEOUT_SECONDS"
ENV_OLLAMA_MAX_CONTEXT_TOKENS = "OLLAMA_MAX_CONTEXT_TOKENS"
ENV_OLLAMA_ALLOW_NON_LOOPBACK = "OLLAMA_ALLOW_NON_LOOPBACK"

# Loopback-only, matching OllamaProviderConfig.base_url's own default — a
# non-loopback host always requires the explicit OLLAMA_ALLOW_NON_LOOPBACK
# opt-in, never a default.
_DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
_DEFAULT_OLLAMA_TIMEOUT_SECONDS = 30.0
# Not tied to any specific model's real context window — a conservative,
# overridable placeholder for whichever model OLLAMA_MODEL names.
_DEFAULT_OLLAMA_MAX_CONTEXT_TOKENS = 8192

# Fixed, not caller-configurable: this identifies the single Ollama
# provider instance the composition root registers, not a value that
# varies per deployment.
OLLAMA_PROVIDER_ID = "ollama-local"

_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


class AppConfigError(ValueError):
    """Raised when application configuration cannot be loaded from the environment.

    Message carries only environment-variable names and the invalid value
    supplied for them — never a credential, since none of this
    configuration surface accepts one.
    """


def _parse_bool_env(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY_ENV_VALUES


def _require_str(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if not value:
        raise AppConfigError(f"{key} is required but not set")
    return value


def _optional_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise AppConfigError(f"{key} must be a number, got {raw!r}") from exc


def _optional_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise AppConfigError(f"{key} must be an integer, got {raw!r}") from exc


class AppConfig(StrictModel):
    """Typed application configuration for the local runtime.

    Wraps only Ollama configuration — the sole real provider wired up in
    this milestone. Construct with ``AppConfig.from_env()``; nothing else
    in this codebase should read ``os.environ`` directly for these values.
    """

    ollama: OllamaProviderConfig

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AppConfig:
        """Load configuration from environment variables.

        ``env`` defaults to ``os.environ`` and exists as an explicit
        parameter purely for deterministic testing. Raises
        ``AppConfigError`` on any missing-required or malformed value —
        never contacts the network.
        """
        source = env if env is not None else os.environ

        model = _require_str(source, ENV_OLLAMA_MODEL)
        base_url = source.get(ENV_OLLAMA_BASE_URL) or _DEFAULT_OLLAMA_BASE_URL
        timeout_seconds = _optional_float(
            source, ENV_OLLAMA_TIMEOUT_SECONDS, _DEFAULT_OLLAMA_TIMEOUT_SECONDS
        )
        max_context_tokens = _optional_int(
            source, ENV_OLLAMA_MAX_CONTEXT_TOKENS, _DEFAULT_OLLAMA_MAX_CONTEXT_TOKENS
        )
        allow_non_loopback = _parse_bool_env(source.get(ENV_OLLAMA_ALLOW_NON_LOOPBACK))

        try:
            ollama_config = OllamaProviderConfig(
                provider_id=OLLAMA_PROVIDER_ID,
                base_url=base_url,
                model=model,
                timeout_seconds=timeout_seconds,
                max_context_tokens=max_context_tokens,
                allow_non_loopback=allow_non_loopback,
            )
        except ValidationError as exc:
            raise AppConfigError(str(exc)) from exc

        return cls(ollama=ollama_config)
