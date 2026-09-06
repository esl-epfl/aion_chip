# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               3_rewrite -- substitute the cells, prove it, sim it
# ================================================================

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from .. import paths
from ..config import Config, optional
from ..style import info, note, ok, warn
from .base import Context, Step, StepFailed
from .s2_pattern_extraction import mining_vars


class RewriteStep(Step):
    key = "3_rewrite"
    title = "Rewrite"
    summary = "instantiate the AION cells, prove equivalence (LEC), re-simulate"
    upstream = ("1_synth", "2_pattern_extraction")
    needs_container = True          # LEC runs kepler-formal

    @property
    def netlist(self):
        return self.outdir / "nl" / "tt_um_aion.nl.v"

    @property
    def cells(self):
        """The library of record: exactly the cells this netlist instantiates.

        Steps 4, 5 and 6 all read this file rather than step 2's, so that a
        hand-curated selection propagates without being re-derived.
        """
        return self.outdir / "nl" / "aion_cells.v"

    def inputs(self, cfg: Config) -> list:
        return [paths.STEP_DIRS["1_synth"] / "nl" / f"{cfg.TOP}.nl.v",
                self._source_library(cfg),
                paths.STEP_DIRS["2_pattern_extraction"] / "work" / "selection.json"]

    def outputs(self, cfg: Config) -> list:
        return [self.outdir / "nl" / f"{cfg.TOP}.nl.v", self.cells,
                self.outdir / "report" / "rewrite_report.json"]

    # -----------------------------------------------------------------
    def _source_library(self, cfg: Config) -> Path:
        step2 = paths.STEP_DIRS["2_pattern_extraction"]
        choice = cfg.REWRITE_CELLS
        if choice == "elite":
            return step2 / "aion_cells_elite.v"
        if choice == "all":
            return step2 / "aion_cells.v"
        path = Path(choice)
        return path if path.is_absolute() else (paths.PROJECT_ROOT / path)

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, run = ctx.cfg, ctx.runner
        step1 = paths.STEP_DIRS["1_synth"] / "nl" / f"{cfg.TOP}.nl.v"
        step2 = paths.STEP_DIRS["2_pattern_extraction"]
        selection = step2 / "work" / "selection.json"
        source = self._source_library(cfg)

        self.require(step1, source, selection)

        nl_dir = self.ensure("nl")
        self.ensure("report")

        # Freeze the chosen library here. Everything downstream reads this
        # copy, so a later edit to step 2's output cannot silently change
        # what step 6 is asked to draw.
        if not self.dry_run and source.resolve() != self.cells.resolve():
            shutil.copy2(source, self.cells)
        elif source.resolve() == self.cells.resolve():
            # REWRITE_CELLS pointing at the frozen copy is a normal way to
            # re-run after hand-editing it; there is nothing to copy.
            info("rewriting with this step's own frozen library")
        kept = _module_names(self.cells) if self.cells.exists() else []
        info(f"library of record: {paths.rel_to_project(source)} -> "
             f"{len(kept)} cell(s)")
        for name in kept:
            note(f"  {name}")
        if not kept and not self.dry_run:
            raise StepFailed(f"{paths.rel_to_project(source)} defines no modules", 2)

        netlist = nl_dir / f"{cfg.TOP}.nl.v"
        flat = nl_dir / f"{cfg.TOP}.flat.nl.v"
        report_prefix = self.outdir / "report" / "rewrite_report"

        run.check(
            run.flow_host("aion-opt-rewrite", [
                f"INPUT={step1}",
                f"TOP={cfg.TOP}",
                *mining_vars(cfg),
                f"CELLS={self.cells}",
                f"REWRITE_NETLIST={netlist}",
                *(["REWRITE_FLAT=" + str(flat)] if cfg.REWRITE_FLAT else []),
                f"REWRITE_REPORT={report_prefix}",
                f"SELECTION={selection}",
                f"BUILD_DIR={self.outdir / 'steps'}",
            ], name="aion-opt-rewrite"),
            "rewrite",
        )
        self.require(netlist)
        if not self.dry_run:
            self._report(ctx)

        # ---- LEC: the rewritten netlist must be the netlist we started from.
        # kepler-formal is container-only. MOD is two files because the
        # netlist instantiates the AION modules and both have to be readable
        # in one compilation unit.
        # Two paths in one make variable, deliberately: the Makefile
        # interpolates MOD unquoted into the script's `--mod` (nargs="+").
        # No quotes here -- Runner.container() quotes each assignment once for
        # the shell hop into the container, and quoting it twice makes the
        # pair a single unopenable filename.
        mod = f"{paths.container(netlist)} {paths.container(self.cells)}"
        lec = run.container("aion-opt-lec", [
            f"REF={paths.container(step1)}",
            f"MOD={mod}",
            f"BUILD_DIR={paths.container(self.outdir / 'steps')}",
            # LEC runs in the container, so a Liberty named on the host has to
            # be renamed too; kepler-formal cannot open /home/... from there.
            *optional("LIB", _lec_lib(cfg)),
        ], name="aion-opt-lec")
        if not lec.ok:
            # run_lec_sec.py decides by string-matching its log, so a real
            # mismatch and a tool crash share an exit status. Say which.
            verdict = "MISMATCH" if "Difference was found." in lec.output else \
                      "did not reach a verdict"
            warn(f"LEC {verdict} — see {paths.rel_to_project(lec.log)}")
            ctx.note(f"LEC {verdict}")
            run.check(lec, "logical equivalence check")
        ok("LEC passed: the rewritten netlist is logically identical")

        # ---- Function check with the new cells in place.
        self.ensure("sim")
        sim = run.host("post_synth_sim_ai", [f"TOOL={cfg.SYNTH_SIM_TOOL}"],
                       name="post_synth_sim_ai")
        if not sim.ok:
            ctx.note("post_synth_sim_ai failed")
            run.check(sim, "post-synthesis simulation of the rewritten netlist")
        ok(f"post-synthesis simulation passed with the AION cells "
           f"({cfg.SYNTH_SIM_TOOL})")
        return "ok"

    # -----------------------------------------------------------------
    def _report(self, ctx: Context) -> None:
        """`aion-opt-rewrite` exits 0 even when it substituted nothing."""
        report = self.outdir / "report" / "rewrite_report.json"
        if not report.exists():
            warn("no rewrite report — cannot confirm anything was substituted")
            return
        try:
            summary = json.loads(report.read_text()).get("summary", {})
        except (OSError, json.JSONDecodeError):
            return
        applied = summary.get("patterns_applied", 0)
        sites = summary.get("occurrences_applied", 0)
        reduction = summary.get("cell_reduction")
        saved = summary.get("estimated_total_area_savings")
        info(f"applied {applied} pattern(s) at {sites} site(s)")
        note(f"cell reduction {reduction}   estimated area saved {saved}")
        ctx.note(f"{applied} patterns at {sites} sites, cell reduction {reduction}")
        if not applied:
            raise StepFailed(
                "the rewrite substituted nothing: no cell in the library "
                "matches the mined cover. Check that the mining knobs match "
                "step 2 and that the curated library kept its "
                "'// AION canonical_key:' comments.", 2)


def _lec_lib(cfg: Config) -> Optional[str]:
    """LEC_LIB as the container sees it, or None to keep the tool's default."""
    if not cfg.LEC_LIB:
        return None
    path = Path(cfg.LEC_LIB)
    if not path.is_absolute():
        path = paths.PROJECT_ROOT / path
    return paths.container(path)


def _module_names(path: Path) -> list:
    names = []
    for line in path.read_text().splitlines():
        stripped = line.lstrip()
        if stripped.startswith("module "):
            names.append(stripped[len("module "):].split("(")[0].strip().rstrip(";"))
    return names


STEP = RewriteStep()
