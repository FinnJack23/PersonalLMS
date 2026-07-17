"""Bounded live smoke test for :class:`OllamaProvider`.

A manual verification tool, not part of the routing or Flow runtime: it
sends exactly one deterministic ``POST /api/chat`` request to a configured
Ollama server and checks the reply against a fixed expected string. It never
calls ``GET /api/tags``, ``GET /api/version``, a hosted provider, or any
model-pull endpoint.

Base URL and model are supplied by the caller (CLI flag or environment
variable) — this module hard-codes neither. Temperature and seed are fixed
at ``0`` so the request is deterministic regardless of caller input.

The reply is compared to the expected string with no normalization
(``_content_matches_expected``). In practice, ``OllamaChatMessage``
(``schemas.py``) currently sets ``str_strip_whitespace=True``, so
``provider.generate()`` already strips leading/trailing whitespace off the
raw response before this module ever sees it — wire-level whitespace
mismatches are therefore not observable end-to-end today. That schema
setting is shared provider-contract code outside this module's scope;
whether it should stay is a separate decision.

Usage::

    uv run personal-lms-ollama-smoke-test --base-url http://127.0.0.1:11434 --model qwen2.5:7b

Or via environment variables::

    OLLAMA_BASE_URL=http://127.0.0.1:11434 OLLAMA_MODEL=qwen2.5:7b \\
        uv run personal-lms-ollama-smoke-test
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from personal_lms.domain.models import ModelRequest
from personal_lms.providers.errors import (
    ProviderContractError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from personal_lms.providers.ollama import OllamaExtraNotInstalledError, OllamaProviderConfig

if TYPE_CHECKING:
    import httpx

# Not a real model's context window — max_context_tokens only feeds this
# provider's capability-profile metadata and has no effect on the single
# /api/chat request this smoke test sends.
_PLACEHOLDER_MAX_CONTEXT_TOKENS = 4096
_DEFAULT_TIMEOUT_SECONDS = 30.0
_PROVIDER_ID = "ollama-smoke-test"
_EXPECTED_CONTENT = "PERSONAL_LMS_PROVIDER_OK"
_PROMPT = f"Reply with exactly: {_EXPECTED_CONTENT}"


@dataclass(frozen=True, slots=True)
class SmokeTestOutcome:
    """Result of one smoke-test run."""

    passed: bool
    message: str


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="personal-lms-ollama-smoke-test",
        description=(
            "Send one bounded POST /api/chat request to a local Ollama server "
            "and confirm it returns the exact expected reply."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OLLAMA_BASE_URL") or None,
        help="Ollama base URL (default: $OLLAMA_BASE_URL, then http://127.0.0.1:11434).",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL") or None,
        help="Model tag to request (default: $OLLAMA_MODEL). Required.",
    )
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help=(
            "Allow a non-loopback --base-url host. Explicit, conscious opt-in — "
            "required whenever the Ollama server is not on this machine."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help=f"Request timeout in seconds (default: {_DEFAULT_TIMEOUT_SECONDS}).",
    )
    return parser


def build_config(args: argparse.Namespace) -> OllamaProviderConfig:
    """Build the provider config from parsed CLI args. Raises ``ValidationError``
    or ``ValueError`` on invalid input — never contacts the network."""
    if not args.model:
        raise ValueError("--model is required (or set OLLAMA_MODEL)")

    config_kwargs: dict[str, object] = {
        "provider_id": _PROVIDER_ID,
        "model": args.model,
        "max_context_tokens": _PLACEHOLDER_MAX_CONTEXT_TOKENS,
        "timeout_seconds": args.timeout_seconds,
        # Fixed, not caller-configurable: this smoke test is deterministic by design.
        "temperature": 0.0,
        "seed": 0,
        "allow_non_loopback": args.allow_non_loopback,
    }
    if args.base_url is not None:
        config_kwargs["base_url"] = args.base_url

    return OllamaProviderConfig.model_validate(config_kwargs)


def _content_matches_expected(content: str) -> bool:
    """Exact, unnormalized comparison against ``_EXPECTED_CONTENT``.

    No stripping or case-folding: any surrounding whitespace or other
    deviation is a mismatch. Note that ``OllamaChatMessage`` (schemas.py)
    currently applies ``str_strip_whitespace=True`` during response
    parsing, so raw wire-level leading/trailing whitespace on ``content``
    is not observable by the time it reaches this function via
    ``run_smoke_test`` — see the module docstring.
    """
    return content == _EXPECTED_CONTENT


async def run_smoke_test(
    config: OllamaProviderConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> SmokeTestOutcome:
    """Run the single-request smoke test against ``config``.

    ``client`` is test-only dependency injection (a mock transport); real
    callers always leave it ``None`` so ``OllamaProvider`` builds its own.
    Imports ``OllamaProvider`` lazily so this module stays importable (and
    ``build_config``/argument parsing stay testable) without the optional
    ``ollama`` extra installed.
    """
    from personal_lms.providers.ollama import OllamaProvider

    provider = OllamaProvider(config, client=client)
    try:
        request = ModelRequest(capability_profile=config.provider_id, prompt=_PROMPT)
        result = await provider.generate(request)
    finally:
        await provider.close()

    if not _content_matches_expected(result.output_text):
        return SmokeTestOutcome(
            passed=False,
            message=(
                "FAIL content mismatch\n"
                f"  expected: {_EXPECTED_CONTENT!r}\n"
                f"  received: {result.output_text!r}"
            ),
        )

    return SmokeTestOutcome(
        passed=True,
        message=(
            "PASS\n"
            "  provider: OllamaProvider\n"
            f"  model:    {config.model}\n"
            f"  base_url: {config.base_url}\n"
            f"  content:  {result.output_text!r}"
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        config = build_config(args)
    except (ValueError, ValidationError) as exc:
        print(f"FAIL invalid configuration: {exc}", file=sys.stderr)
        return 2

    try:
        outcome = asyncio.run(run_smoke_test(config))
    except OllamaExtraNotInstalledError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    except ProviderTimeoutError as exc:
        print(f"FAIL timeout: {exc}", file=sys.stderr)
        return 1
    except ProviderUnavailableError as exc:
        print(f"FAIL connection failed: {exc}", file=sys.stderr)
        return 1
    except ProviderContractError as exc:
        print(f"FAIL malformed response: {exc}", file=sys.stderr)
        return 1
    except ProviderExecutionError as exc:
        print(f"FAIL request failed: {exc}", file=sys.stderr)
        return 1

    print(outcome.message)
    return 0 if outcome.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
