# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               One `claude -p` turn, narrated while it runs
#
#  `claude -p` with the default text output prints exactly one thing, at the
#  very end: the final message.  A layout turn is twenty minutes to half an
#  hour of DRC, LVS and edits, so from the outside it is indistinguishable
#  from a hung flow -- which is exactly what it looked like.
#
#  `--output-format stream-json --verbose` emits one JSON object per event
#  instead, and this module turns that into the log a human wants to watch:
#  the tools the agent runs, the verdict lines they print back, and a
#  heartbeat while one tool call is taking minutes.  The raw JSONL is teed to
#  the log file as it arrives rather than at the end, so a turn that is
#  interrupted or times out still leaves its whole history on disk.
#
#  Nothing here grades anything.  What the agent says it did is never the
#  verdict; step 6 grades the file on disk with its own `make verify`.
# ================================================================

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from . import runner
from .style import Style, color, current_prefix, emit, prefixed

#: What turns `claude -p` from one block at the end into a live feed.
#: --verbose is not optional: --print refuses stream-json without it.
STREAM_FLAGS = ("--output-format", "stream-json", "--verbose")

#: Longest line this module prints. The agent's prose and a `make` command
#: line are both routinely wider than a terminal, and wrapped lines from
#: several cells at once are unreadable.
WIDTH = 150

#: Tool inputs worth showing, in the order they are looked for.
_INPUT_KEYS = ("command", "file_path", "path", "pattern", "notebook_path",
               "url", "prompt", "description")

#: A tool result line that starts at column 0 with one of these is the
#: layout tool's verdict; it beats any other line in the result.
_VERDICTS = ("RESULT:", "COMPARE:", "STEP:")


@dataclass
class AgentTurn:
    """What one `claude -p` invocation did, as observed from outside."""

    returncode: int = 0
    duration_s: float = 0.0
    report: str = ""              # the agent's own last word
    session_id: str = ""
    turns: Optional[int] = None
    cost_usd: Optional[float] = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# ---------------------------------------------------------------------
def run(argv: Sequence[str], *, cwd: Path, env: dict, log: Path,
        timeout: int, stream: bool = True, echo: bool = True,
        heartbeat: float = 45.0) -> AgentTurn:
    """Run one agent turn, teeing it to `log` and echoing what it does.

    `stream` adds the stream-json flags and parses them; without it the CLI
    prints one block at the end and that block is all anyone sees. `echo`
    is the terminal half only -- the log is written either way, because the
    log is the record and the echo is a convenience.
    """
    argv = list(argv) + (list(STREAM_FLAGS) if stream else [])
    if runner.cancelled():
        return AgentTurn(130, 0.0, report="not started, the run was interrupted")

    started = time.monotonic()
    state = {"last": started}
    turn = AgentTurn()
    said = ""                     # last assistant text, the fallback report
    returncode = 0

    log.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        list(argv), cwd=str(cwd), env=env,
        # Closed, not inherited: the CLI waits three seconds for a prompt on
        # stdin and then warns about it, on every single turn, and the flow's
        # own stdin belongs to whoever is watching the run.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1, start_new_session=True,
    )
    runner.register(process)

    timed_out = threading.Event()

    def _expire() -> None:
        timed_out.set()
        runner.terminate(process)

    watchdog = threading.Timer(timeout, _expire)
    watchdog.daemon = True
    watchdog.start()

    stop = threading.Event()
    beat = threading.Thread(
        target=_beat, daemon=True,
        args=(stop, state, current_prefix(), started, heartbeat),
    )
    if echo and heartbeat:
        beat.start()

    try:
        with open(log, "w", encoding="utf-8", errors="replace") as handle:
            handle.write(f"# cwd: {cwd}\n# cmd: {_redacted(argv)}\n\n")
            handle.flush()
            assert process.stdout is not None
            for line in process.stdout:
                handle.write(line)
                handle.flush()          # so `tail -f` shows a live turn
                text = line.strip()
                if not text:
                    continue

                event = _parse(text) if stream else None
                if event is not None:
                    turn.session_id = event.get("session_id") or turn.session_id
                    if event.get("type") == "result":
                        turn.turns = event.get("num_turns")
                        turn.cost_usd = event.get("total_cost_usd")
                    said = _spoken(event) or said
                    rendered = _render(event)
                else:
                    rendered = [(_flat(text), (Style.DIM,))]

                if echo:
                    for message, codes in rendered:
                        _echo(message, started, codes)
                        state["last"] = time.monotonic()
            returncode = process.wait()
    except KeyboardInterrupt:
        runner.terminate(process)
        raise
    finally:
        watchdog.cancel()
        stop.set()
        runner.unregister(process)

    turn.timed_out = timed_out.is_set()
    turn.returncode = 124 if turn.timed_out else returncode
    turn.duration_s = time.monotonic() - started
    turn.report = said
    if echo:
        _echo(_epilogue(turn), started,
              (Style.GREEN,) if turn.ok else (Style.YELLOW,))
    return turn


