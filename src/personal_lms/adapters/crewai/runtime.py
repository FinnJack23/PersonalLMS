"""Stdlib-only CrewAI bootstrap: offline defaults and the optional-dependency error.

This module has no dependency on the external ``crewai`` package, so it
works identically whether or not the optional ``crewai`` extra is
installed. Any module in this codebase that imports ``crewai`` must call
``apply_offline_defaults()`` first, before its own ``import crewai`` line —
see ``personal_lms.adapters.crewai.personal_assistant`` for the only such
call site today.
"""

from __future__ import annotations

import os


class CrewAIExtraNotInstalledError(ImportError):
    """Raised when CrewAI orchestration is requested without the optional extra.

    Message is a fixed string with no dynamic content — never a prompt,
    credential, path, or environment value.
    """

    def __init__(self) -> None:
        super().__init__(
            "CrewAI orchestration is not installed. Install the optional "
            "extra with `uv sync --extra crewai` "
            '(or `pip install "personal-lms[crewai]"`), then retry.'
        )


def apply_offline_defaults() -> None:
    """Set CrewAI's offline/privacy environment defaults.

    Non-destructive (``setdefault``): an operator's real environment or a
    loaded ``.env`` file always wins. Beyond ``OTEL_SDK_DISABLED``, CrewAI
    also performs a PyPI network call to check for a newer version and
    persists a first-run tracing-consent file, independently of the OTel
    setting — the other three defaults close those paths.
    """
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    os.environ.setdefault("CREWAI_DISABLE_VERSION_CHECK", "true")
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    os.environ.setdefault("CREWAI_DISABLE_TRACKING", "true")
