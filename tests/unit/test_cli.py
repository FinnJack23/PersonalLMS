from __future__ import annotations

import pytest

from personal_lms import __version__
from personal_lms.cli import main


def test_version_flag_prints_version_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_returns_zero() -> None:
    assert main([]) == 0
