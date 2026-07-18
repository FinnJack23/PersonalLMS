from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from personal_lms.providers.ollama.errors import OllamaExtraNotInstalledError

# This is the one place in this codebase that imports httpx. No offline/
# telemetry bootstrap is needed here (unlike the CrewAI adapter) — httpx
# performs no import-time network calls or telemetry of its own.
try:
    import httpx
except ModuleNotFoundError as exc:
    if exc.name is not None and (exc.name == "httpx" or exc.name.startswith("httpx.")):
        raise OllamaExtraNotInstalledError() from exc
    raise

from personal_lms.domain.models import (  # noqa: E402
    ModelCapabilityProfile,
    ModelRequest,
    ModelResult,
)
from personal_lms.providers.errors import (  # noqa: E402
    ProviderContractError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from personal_lms.providers.ollama.config import OllamaProviderConfig  # noqa: E402
from personal_lms.providers.ollama.schemas import (  # noqa: E402
    OllamaChatResponse,
    OllamaTagsResponse,
    OllamaVersionResponse,
)

_NANOSECONDS_PER_MILLISECOND = 1_000_000


@dataclass(frozen=True, slots=True)
class OllamaHealthResult:
    """Typed result of a health/version check."""

    version: str


@dataclass(frozen=True, slots=True)
class OllamaModelSummary:
    """Typed, immutable discovery result for one installed model."""

    name: str
    digest: str | None
    size_bytes: int | None
    parameter_size: str | None
    quantization_level: str | None
    family: str | None


@runtime_checkable
class OllamaChatClient(Protocol):
    """Package-private boundary for the single Ollama operation
    ``OllamaProvider.generate()`` needs.

    Never exposed through the public ``ModelProvider`` API — this exists
    only so tests can inject a pure-Python fake implementing exactly this
    one async method, in place of an ``httpx.AsyncClient``/
    ``httpx.MockTransport`` pair, for fully deterministic ``generate()``
    coverage. ``provider.py`` itself still requires ``httpx`` to import at
    all (see the module-level ``OllamaExtraNotInstalledError`` handling
    above) — that existing, deliberate optional-dependency boundary is
    unchanged; this Protocol only narrows what ``generate()`` itself
    depends on, so the production ``_HttpxChatClient`` and any test fake
    share one call shape.
    """

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        stream: bool,
        options: dict[str, Any],
        keep_alive: str | None,
    ) -> dict[str, Any]: ...


