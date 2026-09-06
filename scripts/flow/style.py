# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Terminal styling for the AION flow handler
#
#  Same vocabulary as aion_flow/examples/full_flow/util/style.py, so the
#  two handlers read the same way when their output lands in one log.
# ================================================================

from __future__ import annotations

import os
import sys

_NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()


class Style:
    """ANSI colour/style escape sequences."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BG_GREEN = "\033[42m"
    BG_RED = "\033[41m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


def color(text: str, *codes: str) -> str:
    """Wrap text in ANSI codes and reset."""
    if _NO_COLOR:
        return text
    return "".join(codes) + text + Style.RESET


def banner(text: str, width: int = 78, fill: str = "=") -> str:
    """Return a centered banner line."""
    pad = max(0, width - len(text) - 2)
    left = fill * (pad // 2)
    right = fill * (pad - len(left))
    return f"{left} {text} {right}"


def section(name: str) -> None:
    print()
    print(color(banner(f" {name.upper()} ", fill="═"), Style.MAGENTA, Style.BOLD))
    print()


def title(name: str) -> None:
    print()
    print(color(banner(f" {name.upper()} ", fill="═"), Style.BLUE, Style.BOLD))
    print()


def ok(message: str) -> None:
    print(color(f"  ✔ {message}", Style.GREEN))


def warn(message: str) -> None:
    print(color(f"  ⚠ {message}", Style.YELLOW, Style.BOLD))


def fail(message: str) -> None:
    print(color(f"  ✖ {message}", Style.RED, Style.BOLD))


def info(message: str) -> None:
    print(color(f"  → {message}", Style.DIM))


def note(message: str) -> None:
    print(color(f"    {message}", Style.DIM))
