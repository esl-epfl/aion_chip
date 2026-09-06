# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               4_characterization -- prove the cells compute
#                             what the netlist says they compute
#
#  aion_char builds, per AION module, a gold model from the PDK Liberty
#  functions and a `reference_<module>` transistor view assembled from the
#  PDK cells, then checks them against each other over every input vector --
#  once in SystemVerilog, once in ngspice.  reference_cells.spice is also the
#  input step 5 minimizes, which is why this step has to run before it even
#  if you only care about the minimizer.
# ================================================================

from __future__ import annotations

import re

from .. import paths
from ..config import Config
from ..style import info, note, ok, warn
from .base import Context, Step


class CharacterizationStep(Step):
    key = "4_characterization"
    title = "Characterization"
    summary = "exhaustive SV + SPICE testbenches for every AION cell"
    upstream = ("3_rewrite",)
    needs_container = True

    @property
    def tb_dir(self):
        return self.outdir / "steps" / "aion_char" / "tb"

    @property
    def reference_cells(self):
        return self.tb_dir / "spice" / "reference_cells.spice"

    def inputs(self, cfg: Config) -> list:
        return [paths.STEP_DIRS["3_rewrite"] / "nl" / "aion_cells.v"]

    def outputs(self, cfg: Config) -> list:
        return [self.reference_cells, self.tb_dir / "gold_functions.md"]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, run = ctx.cfg, ctx.runner
        cells = paths.STEP_DIRS["3_rewrite"] / "nl" / "aion_cells.v"
        self.require(cells)

        build = self.ensure("steps")
        # Container paths: every aion-char target runs inside iic-osic-tools,
        # where this project is /foss/designs/aion_chip.
        cells_c = paths.container(cells)
        build_c = paths.container(build)
        common = [f"BUILD_DIR={build_c}", f"NETLIST={cells_c}"]

        run.check(run.container("aion-char-generate", common,
                                name="aion-char-generate"),
                  "testbench generation")
        self.require(self.reference_cells)
        info(f"testbenches in {paths.rel_to_project(self.tb_dir)}")

        run.check(run.container("aion-char-sv", common, name="aion-char-sv"),
                  "SystemVerilog testbenches")
        ok("SystemVerilog testbenches passed")

        spice = run.container("aion-char-spice", common, name="aion-char-spice")
        if not spice.ok:
            for line in spice.output.splitlines():
                if line.startswith("[FAIL]") or line.startswith("[NO-RESULT]"):
                    warn(line.strip())
            ctx.note("SPICE testbenches failed")
            run.check(spice, "SPICE testbenches")
        ok("SPICE testbenches passed")

        self._report(ctx)
        return "ok"

    # -----------------------------------------------------------------
    def _report(self, ctx: Context) -> None:
        cells = _reference_cells(self.reference_cells)
        info(f"{len(cells)} cell(s) characterized")
        for name in cells:
            note(f"  {name}")
        ctx.note(f"{len(cells)} cells characterized")


_SUBCKT = re.compile(r"^\s*\.subckt\s+(\S+)", re.IGNORECASE | re.MULTILINE)


def _reference_cells(path) -> list:
    """The `reference_<CELL>` subckts, with the prefix stripped."""
    try:
        text = path.read_text()
    except OSError:
        return []
    return [name[len("reference_"):] if name.startswith("reference_") else name
            for name in _SUBCKT.findall(text)]


STEP = CharacterizationStep()