class _HttpxChatClient:
    """Default ``OllamaChatClient``, backed by the provider's own ``httpx.AsyncClient``.

    Byte-for-byte the same ``POST /api/chat`` request shape and error
    mapping ``OllamaProvider.generate()`` always sent — moved here
    unchanged so it can be swapped out via constructor injection.
    """

    def __init__(
        self, client: httpx.AsyncClient, *, provider_id: str, timeout_seconds: float
    ) -> None:
        self._client = client
        self._provider_id = provider_id
        self._timeout_seconds = timeout_seconds

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        stream: bool,
        options: dict[str, Any],
        keep_alive: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "think": False,
            "options": options,
        }
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive

        try:
            response = await self._client.request("POST", "/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(self._provider_id, self._timeout_seconds) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(self._provider_id, "connection failed") from exc

        if response.status_code >= 400:
            raise ProviderExecutionError(
                self._provider_id, f"generate received HTTP {response.status_code}"
            )
        result: dict[str, Any] = response.json()
        return result


class OllamaProvider:
    """Local Ollama inference provider. Structurally conforms to ``ModelProvider``.

    Uses only the three documented native endpoints — ``GET /api/version``,
    ``GET /api/tags``, ``POST /api/chat`` (``stream=false``) — never model
    pull/push/create/copy/delete, never the ``ollama`` CLI, never the
    OpenAI-compatible endpoints, never tool calling or embeddings. Never
    retries and never falls back to another provider; a caller (the
    router, a Flow) owns those decisions.
    """

    def __init__(
        self,
        config: OllamaProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        chat_client: OllamaChatClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url, timeout=config.timeout_seconds
        )
        self._chat_client = chat_client or _HttpxChatClient(
            self._client, provider_id=config.provider_id, timeout_seconds=config.timeout_seconds
        )
        self._profile = ModelCapabilityProfile(
            profile_id=config.provider_id,
            supports_reasoning=config.supports_reasoning,
            supports_vision=config.supports_vision,
            max_context_tokens=config.max_context_tokens,
            is_local=True,
            max_privacy_classification=config.max_privacy_classification,
            latency_class=config.latency_class,
            cost_class=config.cost_class,
        )

    @property
    def provider_id(self) -> str:
        return self._config.provider_id

    @property
    def capability_profiles(self) -> tuple[ModelCapabilityProfile, ...]:
        return (self._profile,)

    @property
    def is_local(self) -> bool:
        return True

    @property
    def cost_per_call_usd(self) -> Decimal:
        """Always zero: local inference incurs no hosted-API charge.

        Unlike the fake providers (Commit 3), this is a fixed property, not
        a constructor parameter — for a real local provider, zero cost is
        an architectural fact, not a test convenience. It says nothing
        about the storage, compute, or electricity cost of running Ollama
        itself; see the local-provider documentation.
        """
        return Decimal("0")

    async def close(self) -> None:
        """Close the owned HTTP client. No-op if a client was injected."""
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> OllamaHealthResult:
        response = await self._request("GET", "/api/version", operation="health")
        try:
            parsed = OllamaVersionResponse.model_validate(response.json())
        except ValueError as exc:
            raise ProviderContractError(self.provider_id, "malformed version response") from exc
        return OllamaHealthResult(version=parsed.version)

    async def list_models(self) -> tuple[OllamaModelSummary, ...]:
        response = await self._request("GET", "/api/tags", operation="list_models")
        try:
            parsed = OllamaTagsResponse.model_validate(response.json())
        except ValueError as exc:
            raise ProviderContractError(self.provider_id, "malformed tags response") from exc

        summaries = tuple(
            OllamaModelSummary(
                name=model.name,
                digest=model.digest,
                size_bytes=model.size,
                parameter_size=model.details.parameter_size if model.details else None,
                quantization_level=model.details.quantization_level if model.details else None,
                family=model.details.family if model.details else None,
            )
            for model in parsed.models
        )
        return tuple(sorted(summaries, key=lambda summary: summary.name))

    async def is_model_installed(self, model: str | None = None) -> bool:
        """Whether ``model`` (default: the configured model) is installed.

        Never pulls a missing model — this is a read-only check.
        """
        target = model if model is not None else self._config.model
        installed = await self.list_models()
        return any(summary.name == target for summary in installed)

    async def generate(self, request: ModelRequest) -> ModelResult:
        options: dict[str, Any] = {"temperature": self._config.temperature}
        if self._config.seed is not None:
            options["seed"] = self._config.seed

        raw = await self._chat_client.chat(
            model=self._config.model,
            messages=[{"role": "user", "content": request.prompt}],
            stream=False,
            options=options,
            keep_alive=self._config.keep_alive,
        )

        try:
            parsed = OllamaChatResponse.model_validate(raw)
        except ValueError as exc:
            raise ProviderContractError(self.provider_id, "malformed chat response") from exc

        if not parsed.done:
            raise ProviderContractError(self.provider_id, "response did not complete (done=false)")

        latency_ms = (
            parsed.total_duration / _NANOSECONDS_PER_MILLISECOND
            if parsed.total_duration is not None
            else 0.0
        )

        return ModelResult(
            request_id=request.request_id,
            capability_profile=self._profile.profile_id,
            is_local=True,
            output_text=parsed.message.content,
            input_tokens=parsed.prompt_eval_count or 0,
            output_tokens=parsed.eval_count or 0,
            latency_ms=latency_ms,
            finish_reason=parsed.done_reason or "stop",
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(self.provider_id, self._config.timeout_seconds) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(self.provider_id, "connection failed") from exc

        if response.status_code >= 400:
            raise ProviderExecutionError(
                self.provider_id,
                f"{operation} received HTTP {response.status_code}",
            )
        return response
