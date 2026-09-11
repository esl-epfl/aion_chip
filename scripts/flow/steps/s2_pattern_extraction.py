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

import cell_registry

from .. import paths, pdk_ext
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


def cell_lib_vars(ctx: Context) -> list:
    """CELL_LIB for aion_opt, and it belongs beside the mining knobs.

    aion_opt's technology dictionary is not just a table of areas: it is the
    list of cell types it will keep at all.  A netlist instance whose type is
    missing from it is dropped on load, so the dictionary steps 2 and 3 read
    has to describe the same library synthesis mapped against -- the PDK's
    plus implementation/pdk_extension/ when PDK_EXT is on.

    Both steps call this for the same reason they share `mining_vars`: a miner
    and a rewriter that disagree about what the technology holds do not mine
    the same cover, and here they would not even see the same netlist.
    """
    return optional("CELL_LIB", pdk_ext.views(ctx).tech_dict)


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
                *cell_lib_vars(ctx),
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
        if not self.dry_run:
            self._stabilize_names(ctx)
        self._report(ctx)
        return "ok"

    # -----------------------------------------------------------------
    def _stabilize_names(self, ctx: Context) -> None:
        """Give a mined pattern back the name its layout is filed under.

        aion_opt names a module after its *rank* in this run's cover, so the
        same pattern comes back under a different number whenever the cover
        moves -- a different MIN_SELECTED, an edit to the RTL.  Step 6 decides
        whether a cell still needs drawing by looking for
        `<name>.py` on disk, so a renumbered pattern is redrawn from scratch
        and the number it vacated can be picked up by a different pattern
        that then inherits a layout it was never drawn for.

        implementation/cells/registry.json remembers which canonical key each
        drawn cell belongs to; see scripts/cell_registry.py.
        """
        entries = cell_registry.load()
        if not entries:
            return

        # One plan, derived from the full library and applied to both files:
        # an unregistered pattern pushed off a reserved name must land on the
        # same free index in each, because REWRITE_CELLS may name either.
        modules = cell_registry.split_modules(
            self.cells.read_text(encoding="utf-8"))
        renames = cell_registry.plan(modules, entries)
        if not renames:
            return
        for library in (self.cells, self.elite_cells):
            cell_registry.apply_to_library(library, entries, renames=renames)
        _restamp_selection(self.selection, entries)
        _restamp_report(self.pattern_report, entries)

        info(f"{len(renames)} cell(s) renamed to match "
             f"{paths.rel_to_project(cell_registry.REGISTRY)}")
        for old, new in sorted(renames.items()):
            note(f"  {old} -> {new}")
        ctx.note(f"{len(renames)} cells renamed from the registry")

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


def _restamp_selection(path, entries: dict) -> None:
    """Keep the selection cache's `module_names` telling the same story.

    Nothing reads it -- `_cover_from_selection` rebuilds occurrences and
    savings and never looks at the names -- but a cache that disagrees with
    the library beside it is exactly the kind of thing this whole mechanism
    exists to stop someone chasing.  Only the fingerprinted fields matter for
    a cache hit, and this touches none of them.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    names = data.get("module_names")
    if not isinstance(names, dict):
        return
    data["module_names"] = {k: entries.get(k, v) for k, v in names.items()}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _restamp_report(path, entries: dict) -> None:
    """Rename the patterns in the report aion_opt wrote before the rename.

    This one is not cosmetic in the same way the selection cache is: the
    ranking table below, and anyone reading flow/2_pattern_extraction/report/
    afterwards, would otherwise be told to draw a cell under a name no file
    in the library uses.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for section in ("patterns_found", "patterns_selected"):
        for entry in data.get(section, []):
            if not isinstance(entry, dict):
                continue
            name = entries.get(entry.get("pattern_key"))
            if name is not None:
                entry["module_name"] = name
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _count_modules(path) -> int:
    try:
        text = path.read_text()
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("module "))


STEP = PatternExtractionStep()
