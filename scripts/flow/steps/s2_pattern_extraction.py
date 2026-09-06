# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               2_pattern_extraction -- mine the AION cells
#
#  Deliberately separate from 3_rewrite: between the two you may hand-curate
#  which cells survive, and every kept cell is a cell you will have to draw
#  by hand in step 6.
# ================================================================

from __future__ import annotations

import json

from .. import paths
from ..config import Config, optional
from ..style import info, note, ok, warn
from .base import Context, Step

#: The mining knobs that must be spelled identically here and in 3_rewrite,
#: or the selection cache's fingerprint stops matching and the rewrite
#: silently re-mines a different cover than the cells were built from.
def mining_vars(cfg: Config) -> list:
    return [
        f"MAX_SIZE={cfg.MAX_SIZE}",
        f"MIN_OCCURRENCES={cfg.MIN_OCCURRENCES}",
        f"AREA_FACTOR={cfg.AREA_FACTOR}",
        f"CELL_PREFIX={cfg.CELL_PREFIX}",
        *optional("MIN_SELECTED", cfg.MIN_SELECTED),
        *optional("MAX_OUTPUTS", cfg.MAX_OUTPUTS),
        *optional("MAX_INPUTS", cfg.MAX_INPUTS),
        *optional("JOBS", cfg.JOBS),
    ]


class PatternExtractionStep(Step):
    key = "2_pattern_extraction"
    title = "Pattern extraction"
    summary = "mine recurring subgraphs and emit the AION cell library"
    upstream = ("1_synth",)

    @property
    def cells(self):
        return self.outdir / "aion_cells.v"

    @property
    def elite_cells(self):
        return self.outdir / "aion_cells_elite.v"

    @property
    def selection(self):
        return self.outdir / "work" / "selection.json"

    @property
    def pattern_report(self):
        return self.outdir / "report" / "pattern_report.json"

    def inputs(self, cfg: Config) -> list:
        return [paths.STEP_DIRS["1_synth"] / "nl" / f"{cfg.TOP}.nl.v"]

    def outputs(self, cfg: Config) -> list:
        return [self.cells, self.elite_cells, self.selection, self.pattern_report]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, run = ctx.cfg, ctx.runner
        netlist = paths.STEP_DIRS["1_synth"] / "nl" / f"{cfg.TOP}.nl.v"
        self.require(netlist)

        self.ensure("report")
        self.ensure("work")

        if cfg.ELITE_COUNT is None:
            warn("ELITE_COUNT is unset: the elite library will keep EVERY mined "
                 "cell, and step 6 draws one layout per cell by hand")

        # Pure host Python plus yosys — no container, host paths throughout.
        run.check(
            run.flow_host("aion-opt-generate-cells", [
                f"INPUT={netlist}",
                f"TOP={cfg.TOP}",
                *mining_vars(cfg),
                f"ELITE_METRIC={cfg.ELITE_METRIC}",
                *optional("ELITE_COUNT", cfg.ELITE_COUNT),
                f"CELLS={self.cells}",
                f"ELITE_CELLS={self.elite_cells}",
                f"PATTERN_REPORT={self.pattern_report}",
                f"SELECTION={self.selection}",
                f"BUILD_DIR={self.outdir / 'steps'}",
            ], name="aion-opt-generate-cells"),
            "cell generation",
        )

        self.require(self.cells, self.elite_cells, self.pattern_report)
        self._report(ctx)
        return "ok"

    # -----------------------------------------------------------------
    def _report(self, ctx: Context) -> None:
        full = _count_modules(self.cells)
        elite = _count_modules(self.elite_cells)
        info(f"mined {full} cell(s); elite library keeps {elite}")
        ctx.note(f"{full} cells mined, {elite} elite")

        if elite == full and ctx.cfg.ELITE_COUNT is None:
            warn(f"the elite library is the full library ({full} cells)")

        try:
            report = json.loads(self.pattern_report.read_text())
        except (OSError, json.JSONDecodeError):
            return

        # `patterns_selected` is the non-overlapping cover -- the patterns that
        # can actually be substituted. `patterns_found` is everything mined,
        # including the ones the cover had to drop, so ranking that would
        # recommend cells the rewrite cannot use.
        entries = [e for e in report.get("patterns_selected", [])
                   if isinstance(e, dict)]
        if not entries:
            return
        ranked = sorted(entries,
                        key=lambda e: -float(e.get("total_saved_area") or 0))[:8]
        note("")
        note(f"{'cell':<32}{'uses':>6}{'saved um2':>12}   elite")
        for entry in ranked:
            note(f"{entry.get('module_name', '?'):<32}"
                 f"{entry.get('occurrences', 0):>6}"
                 f"{float(entry.get('total_saved_area') or 0):>12.1f}"
                 f"   {'yes' if entry.get('elite') else ''}")
        note("")
        note("to pick cells by name instead of by rank, curate before step 3:")
        note("  cp flow/2_pattern_extraction/aion_cells.v flow/my_cells.v")
        note("  $EDITOR flow/my_cells.v      # delete what you do not want,")
        note("                               # keeping each module's")
        note("                               # '// AION canonical_key:' comment")
        note("  ./flow.py 3 --set REWRITE_CELLS=flow/my_cells.v")


def _count_modules(path) -> int:
    try:
        text = path.read_text()
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("module "))


STEP = PatternExtractionStep()
