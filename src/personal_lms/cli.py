from __future__ import annotations

import argparse
from collections.abc import Sequence

from personal_lms import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="personal-lms")
    parser.add_argument(
        "--version",
        action="version",
        version=f"personal-lms {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
