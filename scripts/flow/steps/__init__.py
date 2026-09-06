# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The seven steps, in order
# ================================================================

from __future__ import annotations

from typing import Optional

from . import (s1_synth, s2_pattern_extraction, s3_rewrite,
               s4_characterization, s5_gate_minimization,
               s6_layout_drawing, s7_pnr)
from .base import Context, Step, StepFailed

STEPS = [
    s1_synth.STEP,
    s2_pattern_extraction.STEP,
    s3_rewrite.STEP,
    s4_characterization.STEP,
    s5_gate_minimization.STEP,
    s6_layout_drawing.STEP,
    s7_pnr.STEP,
]

BY_KEY = {step.key: step for step in STEPS}
BY_NUMBER = {step.key.split("_", 1)[0]: step for step in STEPS}


def resolve(token: str) -> Step:
    """Accept `3`, `3_rewrite`, `rewrite` or a unique prefix of the name."""
    token = str(token).strip().lower()
    if token in BY_NUMBER:
        return BY_NUMBER[token]
    if token in BY_KEY:
        return BY_KEY[token]
    matches = [s for s in STEPS if s.key.split("_", 1)[1].startswith(token)]
    if len(matches) == 1:
        return matches[0]
    known = ", ".join(f"{s.key.split('_', 1)[0]}={s.key}" for s in STEPS)
    if matches:
        raise KeyError(f"{token!r} is ambiguous. Steps: {known}")
    raise KeyError(f"unknown step {token!r}. Steps: {known}")


def expand(tokens) -> list:
    """`2..5` and `4-` expand to ranges; anything else resolves one step."""
    if not tokens:
        return list(STEPS)
    out: list = []
    for token in tokens:
        text = str(token).strip()
        if ".." in text or (text.endswith("-") and len(text) > 1):
            lo, _, hi = text.partition("..") if ".." in text else (text[:-1], "", "")
            start = STEPS.index(resolve(lo))
            stop = STEPS.index(resolve(hi)) if hi else len(STEPS) - 1
            chosen = STEPS[start:stop + 1] if start <= stop else STEPS[stop:start + 1]
        else:
            chosen = [resolve(text)]
        for step in chosen:
            if step not in out:
                out.append(step)
    return out


def upstream_of(step: Step) -> list:
    """Every step `step` transitively depends on, in flow order."""
    seen: list = []

    def walk(key: str) -> None:
        for parent in BY_KEY[key].upstream:
            if parent not in seen:
                seen.append(parent)
                walk(parent)

    walk(step.key)
    return [k for k in (s.key for s in STEPS) if k in seen]


__all__ = ["STEPS", "BY_KEY", "BY_NUMBER", "resolve", "expand",
           "upstream_of", "Context", "Step", "StepFailed"]
