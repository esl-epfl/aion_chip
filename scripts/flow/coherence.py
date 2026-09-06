# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               flow/coherence.json -- is this flow coherent?
#
#  Every step records when it finished, the knobs it ran with, and a digest
#  of each file it consumed and produced.  A step is STALE when an upstream
#  step finished after it did, or when a file it consumed no longer hashes
#  to what it hashed at the time.  Either way the chip you would tape out is
#  not the chip these directories describe.
#
#  The flow warns and runs anyway: re-running one step in isolation is the
#  normal way to work here, and the warning is the point, not a gate.
# ================================================================

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from . import paths
from .style import Style, color

# 2: directory digests hash file CONTENT rather than mtimes. Records written
#    under 1 are not comparable and are discarded rather than reported stale.
FORMAT_VERSION = 2

OK = "ok"
FAILED = "failed"
BLOCKED = "blocked"      # step ran, but is waiting on something you must do
MISSING = "missing"
STALE = "stale"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _instant(stamp: str) -> Optional[datetime]:
    """Parse a recorded timestamp.

    Compared as instants rather than strings: two records written either side
    of a DST change carry different UTC offsets, and the string order is then
    not the time order.
    """
    try:
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


#: Files larger than this contribute their size rather than their bytes when a
#: whole directory is digested. A 3 MB GDS or a 1.3 MB SDF changing size is a
#: real change; re-reading every byte of them on every `status` call is not
#: worth the wall clock.
_BIG_FILE = 4 << 20


def digest(path) -> Optional[str]:
    """Content hash of a file, or of a directory tree.

    Directories hash their entries' names and CONTENT, not mtimes: a branch
    switch, a `git stash pop` or an editor save-without-change all move mtimes
    without changing anything, and a flow that cries STALE at those is a flow
    whose warnings get ignored.
    """
    p = Path(path)
    if not p.exists():
        return None
    if p.is_dir():
        h = hashlib.sha256()
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                f = Path(root) / name
                try:
                    size = f.stat().st_size
                except OSError:
                    continue
                h.update(f"{f.relative_to(p)}\0{size}\0".encode())
                if size <= _BIG_FILE:
                    inner = _file_digest(f)
                    if inner:
                        h.update(inner.encode())
        return "dir:" + h.hexdigest()
    return _file_digest(p)


def _file_digest(path: Path) -> Optional[str]:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return None
    return "sha256:" + h.hexdigest()


def _digests(items: Iterable) -> dict:
    out = {}
    for item in items:
        out[paths.rel_to_project(item)] = digest(item)
    return out


@dataclass
class Verdict:
    step: str
    state: str
    reasons: list

    @property
    def coherent(self) -> bool:
        return self.state == OK


class Coherence:
    """Read/write flow/coherence.json."""

    def __init__(self, path: Path = paths.COHERENCE_FILE):
        self.path = path
        self.data = {"version": FORMAT_VERSION, "steps": {}}
        self.unreadable = False
        if path.exists():
            try:
                loaded = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict) and loaded.get("version") == FORMAT_VERSION:
                self.data = loaded
                self.data.setdefault("steps", {})
            else:
                # Do not pretend the flow has never run. Something is there and
                # we could not use it; the next record() would overwrite it
                # silently, which is how you lose the only record of a run.
                self.unreadable = True

    # -----------------------------------------------------------------
    def record(self, step: str, *, status: str, started: str,
               inputs: Iterable = (), outputs: Iterable = (),
               config: Optional[dict] = None, notes: Iterable = (),
               commands: Optional[list] = None) -> None:
        self.data["steps"][step] = {
            "status": status,
            "started": started,
            "finished": now(),
            "config": config or {},
            "inputs": _digests(inputs),
            "outputs": _digests(outputs),
            "notes": list(notes),
            "commands": commands or [],
        }
        self.save()

    def get(self, step: str) -> Optional[dict]:
        return self.data["steps"].get(step)

    def save(self) -> None:
        """Write the file atomically.

        A step can finish while the machine is being shut down or the run is
        being interrupted; a half-written coherence file reads as "nothing
        has ever run", which is worse than a slightly stale one.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(self.data)
        payload["updated"] = now()
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(temp, self.path)

    def forget(self, step: str) -> None:
        self.data["steps"].pop(step, None)
        self.save()

    # -----------------------------------------------------------------
    def check(self, step: str, upstream: Iterable) -> Verdict:
        """Is `step`'s recorded result still a valid description of disk?"""
        record = self.get(step)
        if record is None:
            return Verdict(step, MISSING, ["never run"])

        reasons = []
        finished = _instant(record.get("finished", ""))
        for up in upstream:
            up_record = self.get(up)
            if up_record is None:
                reasons.append(f"{up} has never run")
                continue
            up_finished = _instant(up_record.get("finished", ""))
            if finished is None or up_finished is None:
                continue
            if up_finished > finished:
                reasons.append(f"{up} re-ran at {up_record['finished']}")

        for rel, recorded in (record.get("inputs") or {}).items():
            current = digest(paths.PROJECT_ROOT / rel)
            if current is None:
                reasons.append(f"input gone: {rel}")
            elif current != recorded:
                reasons.append(f"input changed: {rel}")

        for rel, recorded in (record.get("outputs") or {}).items():
            current = digest(paths.PROJECT_ROOT / rel)
            if current is None:
                reasons.append(f"output gone: {rel}")
            elif current != recorded:
                reasons.append(f"output changed since it was recorded: {rel}")

        # A step that failed, or that is waiting on something you must do,
        # reports that rather than "stale" -- the distinction is what tells
        # you whether to re-run it or to go and do the thing.
        status = record.get("status")
        if reasons:
            # Staleness outranks both: a blocked step whose inputs moved is
            # not merely waiting, and re-running it is the fix either way.
            return Verdict(step, STALE, reasons)
        if status == FAILED:
            return Verdict(step, FAILED, ["last run failed"])
        if status == BLOCKED:
            return Verdict(step, BLOCKED, ["waiting on you"])
        return Verdict(step, OK, [])


_BADGE = {
    OK:      (" OK      ", Style.GREEN),
    STALE:   (" STALE   ", Style.YELLOW),
    MISSING: (" MISSING ", Style.DIM),
    FAILED:  (" FAILED  ", Style.RED),
    BLOCKED: (" WAITING ", Style.CYAN),
}


def badge(state: str) -> str:
    text, style = _BADGE.get(state, (f" {state:7s}", Style.WHITE))
    return color(text, style, Style.BOLD)
