# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               1_synth -- VHDL -> gate netlist, then simulate
# ================================================================

from __future__ import annotations

import json

from .. import paths
from ..config import Config, flag
from ..style import info, note, ok, warn
from .base import Context, Step


class SynthStep(Step):
    key = "1_synth"
    title = "Synthesis"
    summary = "VHDL -> gate-level netlist + pre-PnR STA, then post-synthesis simulation"
    upstream = ()
    needs_container = True          # librelane runs in iic-osic-tools

    @property
    def netlist(self):
        return self.outdir / "nl" / "tt_um_aion.nl.v"

    def inputs(self, cfg: Config) -> list:
        return [paths.PROJECT_ROOT / "src" / "rtl",
                paths.PROJECT_ROOT / "implementation" / "config.json",
                paths.PROJECT_ROOT / "implementation" / "constraints" / "aion.sdc"]

    def outputs(self, cfg: Config) -> list:
        return [self.outdir / "nl" / f"{cfg.TOP}.nl.v",
                self.outdir / "metrics.json"]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, run = ctx.cfg, ctx.runner

        # LibreLane through aion_chip's own wrapper. SYNTH_OUT_DIR is the
        # knob the Makefile grew for this handler; `make synth` on its own
        # still defaults to the same directory.
        run.check(
            run.host("synth", [
                f"SYNTH_OUT_DIR={self.outdir}",
                *flag("LENIENT", cfg.LENIENT),
            ], name="synth"),
            "synthesis",
        )

        netlist = self.outdir / "nl" / f"{cfg.TOP}.nl.v"
        self.require(netlist)
        self._report_metrics(ctx)

        # Function check on the netlist we just produced. flow/synth.core
        # points here, so the FuseSoC target needs no argument.
        self.ensure("sim")
        sim = run.host("post_synth_sim", [f"TOOL={cfg.SYNTH_SIM_TOOL}"],
                       name="post_synth_sim")
        if not sim.ok:
            warn("post-synthesis simulation FAILED — the netlist is suspect")
            ctx.note("post_synth_sim failed")
            run.check(sim, "post-synthesis simulation")
        ok(f"post-synthesis simulation passed ({cfg.SYNTH_SIM_TOOL})")
        return "ok"

    # -----------------------------------------------------------------
    def _report_metrics(self, ctx: Context) -> None:
        """Pull the two numbers every later step is judged against."""
        metrics_file = self.outdir / "metrics.json"
        if not metrics_file.exists():
            return
        try:
            metrics = json.loads(metrics_file.read_text())
        except (OSError, json.JSONDecodeError):
            return
        cells = metrics.get("design__instance__count")
        area = metrics.get("design__instance__area")
        wns = metrics.get("timing__setup__ws")
        info("synthesis baseline")
        note(f"cells {cells}   area {area} um2   setup ws {wns}")
        ctx.note(f"baseline: {cells} cells, {area} um2")


STEP = SynthStep()
