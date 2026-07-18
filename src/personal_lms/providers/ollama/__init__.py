"""Ollama local-inference provider — optional.

``OllamaProviderConfig`` has no dependency on ``httpx`` and is always
importable. ``OllamaProvider`` (and its small result types) are loaded
lazily on first access, so ``import personal_lms.providers.ollama`` alone
never imports ``httpx`` — only actually using the provider does, and by
then ``provider.py`` has already translated a missing install into
``OllamaExtraNotInstalledError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from personal_lms.providers.ollama.config import OllamaProviderConfig
from personal_lms.providers.ollama.errors import OllamaExtraNotInstalledError

if TYPE_CHECKING:
    from personal_lms.providers.ollama.provider import (
        OllamaHealthResult,
        OllamaModelSummary,
        OllamaProvider,
    )

__all__ = [
    "OllamaExtraNotInstalledError",
    "OllamaHealthResult",
    "OllamaModelSummary",
    "OllamaProvider",
    "OllamaProviderConfig",
]

_LAZY_ATTRS = frozenset({"OllamaHealthResult", "OllamaModelSummary", "OllamaProvider"})


def __getattr__(name: str) -> Any:
    if name in _LAZY_ATTRS:
        from personal_lms.providers.ollama import provider

        return getattr(provider, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
