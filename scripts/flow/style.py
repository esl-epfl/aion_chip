# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Terminal styling for the AION flow handler
#
#  Same vocabulary as aion_flow/examples/full_flow/util/style.py, so the
#  two handlers read the same way when their output lands in one log.
#
#  Everything goes through emit(), which holds a lock for the whole message
#  and stamps it with the calling thread's prefix.  Step 6 draws several
#  cells at once, each in its own thread; without the lock their lines
#  interleave mid-line, and without the prefix there is no way to tell which
#  cell any given line came from.
# ================================================================

from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager

_NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()

_print_lock = threading.RLock()
_local = threading.local()


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
    if _NO_COLOR or not codes:
        return text
    return "".join(codes) + text + Style.RESET


# ---------------------------------------------------------------------
def current_prefix() -> str:
    """The tag this thread's output is stamped with ('' for the main one)."""
    return getattr(_local, "prefix", "")


@contextmanager
def prefixed(prefix: str):
    """Stamp every line printed by this thread with `prefix`."""
    previous = current_prefix()
    _local.prefix = prefix
    try:
        yield
    finally:
        _local.prefix = previous


def emit(text: str = "") -> None:
    """Print one message atomically, prefixed for the calling thread.

    Multi-line messages get the prefix on every line, so a tagged stream
    stays tagged even when a tool prints a paragraph. Blank lines stay
    blank: a column of empty tags is noise.
    """
    prefix = current_prefix()
    if prefix and text:
        text = "\n".join(prefix + line if line else line
                         for line in text.split("\n"))
    with _print_lock:
        print(text, flush=True)


# ---------------------------------------------------------------------
def banner(text: str, width: int = 78, fill: str = "=") -> str:
    """Return a centered banner line."""
    pad = max(0, width - len(text) - 2)
    left = fill * (pad // 2)
    right = fill * (pad - len(left))
    return f"{left} {text} {right}"


def section(name: str) -> None:
    emit()
    emit(color(banner(f" {name.upper()} ", fill="═"), Style.MAGENTA, Style.BOLD))
    emit()


def title(name: str) -> None:
    emit()
    emit(color(banner(f" {name.upper()} ", fill="═"), Style.BLUE, Style.BOLD))
    emit()


def ok(message: str) -> None:
    emit(color(f"  ✔ {message}", Style.GREEN))


def warn(message: str) -> None:
    emit(color(f"  ⚠ {message}", Style.YELLOW, Style.BOLD))


def fail(message: str) -> None:
    emit(color(f"  ✖ {message}", Style.RED, Style.BOLD))


def info(message: str) -> None:
    emit(color(f"  → {message}", Style.DIM))


def note(message: str) -> None:
    emit(color(f"    {message}", Style.DIM))
