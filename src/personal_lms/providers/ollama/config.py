"""Ollama provider configuration.

Pure Pydantic/stdlib — no dependency on ``httpx``, so this module (and
therefore construction of ``OllamaProviderConfig``) works identically
whether or not the optional ``ollama`` extra is installed.
"""

from __future__ import annotations

from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from personal_lms.domain.base import StrictModel
from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.privacy import PrivacyClassification

_LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _validate_base_url(url: str, *, allow_non_loopback: bool) -> None:
    """Raise ``ValueError`` on any URL shape this provider must reject.

    Never includes ``url`` itself in the raised message — a base URL can
    carry host or path information a caller may not want surfaced in a
    traceback or log.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError("base_url must use the http or https scheme")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not embed credentials")
    if parsed.query:
        raise ValueError("base_url must not include a query string")
    if parsed.fragment:
        raise ValueError("base_url must not include a fragment")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("base_url must include a host")
    if not allow_non_loopback and hostname.lower() not in _LOOPBACK_HOSTNAMES:
        raise ValueError("base_url host must be loopback unless allow_non_loopback is enabled")


class OllamaProviderConfig(StrictModel):
    """Strict configuration for one ``OllamaProvider`` instance.

    Constructed explicitly by the caller — this module never reads
    environment variables. Configuration *loading* (from ``.env`` or
    elsewhere) is a separate concern left to a later commit.
    """

    provider_id: str = Field(min_length=1)
    base_url: str = Field(default="http://127.0.0.1:11434", min_length=1)
    model: str = Field(min_length=1)
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_context_tokens: int = Field(gt=0)
    supports_reasoning: bool = True
    supports_vision: bool = False
    max_privacy_classification: PrivacyClassification = PrivacyClassification.RESTRICTED_LOCAL_ONLY
    latency_class: LatencyClass = LatencyClass.STANDARD
    cost_class: CostClass = CostClass.FREE
    keep_alive: str | None = Field(default=None, min_length=1)
    temperature: float = Field(default=0.0, ge=0)
    seed: int | None = 0
    allow_non_loopback: bool = False

    @model_validator(mode="after")
    def _check_base_url(self) -> Self:
        _validate_base_url(self.base_url, allow_non_loopback=self.allow_non_loopback)
        return self