# ---------------------------------------------------------------------
def _epilogue(turn: AgentTurn) -> str:
    bits = [f"claude exit {turn.returncode}"]
    if turn.timed_out:
        bits.append("TIMED OUT")
    if turn.turns is not None:
        bits.append(f"{turn.turns} turn(s)")
    if turn.cost_usd is not None:
        bits.append(f"${turn.cost_usd:.2f}")
    if turn.session_id:
        bits.append(f"session {turn.session_id[:8]}")
    return "  ".join(bits)


def _redacted(argv: Sequence[str]) -> str:
    """The command line with the prompt replaced by its size.

    The prompt carries up to 60k of evidence packet; it is reproducible from
    the step and the packet in the build directory, and pasting it at the
    top of every turn log would bury the turn.
    """
    out = []
    skip = False
    for arg in argv:
        if skip:
            out.append(f"<prompt: {len(arg)} chars>")
            skip = False
            continue
        out.append(arg)
        skip = arg in ("-p", "--print")
    return " ".join(out)


def _parse(text: str) -> Optional[dict]:
    """One stream-json event, or None when the line is not one.

    The CLI writes the odd plain-text warning to stderr, which is merged
    into this stream; those lines are printed as they are rather than
    dropped, since a warning about credentials is exactly what explains a
    turn that did nothing.
    """
    if not text.startswith("{"):
        return None
    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _spoken(event: dict) -> str:
    """The agent's own words in this event, if it had any."""
    if event.get("type") == "result":
        return _flat(str(event.get("result") or ""), 300)
    if event.get("type") != "assistant":
        return ""
    for block in _blocks(event):
        if block.get("type") == "text" and block.get("text", "").strip():
            return _flat(block["text"], 300)
    return ""


def _render(event: dict) -> list:
    """(message, ansi codes) pairs for one event. Unknown types print nothing."""
    kind = event.get("type")

    if kind == "system" and event.get("subtype") == "init":
        model = event.get("model") or "?"
        tools = len(event.get("tools") or ())
        return [(f"session {(event.get('session_id') or '?')[:8]}  "
                 f"model {model}  {tools} tool(s)", (Style.DIM,))]

    if kind == "assistant":
        lines = []
        for block in _blocks(event):
            btype = block.get("type")
            if btype == "text" and block.get("text", "").strip():
                lines.append((_flat(block["text"], 300), ()))
            elif btype == "thinking" and block.get("thinking", "").strip():
                # Kept short on purpose: it is here as a sign of life and a
                # hint at what the agent is chasing, not as a transcript.
                lines.append(("· " + _flat(block["thinking"], 140),
                              (Style.DIM,)))
            elif btype == "tool_use":
                lines.append(("⚙ " + _tool(block.get("name") or "tool",
                                           block.get("input") or {}),
                              (Style.CYAN,)))
        return lines

    if kind == "user":
        lines = []
        for block in _blocks(event):
            if block.get("type") != "tool_result":
                continue
            digest = _digest(_text_of(block.get("content")))
            if not digest:
                continue
            bad = bool(block.get("is_error"))
            lines.append((("✖ " if bad else "↳ ") + digest,
                          (Style.YELLOW,) if bad else (Style.DIM,)))
        return lines

    if kind == "result":
        message = _flat(str(event.get("result") or event.get("subtype") or ""), 300)
        if not message:
            return []
        return [("⇢ " + message,
                 (Style.RED,) if event.get("is_error") else (Style.WHITE,))]

    return []


def _blocks(event: dict) -> list:
    content = (event.get("message") or {}).get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _tool(name: str, data: dict) -> str:
    """`Bash  make -C ... verify` -- the tool and the one argument that says
    which call this is."""
    for key in _INPUT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return f"{name}  {_flat(value, WIDTH - len(name) - 2)}"
    return name


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return ""


def _digest(text: str) -> str:
    """One line out of a tool result: its verdict, or its first line."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    for line in lines:
        if line.startswith(_VERDICTS):
            return _flat(line)
    head = _flat(lines[0])
    return head if len(lines) == 1 else f"{head}  (+{len(lines) - 1} line(s))"


def _flat(text: str, limit: int = WIDTH) -> str:
    """One line, no runaway width."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: max(1, limit - 1)] + "…"


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _echo(message: str, started: float, codes: Sequence[str] = ()) -> None:
    emit(color(f"    {_clock(time.monotonic() - started)}  ", Style.DIM)
         + color(message, *codes))


def _beat(stop: threading.Event, state: dict, prefix: str,
          started: float, interval: float) -> None:
    """Say the turn is alive while a single tool call takes minutes.

    A container DRC run is several silent minutes on its own, which is long
    enough for the whole step to read as hung again.
    """
    with prefixed(prefix):
        while not stop.wait(min(interval, 5.0)):
            quiet = time.monotonic() - state["last"]
            if quiet < interval:
                continue
            state["last"] = time.monotonic()
            _echo(f"… still working ({_clock(quiet)} without output)",
                  started, (Style.DIM,))
