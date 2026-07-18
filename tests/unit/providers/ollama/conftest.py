"""Shared, httpx-independent test helpers for the Ollama provider suite.

Deliberately has no ``httpx`` import: ``OllamaProviderConfig`` needs none,
so ``test_config.py`` can run in every installation mode, including
core-only. httpx-dependent helpers (mock transports, provider construction)
live in ``_helpers.py`` instead, imported only by test files that have
already confirmed httpx is available.
"""

from __future__ import annotations

from personal_lms.providers.ollama import OllamaProviderConfig


def make_config(**overrides: object) -> OllamaProviderConfig:
    defaults: dict[str, object] = {
        "provider_id": "ollama-local",
        "model": "qwen2.5:7b",
        "max_context_tokens": 8192,
    }
    defaults.update(overrides)
    return OllamaProviderConfig.model_validate(defaults)
