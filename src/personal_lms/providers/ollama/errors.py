"""The Ollama provider's optional-dependency error.

Stdlib-only — has no dependency on ``httpx``, so it is safe to import
regardless of whether the optional ``ollama`` extra is installed.
"""

from __future__ import annotations


class OllamaExtraNotInstalledError(ImportError):
    """Raised when the Ollama provider is requested without the optional extra.

    Message is a fixed string with no dynamic content — never a prompt,
    credential, URL, or environment value.
    """

    def __init__(self) -> None:
        super().__init__(
            "The Ollama provider is not installed. Install the optional "
            "extra with `uv sync --extra ollama` "
            '(or `pip install "personal-lms[ollama]"`), then retry.'
        )
