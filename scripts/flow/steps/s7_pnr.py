# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               7_pnr -- harden the AION netlist, then simulate
# ================================================================

from __future__ import annotations

import json

from .. import paths, pdk_ext
from ..config import Config, flag
from ..style import info, note, ok, warn
from .base import Context, Step, StepFailed


class PnrStep(Step):
    key = "7_pnr"
    title = "Place and route"
    summary = "harden the rewritten netlist with the AI cells, then simulate with SDF"
    upstream = ("3_rewrite", "6_layout_drawing")
    needs_container = True          # librelane runs in iic-osic-tools

    def inputs(self, cfg: Config) -> list:
        sources = [paths.STEP_DIRS["3_rewrite"] / "nl" / f"{cfg.TOP}.nl.v",
                   paths.CELLS_DIR]
        # The netlist can instantiate a hand-designed cell, and `make pnr`
        # hands its LEF, Liberty and GDS to LibreLane -- so redrawing or
        # recharacterizing one here is a changed input to the hardening, the
        # same way it is to synthesis.
        if cfg.PDK_EXT:
            sources.append(paths.PDK_EXT_DIR)
        return sources

    def outputs(self, cfg: Config) -> list:
        return [self.outdir / "nl" / f"{cfg.TOP}.nl.v",
                self.outdir / "gds" / f"{cfg.TOP}.gds",
                self.outdir / "metrics.json"]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, run = ctx.cfg, ctx.runner
        netlist = paths.STEP_DIRS["3_rewrite"] / "nl" / f"{cfg.TOP}.nl.v"
        self.require(netlist)

        # Two sources of leaf cells, and the netlist does not distinguish
        # them: the mined ones step 6 drew, and the hand-designed ones under
        # implementation/pdk_extension/ that the mapper chose in step 1. Both
        # have to be published before PnR can place either.
        ext = pdk_ext.views(ctx) if cfg.PDK_EXT else pdk_ext.NONE
        roots = [paths.CELLS_DIR] + ([paths.PDK_EXT_DIR] if ext.active else [])
        published = set()
        for root in roots:
            drawn = _drawn_cells(root)
            published |= drawn
            info(f"{len(drawn)} cell(s) in {paths.rel_to_project(root)}")
            for name in sorted(drawn):
                note(f"  {name}")

        wanted = _instantiated_cells(netlist, cfg.CELL_PREFIX)
        missing = sorted(wanted - published)
        if missing and not self.dry_run:
            # PnR would fail late, inside OpenROAD, with a much worse message.
            raise StepFailed(
                "the netlist instantiates cells that have no published views: "
                + ", ".join(missing)
                + "\n  run `python flow.py 6` until every mined cell is "
                  "published, or rewrite with a library that only contains "
                  "drawn cells."
                + ("\n  a hand-designed one is drawn with "
                   "`scripts/pdk_cell.py <CELL>`." if ext.active else ""), 2)
        if not published:
            warn("no AI cells published — this run is PDK standard cells only, "
                 "which makes it a second pnr_simple rather than the AI flow")

        run.check(
            run.host("pnr", [
                f"NETLIST={netlist}",
                f"CELLS_DIR={paths.CELLS_DIR}",
                f"PNR_OUT_DIR={self.outdir}",
                # Default-on in the Makefile, so it only has to be forwarded
                # to turn it off. With it off the extension cells are not
                # offered to PnR -- which is correct, because a netlist
                # synthesized with PDK_EXT=0 does not name any.
                *([] if cfg.PDK_EXT else ["PDK_EXT=0"]),
                *flag("LENIENT", cfg.LENIENT),
            ], name="pnr"),
            "place and route",
        )

        self.require(self.outdir / "nl" / f"{cfg.TOP}.nl.v")
        if not self.dry_run:
            self._report(ctx)

        self.ensure("sim")
        sim = run.host("post_pnr_sim_ai", [
            f"TOOL={cfg.PNR_SIM_TOOL}",
            f"SDF_CORNER={cfg.SDF_CORNER}",
        ], name="post_pnr_sim_ai")
        if not sim.ok:
            ctx.note("post_pnr_sim_ai failed")
            run.check(sim, "post-PnR simulation")
        ok(f"post-PnR simulation passed ({cfg.PNR_SIM_TOOL}, {cfg.SDF_CORNER})")
        if cfg.PNR_SIM_TOOL == "icarus":
            note("Icarus applies the SDF's delays but implements no timing "
                 "checks — setup and hold live in the STA reports under "
                 f"{paths.rel_to_project(self.outdir)}/reports/*-openroad-stapostpnr/")
        return "ok"

    # -----------------------------------------------------------------
    def _report(self, ctx: Context) -> None:
        metrics_file = self.outdir / "metrics.json"
        if not metrics_file.exists():
            return
        try:
            metrics = json.loads(metrics_file.read_text())
        except (OSError, json.JSONDecodeError):
            return
        info("hardened")
        note(f"instances {metrics.get('design__instance__count')}   "
             f"core area {metrics.get('design__core__area')}   "
             f"utilization {metrics.get('design__instance__utilization')}")
        note(f"setup ws {metrics.get('timing__setup__ws')}   "
             f"hold ws {metrics.get('timing__hold__ws')}   "
             f"DRC {metrics.get('route__drc_errors')}   "
             f"LVS {metrics.get('design__lvs_error__count')}")
        ctx.note(f"pnr: {metrics.get('design__instance__count')} instances, "
                 f"setup ws {metrics.get('timing__setup__ws')}")


def _drawn_cells(root) -> set:
    """Cells with a published abstract under `root`, by LEF file stem.

    The LEF is the view that decides this: a cell without one cannot be
    placed, whatever else it has. That holds for both directory shapes --
    implementation/cells/<CELL>/<CELL>.lef and
    implementation/pdk_extension/<CELL>/<CELL>.lef -- and neither the
    provisional Liberty nor the structural Verilog can be mistaken for it.
    """
    if not root.is_dir():
        return set()
    return {path.stem for path in root.rglob("*.lef") if path.is_file()}


def _instantiated_cells(netlist, prefix: str) -> set:
    """Which AION modules the netlist actually instantiates."""
    try:
        text = netlist.read_text()
    except OSError:
        return set()
    found = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            found.add(stripped.split()[0].split("(")[0])
    return found


STEP = PnrStep()
