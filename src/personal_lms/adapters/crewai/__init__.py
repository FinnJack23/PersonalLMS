"""CrewAI orchestration adapter — optional.

This package must remain importable without the external ``crewai``
package installed (see the ``crewai`` extra in ``pyproject.toml``).
``CrewAIPersonalAssistantFlow`` and ``PersonalAssistantFlowState`` are
loaded lazily on first access, so ``import personal_lms.adapters.crewai``
alone never imports ``crewai`` — only actually using the adapter does, and
by then ``personal_assistant.py`` has already applied the offline defaults
and translated a missing install into ``CrewAIExtraNotInstalledError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from personal_lms.adapters.crewai.runtime import CrewAIExtraNotInstalledError

if TYPE_CHECKING:
    from personal_lms.adapters.crewai.personal_assistant import (
        CrewAIPersonalAssistantFlow,
        PersonalAssistantFlowState,
    )

__all__ = [
    "CrewAIExtraNotInstalledError",
    "CrewAIPersonalAssistantFlow",
    "PersonalAssistantFlowState",
]

_LAZY_ATTRS = frozenset({"CrewAIPersonalAssistantFlow", "PersonalAssistantFlowState"})


def __getattr__(name: str) -> Any:
    if name in _LAZY_ATTRS:
        from personal_lms.adapters.crewai import personal_assistant

        return getattr(personal_assistant, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
