from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import ValidationError

from personal_lms import __version__
from personal_lms.config import AppConfig, AppConfigError
from personal_lms.domain.models import ModelRequest
from personal_lms.policies.errors import RoutingError
from personal_lms.providers.errors import ProviderError

if TYPE_CHECKING:
    # Only for the type hint below — a real (non-TYPE_CHECKING) import
    # would make importing this module unconditionally require the
    # optional `ollama` extra, breaking `personal-lms --version` in
    # core-only installs. See _ask_command's local import of `compose`.
    from personal_lms.composition import Application


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="personal-lms")
    parser.add_argument(
        "--version",
        action="version",
        version=f"personal-lms {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    ask_parser = subparsers.add_parser(
        "ask",
        help="Send one prompt through the local Personal Assistant Flow.",
    )
    ask_parser.add_argument(
        "--prompt",
        required=True,
        help="Prompt text to send. Always routed to a local provider only.",
    )

    demo_parser = subparsers.add_parser(
        "build-week-demo", help="Run the loopback-only Grounded Tutor demo."
    )
    demo_parser.add_argument("--host", default="127.0.0.1")
    demo_parser.add_argument("--port", type=int, default=8000)

    return parser


async def _run_ask(app: Application, prompt: str) -> tuple[int, str]:
    """Run exactly one local-only ask through ``app.flow`` and always clean up.

    Returns ``(exit_code, message)``: on success ``message`` is the model's
    output text; on failure it is a human-readable error description. A
    single ``try``/``finally`` around Flow execution guarantees ``app`` is
    closed exactly once, whether or not the run succeeds.
    """
    try:
        try:
            request = ModelRequest(capability_profile=app.config.ollama.provider_id, prompt=prompt)
        except ValidationError as exc:
            return 2, f"invalid prompt: {exc}"

        try:
            # local_only=True is the hard requirement for this command: no
            # hosted candidate is ever selectable, independent of whatever
            # budget_policy.local_only happens to be set to.
            result = await app.flow.run(request, budget_policy=app.budget_policy, local_only=True)
        except RoutingError as exc:
            return 1, f"routing error: {exc}"
        except ProviderError as exc:
            return 1, f"provider error: {exc}"

        if result.model_result is None:
            return 1, f"no model output produced (routing outcome: {result.decision.outcome.value})"

        return 0, result.model_result.output_text
    finally:
        await app.aclose()


def _ask_command(prompt: str) -> int:
    try:
        config = AppConfig.from_env()
    except AppConfigError as exc:
        print(f"FAIL configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        from personal_lms.composition import compose
    except ImportError as exc:  # OllamaExtraNotInstalledError, e.g.
        print(f"FAIL {exc}", file=sys.stderr)
        return 2

    app = compose(config)
    exit_code, message = asyncio.run(_run_ask(app, prompt))

    if exit_code == 0:
        print(message)
    else:
        print(f"FAIL {message}", file=sys.stderr)
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ask":
        return _ask_command(args.prompt)
    if args.command == "build-week-demo":
        from personal_lms.build_week_demo import serve

        serve(args.host, args.port)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
