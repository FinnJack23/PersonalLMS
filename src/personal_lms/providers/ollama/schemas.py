"""Typed, immutable parsers for Ollama's native JSON API responses.

Pure Pydantic — no ``httpx`` dependency. Uses ``extra="ignore"`` rather than
the domain layer's ``StrictModel`` (``extra="forbid"``): this parses an
external API's JSON, and Ollama may add response fields over time without
that being a contract violation for us.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _OllamaResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True)


class OllamaVersionResponse(_OllamaResponseModel):
    """``GET /api/version`` — ``{"version": "0.1.2"}``."""

    version: str = Field(min_length=1)


class OllamaModelDetails(_OllamaResponseModel):
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None


class OllamaModelInfo(_OllamaResponseModel):
    name: str = Field(min_length=1)
    digest: str | None = None
    size: int | None = Field(default=None, ge=0)
    details: OllamaModelDetails | None = None


class OllamaTagsResponse(_OllamaResponseModel):
    """``GET /api/tags`` — ``{"models": [...]}``."""

    models: list[OllamaModelInfo] = Field(default_factory=list)


class OllamaChatMessage(_OllamaResponseModel):
    role: str = Field(min_length=1)
    content: str


class OllamaChatResponse(_OllamaResponseModel):
    """``POST /api/chat`` with ``stream=false``.

    ``content`` may legitimately be empty — ``ModelResult.output_text`` has
    no minimum length, so an empty assistant reply is not a contract
    violation. Duration/token-count fields are optional: required only to
    be nonnegative when present, never required to be present.
    """

    message: OllamaChatMessage
    done: bool
    done_reason: str | None = None
    prompt_eval_count: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)
    total_duration: int | None = Field(default=None, ge=0)
