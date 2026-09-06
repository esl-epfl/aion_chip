# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               5_gate_minimization -- one transistor-level cell
#                             per AION module, proved in SPICE
# ================================================================

from __future__ import annotations

from .. import paths
from ..config import Config, flag
from ..style import info, note, ok, warn
from .base import Context, Step, StepFailed
from .s4_characterization import _reference_cells


class GateMinimizationStep(Step):
    key = "5_gate_minimization"
    title = "Gate minimization"
    summary = "re-implement each cell as one CMOS stack, verified in SPICE"
    upstream = ("4_characterization",)
    needs_container = True          # the per-cell SPICE proof needs ngspice

    @property
    def raw_spice(self):
        return self.outdir / "raw_spice"

    @property
    def minimized_spice(self):
        return self.outdir / "minimized_spice"

    def inputs(self, cfg: Config) -> list:
        step4 = paths.STEP_DIRS["4_characterization"]
        return [step4 / "steps" / "aion_char" / "tb" / "spice" / "reference_cells.spice",
                paths.STEP_DIRS["3_rewrite"] / "nl" / "aion_cells.v"]

    def outputs(self, cfg: Config) -> list:
        return [self.raw_spice, self.minimized_spice]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, run = ctx.cfg, ctx.runner
        step4 = paths.STEP_DIRS["4_characterization"]
        reference = step4 / "steps" / "aion_char" / "tb" / "spice" / "reference_cells.spice"
        cells = paths.STEP_DIRS["3_rewrite"] / "nl" / "aion_cells.v"
        self.require(reference, cells)

        # One gate-level file per cell. Host-side pure Python.
        # (`split-spice-cells` is the target that splits; the script's own
        # subcommand names are inverted, which is why we go through make.)
        self.fresh("raw_spice")
        run.check(
            run.flow_host("split-spice-cells", [
                f"INPUT={reference}",
                f"OUTPUT={self.raw_spice}",
            ], name="split-spice-cells"),
            "splitting the reference cells",
        )
        pieces = sorted(self.raw_spice.glob("*.spice"))
        info(f"{len(pieces)} gate-level cell(s) to minimize")
        if not pieces and not self.dry_run:
            raise StepFailed("split produced no cells", 2)

        self.fresh("minimized_spice")
        # BUILD_DIR must NOT be step 4's: each per-cell SPICE verification
        # regenerates the aion_char testbench tree restricted to that one
        # module, which deletes the other decks and rewrites
        # reference_cells.spice — the very file we just split.
        build = self.ensure("steps")
        run.check(
            run.container("run-aion-minimizer-batch", [
                f"INPUT_DIR={paths.container(self.raw_spice)}",
                f"OUTPUT_DIR={paths.container(self.minimized_spice)}",
                # relative to the aion_flow root, which is the container cwd
                "GATES=tech/spice/sg13g2_stdcell.spice",
                # Required whenever VERIFY_SPICE is on: without it the target
                # falls back to an aion_opt path that does not exist here.
                f"NETLIST={paths.container(cells)}",
                f"BUILD_DIR={paths.container(build)}",
                f"MODE={cfg.MINIMIZER_MODE}",
                f"WN={cfg.MINIMIZER_WN}",
                f"WP={cfg.MINIMIZER_WP}",
                f"L={cfg.MINIMIZER_L}",
                f"MAX_INPUTS={cfg.MINIMIZER_MAX_INPUTS}",
                *flag("VERIFY", cfg.MINIMIZER_VERIFY),
                *flag("VERIFY_SPICE", cfg.MINIMIZER_VERIFY_SPICE),
            ], name="run-aion-minimizer-batch"),
            "batch minimization",
        )

        if not self.dry_run:
            self._report(ctx, reference)
        return "ok"

    # -----------------------------------------------------------------
    def _report(self, ctx: Context, reference) -> None:
        expected = set(_reference_cells(reference))
        produced = {p.name[: -len("_minimized.spice")]
                    for p in self.minimized_spice.glob("*_minimized.spice")}
        info(f"{len(produced)}/{len(expected)} cell(s) minimized")
        for name in sorted(produced):
            note(f"  {name}")
        missing = sorted(expected - produced)
        if missing:
            warn("no minimized netlist for: " + ", ".join(missing))
            ctx.note(f"missing minimized cells: {', '.join(missing)}")
        ctx.note(f"{len(produced)}/{len(expected)} cells minimized")
        if not produced:
            raise StepFailed("no cell was minimized", 2)


STEP = GateMinimizationStep()
