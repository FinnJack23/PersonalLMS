"""httpx-dependent test helpers.

Only imported by test files that have already checked ``httpx`` is
importable (see each test file's module-level skip guard) — importing this
module directly in core-only mode would raise ``ModuleNotFoundError``.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from personal_lms.providers.ollama import OllamaProvider, OllamaProviderConfig

Handler = Callable[[httpx.Request], httpx.Response]


def build_client(handler: Handler, *, base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


def build_provider(config: OllamaProviderConfig, handler: Handler) -> OllamaProvider:
    return OllamaProvider(config, client=build_client(handler, base_url=config.base_url))


def counting_handler(handler: Handler) -> tuple[Handler, list[httpx.Request]]:
    """Wrap ``handler`` to record every request it receives, in order."""
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return wrapped, calls
