# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Every knob of the AION chip flow, in one place
#
#  Same idea as aion_flow/examples/full_flow/flow.py: the variables live at
#  the top, the steps below read them.  Anything here can be overridden on
#  the command line with `--set NAME=VALUE`, so a one-off run never needs an
#  edit:  python flow.py 2 --set ELITE_COUNT=4
# ================================================================

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional


def optional(name: str, value) -> list:
    """Forward a Make variable only when it is set.

    Leaving a variable out keeps the Makefile/CLI default in charge instead
    of passing an empty value down the chain — several aion_flow targets use
    `$(if $(VAR),...)`, for which an empty string and an unset variable are
    the same thing but `VAR=` on the command line is not.
    """
    return [] if value is None else [f"{name}={value}"]


def flag(name: str, value: bool) -> list:
    """Forward a boolean Make variable only when it is true."""
    return [f"{name}=1"] if value else []


@dataclass
class Config:
    # =====================================================================
    # General
    # =====================================================================
    TOP: str = "tt_um_aion"

    # Downgrade LibreLane's hard checkers to warnings (steps 1 and 7).
    # Never use it for a signoff run.
    LENIENT: bool = False

    # =====================================================================
    # 1_synth
    # =====================================================================
    # verilator | icarus
    SYNTH_SIM_TOOL: str = "verilator"

    # =====================================================================
    # 2_pattern_extraction   (aion_opt mining)
    # =====================================================================
    # MAX_SIZE            cells per mined pattern (2..8)
    # MIN_OCCURRENCES     times a pattern must be mined to be kept
    # MIN_SELECTED        times it must survive the non-overlapping cover
    #                     (None = same as MIN_OCCURRENCES; 1 disables)
    # MAX_OUTPUTS         boundary outputs per pattern (None = no limit)
    # MAX_INPUTS          boundary inputs per pattern (None = no limit)
    # JOBS                mining workers (None = every core)
    # CELL_PREFIX         prefix of every generated module
    # ELITE_COUNT         size of the elite cell library (None = keep every
    #                     cell -- which is almost never what you want, since
    #                     every kept cell has to be drawn by hand in step 6)
    # ELITE_METRIC        saved-area | occurrences | saved-area-per-cell
    MAX_SIZE: int = 3
    MIN_OCCURRENCES: int = 2
    MIN_SELECTED: Optional[int] = None
    AREA_FACTOR: float = 0.65
    MAX_OUTPUTS: Optional[int] = 1
    MAX_INPUTS: Optional[int] = 5
    JOBS: Optional[int] = None
    CELL_PREFIX: str = "AION_"
    ELITE_COUNT: Optional[int] = 2
    ELITE_METRIC: str = "saved-area"

    # =====================================================================
    # 3_rewrite
    # =====================================================================
    # Which library the rewrite instantiates, and therefore which cells
    # steps 4-6 have to characterize, minimize and draw:
    #   "elite"  aion_cells_elite.v  (the ELITE_COUNT cut from step 2)
    #   "all"    aion_cells.v        (every mined cell)
    #   <path>   a hand-curated .v -- delete modules from a copy of either
    #            file, keeping each `// AION canonical_key:` comment
    #            attached to its module.  The rewriter filters the cover
    #            down to whatever is left; the deleted sites simply stay as
    #            PDK cells.  This is the supported way to pick cells by
    #            name rather than by rank.
    REWRITE_CELLS: str = "elite"

    # Emit the flattened netlist too (AION modules inlined). Only needed for
    # a sequential equivalence check; costs a second netlist write.
    REWRITE_FLAT: bool = False

    # Liberty the LEC reads. None = run_lec_sec.py's own default
    # (aion_flow/tech/lib/sg13g2_stdcell_typ_1p20V_25C.lib).
    LEC_LIB: Optional[str] = None

    # =====================================================================
    # 5_gate_minimization   (aion_minimizer)
    # =====================================================================
    # MINIMIZER_VERIFY        exhaustive switch-level equivalence, pure
    #                         Python, no simulator (cheap -- keep it on)
    # MINIMIZER_VERIFY_SPICE  the real ngspice 3-way check per cell
    #                         (gold vs PDK reference vs the new netlist)
    MINIMIZER_MODE: str = "transistor"
    MINIMIZER_WN: str = "0.74u"
    MINIMIZER_WP: str = "1.48u"
    MINIMIZER_L: str = "0.13u"
    MINIMIZER_MAX_INPUTS: int = 6
    MINIMIZER_VERIFY: bool = True
    MINIMIZER_VERIFY_SPICE: bool = True

    # =====================================================================
    # 6_layout_drawing   (aion_layout_claude)
    # =====================================================================
    # LAYOUT_CORNERS  MUST stay "typ".  "all" makes the exporter publish one
    #                 Liberty per corner, and `make pnr` groups cell views by
    #                 file stem -- three .lib files become three phantom
    #                 cells with no LEF and no GDS, and PnR refuses to start.
    # DRAW_MODE       manual | auto.  See step 6 for the loop.
    # DRAW_JOBS       cells worked on at once -- in auto mode that is this
    #                 many concurrent `claude` sessions, each also driving
    #                 the container, so it multiplies LAYOUT_JOBS.  Every
    #                 line of output is tagged with its cell.
    # DRAW_STREAM     narrate each agent turn while it runs (the tools it
    #                 calls and what they answered).  Off, `claude -p` says
    #                 nothing until the turn ends half an hour later.
    # DRAW_MODEL      the model that draws.  The layout generator is the one
    #                 artifact in this flow nothing can compute, and a turn
    #                 costs a container DRC/LVS round trip either way, so the
    #                 cheap model is not the cheap option here.
    # DRAW_EFFORT     how hard it thinks per turn: low | medium | high |
    #                 xhigh | max.  Either of these set to None ("none" on
    #                 the command line) leaves the CLI's own setting alone.
    LAYOUT_CORNERS: str = "typ"
    LAYOUT_JOBS: int = 8
    DRAW_MODE: str = "manual"
    DRAW_JOBS: int = 1
    DRAW_STREAM: bool = True
    DRAW_MAX_ITERS: int = 12
    DRAW_TIMEOUT: int = 1800
    DRAW_MODEL: Optional[str] = "claude-opus-5"
    DRAW_EFFORT: Optional[str] = "low"
    # Publish a cell into implementation/cells/ even when it lost the
    # area/delay comparison against the abutted PDK baseline.
    DRAW_PUBLISH_ON_LOSS: bool = True
    # PEX SPICE re-verification of the extracted layout (needs the container).
    LAYOUT_VERIFY_PEX: bool = True

    # =====================================================================
    # 7_pnr
    # =====================================================================
    # nom_typ_1p20V_25C | nom_fast_1p32V_m40C | nom_slow_1p08V_125C
    SDF_CORNER: str = "nom_slow_1p08V_125C"
    # icarus keeps the SDF delays; verilator drops them.
    PNR_SIM_TOOL: str = "icarus"

    # =====================================================================
    # Runner behaviour
    # =====================================================================
    # Per-command wall clock, seconds. Characterization is the long pole.
    TIMEOUT: int = 4 * 60 * 60

    _overrides: dict = field(default_factory=dict, repr=False)

    # -----------------------------------------------------------------
    #: Settings whose value must be one of a fixed set. A typo here would
    #: otherwise fall through to a default and look like a real result.
    CHOICES = {
        "DRAW_MODE": ("manual", "auto"),
        "SYNTH_SIM_TOOL": ("verilator", "icarus"),
        "PNR_SIM_TOOL": ("verilator", "icarus"),
        "LAYOUT_CORNERS": ("typ", "all"),
        # "none" is how the command line spells "leave the CLI's own effort
        # setting alone"; _coerce turns it into None.
        "DRAW_EFFORT": ("low", "medium", "high", "xhigh", "max", "none"),
        "ELITE_METRIC": ("saved-area", "occurrences", "saved-area-per-cell"),
        "MINIMIZER_MODE": ("transistor", "area", "balance"),
        "SDF_CORNER": ("nom_typ_1p20V_25C", "nom_fast_1p32V_m40C",
                       "nom_slow_1p08V_125C"),
    }

    def apply(self, assignments) -> None:
        """Apply `NAME=VALUE` overrides, coerced to the field's type."""
        known = {f.name: f for f in fields(self) if not f.name.startswith("_")}
        for raw in assignments:
            if "=" not in raw:
                raise ValueError(f"--set expects NAME=VALUE, got {raw!r}")
            name, _, value = raw.partition("=")
            name = name.strip()
            if name not in known:
                choices = ", ".join(sorted(known))
                raise ValueError(f"unknown setting {name!r}. Known: {choices}")
            text = value.strip()
            allowed = self.CHOICES.get(name)
            if allowed is not None and text not in allowed:
                raise ValueError(
                    f"{name}={text!r} is not one of: {', '.join(allowed)}")
            setattr(self, name, _coerce(known[name].type, text))
            self._overrides[name] = text

    def digest(self) -> dict:
        """The knobs, as recorded in the coherence file."""
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if not f.name.startswith("_")
        }


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _coerce(annotation, value: str):
    """Turn a command-line string into the type the field declares."""
    text = str(annotation)
    if value.lower() in {"none", "null", ""} and "Optional" in text:
        return None
    if "bool" in text:
        low = value.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"expected a boolean, got {value!r}")
    if "int" in text:
        return int(value)
    if "float" in text:
        return float(value)
    return value
