# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               7_pnr -- harden the AION netlist, then simulate
# ================================================================

from __future__ import annotations

import json

from .. import paths
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
        return [paths.STEP_DIRS["3_rewrite"] / "nl" / f"{cfg.TOP}.nl.v",
                paths.CELLS_DIR]

    def outputs(self, cfg: Config) -> list:
        return [self.outdir / "nl" / f"{cfg.TOP}.nl.v",
                self.outdir / "gds" / f"{cfg.TOP}.gds",
                self.outdir / "metrics.json"]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, run = ctx.cfg, ctx.runner
        netlist = paths.STEP_DIRS["3_rewrite"] / "nl" / f"{cfg.TOP}.nl.v"
        self.require(netlist)

        published = self._published_cells()
        wanted = _instantiated_cells(netlist, cfg.CELL_PREFIX)
        info(f"{len(published)} cell(s) in "
             f"{paths.rel_to_project(paths.CELLS_DIR)}")
        for name in sorted(published):
            note(f"  {name}")

        missing = sorted(wanted - published)
        if missing and not self.dry_run:
            # PnR would fail late, inside OpenROAD, with a much worse message.
            raise StepFailed(
                "the netlist instantiates cells that have no published views: "
                + ", ".join(missing)
                + "\n  run `python flow.py 6` until every cell is published, "
                  "or rewrite with a library that only contains drawn cells.", 2)
        if not published:
            warn("no AI cells published — this run is PDK standard cells only, "
                 "which makes it a second pnr_simple rather than the AI flow")

        run.check(
            run.host("pnr", [
                f"NETLIST={netlist}",
                f"CELLS_DIR={paths.CELLS_DIR}",
                f"PNR_OUT_DIR={self.outdir}",
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
    def _published_cells(self) -> set:
        """Cell names discoverable under implementation/cells/, by file stem."""
        if not paths.CELLS_DIR.is_dir():
            return set()
        names = set()
        for path in paths.CELLS_DIR.rglob("*"):
            if path.is_file() and path.suffix.lower() in (".lef",):
                names.add(path.name[: -len(path.suffix)])
        return names

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
