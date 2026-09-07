# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               8_render -- draw the hardened die, and say
#                             where the AION cells ended up in it
#
#  The only step that produces nothing a tool downstream reads.  That is the
#  point: seven steps turn RTL into a GDS through numbers -- utilization,
#  worst slack, DRC count -- and none of them ever shows the thing itself.
#  This one does, and it is also the only place the flow answers "so where
#  did the AI cells actually land", which no metric in metrics.json carries.
#
#  Pure host Python: klayout.db reads the layout, klayout.lay draws it
#  offscreen and Pillow adds the header, the legend and the overlay.  No
#  container, no display, about a second for a 12k-instance die.
# ================================================================

from __future__ import annotations

from pathlib import Path

from .. import paths
from ..config import Config
from ..style import info, note, ok, warn
from .base import Context, Step, StepFailed


class RenderStep(Step):
    key = "8_render"
    title = "Render"
    summary = "draw the hardened die and mark where the AION cells landed"
    upstream = ("7_pnr",)
    needs_container = False         # klayout + Pillow, on the host

    # -----------------------------------------------------------------
    def gds(self, cfg: Config) -> Path:
        return paths.STEP_DIRS["7_pnr"] / "gds" / f"{cfg.TOP}.gds"

    def inputs(self, cfg: Config) -> list:
        return [self.gds(cfg)]

    def outputs(self, cfg: Config) -> list:
        return [self.outdir / f"{cfg.TOP}.chip.png",
                self.outdir / f"{cfg.TOP}.aion.png",
                self.outdir / "render.json"]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg = ctx.cfg
        gds = self.gds(cfg)
        self.require(gds)

        # Every picture is redrawn from scratch, so a stale PNG from a cell
        # that no longer exists must not survive into the new set.
        outdir = self.fresh()
        if self.dry_run:
            info(f"would render {paths.rel_to_project(gds)} into "
                 f"{paths.rel_to_project(outdir)}/")
            return "ok"

        try:
            from render_chip import render_chip
        except ImportError as exc:
            raise StepFailed(
                f"cannot import the renderer: {exc}\n"
                "  it needs klayout and Pillow on the host — "
                "`pip install klayout pillow`", 2) from exc

        try:
            record = render_chip(gds, outdir, prefix=cfg.CELL_PREFIX,
                                 width=cfg.RENDER_WIDTH,
                                 height=cfg.RENDER_HEIGHT,
                                 zoom_sites=cfg.RENDER_ZOOM_SITES)
        except Exception as exc:            # noqa: BLE001 - reported as a step failure
            raise StepFailed(f"rendering {paths.rel_to_project(gds)} failed: "
                             f"{exc}", 2) from exc

        self._report(ctx, record, outdir)
        return "ok"

    # -----------------------------------------------------------------
    def _report(self, ctx: Context, record: dict, outdir: Path) -> None:
        die = record["die_um"]
        info(f"{record['top']}  {die[0]:.3f} x {die[1]:.3f} um  "
             f"{record['die_area_um2']:,.0f} um²  "
             f"{record['instances']:,} placed instance(s)")

        by_cell = record["aion_instances_by_cell"]
        if not by_cell:
            # Step 7 says the same thing about the netlist; this says it about
            # what is actually on the die, which is the stronger statement.
            warn(f"no cell named {ctx.cfg.CELL_PREFIX}* is placed on this die "
                 "— the render shows a chip built from PDK cells only")
        else:
            area = record["area_um2_by_class"].get("aion", 0.0)
            core = record["core_area_um2"] or 0.0
            share = f"{100.0 * area / core:.2f}%" if core else "—"
            info(f"{record['instances_by_class'].get('aion', 0)} AION "
                 f"instance(s) of {len(by_cell)} cell(s), {area:,.1f} um² "
                 f"= {share} of the core")
            for name in sorted(by_cell, key=lambda k: -by_cell[k]):
                note(f"  {name:<28}{by_cell[name]:>5} x   "
                     f"{record['aion_area_um2_by_cell'][name]:>10,.1f} um²")

        ok(f"{len(record['images'])} image(s) in "
           f"{paths.rel_to_project(outdir)}/")
        for name in record["images"]:
            note(f"  {name}")
        ctx.note(f"rendered {record['instances']} instances, "
                 f"{record['instances_by_class'].get('aion', 0)} AION")


STEP = RenderStep()
