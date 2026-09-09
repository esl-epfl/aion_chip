#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Build one hand-designed PDK-extension cell:
#                             characterize, draw, characterize again.
#
#  The nine-step flow mines its cells out of a netlist.  This does not: a
#  PDK-extension cell is *designed*, by hand, because the PDK's own
#  implementation of that function is not the best one available.  The
#  transmission-gate mux2 is the case in point -- IHP builds mux2 the only
#  way complementary CMOS can (select inverter + AOI22 + output inverter,
#  12 devices), and a pair of transmission gates does the selection in 4.
#
#  So steps 1-3 have nothing to do here.  There is no netlist to mine, no
#  cover to select and no rewrite to prove: the cell exists first and the
#  synthesizer is told about it afterwards.  What is still needed is
#  everything that makes a cell trustworthy, and that is exactly steps 4
#  and 6 of the main flow:
#
#    characterize   aion_char builds the gold truth table from the PDK
#                   Liberty function of the reference netlist, assembles
#                   reference_<CELL> from the PDK transistor views, and
#                   checks gold, reference and our own SPICE against each
#                   other over every input vector -- a 3-way ngspice proof
#                   that the hand-designed netlist computes the function.
#    layout         build the GDS from the cell generator, then Magic and
#                   KLayout DRC and Magic + Netgen LVS against that same
#                   SPICE.  No model is invoked: the generator is a source
#                   file in this repository, written by hand.  If it is
#                   missing the stage scaffolds one and stops.
#    post-layout    Magic PEX, the abutted PDK-cell baseline, Liberty
#                   characterization from the extracted netlist, the
#                   area/delay comparison against that baseline, and the
#                   view export.  Every number here is measured from the
#                   drawn cell, not projected from a device count.
#
#  Nothing is published until the layout verifies, and the verdict is this
#  script's own `verify`, never a claim made anywhere else.
# ================================================================

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

AION_FLOW = PROJECT_ROOT / "aion_flow"
DOCKER_RUN = PROJECT_ROOT / "scripts" / "docker_run.sh"
CELLS_ROOT = PROJECT_ROOT / "implementation" / "pdk_extension"
VIEWS_ROOT = CELLS_ROOT / "views"
BUILD_ROOT = PROJECT_ROOT / "flow" / "pdk_extension"
LAYOUT_CELLS = AION_FLOW / "tools" / "aion_layout_claude" / "cells"

#: Where the container sees this project.  Same mapping scripts/flow/paths.py
#: uses, repeated here so this script stands alone.
CONTAINER_ROOT = "/foss/designs/aion_chip"

STAGES = ("characterize", "layout", "post-layout")


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------
_TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def head(text: str) -> None:
    print()
    print(_c(f"── {text} " + "─" * max(0, 58 - len(text)), "36;1"), flush=True)


def info(text: str) -> None:
    print(f"  {text}", flush=True)


def ok(text: str) -> None:
    print(f"  {_c('✓', '32;1')} {text}", flush=True)


def warn(text: str) -> None:
    print(f"  {_c('!', '33;1')} {text}", flush=True)


def fail(text: str) -> None:
    print(f"  {_c('✗', '31;1')} {text}", flush=True)


# ---------------------------------------------------------------------------
def container(path: Path) -> str:
    """A host path as the container sees it."""
    return str(path).replace(str(PROJECT_ROOT), CONTAINER_ROOT, 1)


@dataclass
class Cell:
    """The two files a PDK-extension cell is authored from."""

    name: str

    @property
    def dir(self) -> Path:
        return CELLS_ROOT / self.name

    @property
    def verilog(self) -> Path:
        """Gold gate-level reference: what the cell must compute."""
        return self.dir / f"{self.name}.v"

    @property
    def spice(self) -> Path:
        """The hand-designed transistor implementation."""
        return self.dir / f"{self.name}.spice"

    @property
    def build(self) -> Path:
        return BUILD_ROOT / self.name

    @property
    def generator(self) -> Path:
        return LAYOUT_CELLS / f"{self.name}.py"

    @property
    def reference(self) -> Path:
        """The abutted PDK netlist aion_char assembles, used as the baseline."""
        return (self.build / "steps" / "aion_char" / "tb" / "spice"
                / "reference_cells.spice")

    @property
    def reference_split(self) -> Path:
        return self.build / "raw_spice" / f"reference_{self.name}.spice"

    @property
    def views(self) -> Path:
        return VIEWS_ROOT / self.name

    def require_sources(self) -> None:
        missing = [p for p in (self.verilog, self.spice) if not p.exists()]
        if missing:
            raise SystemExit(
                f"error: {self.name} is missing "
                + ", ".join(str(p.relative_to(PROJECT_ROOT)) for p in missing)
                + f"\n       a PDK-extension cell is authored as {self.name}.v "
                  f"(the gold gate-level reference)\n       plus {self.name}.spice "
                  f"(the transistor implementation), both under\n       "
                  f"{CELLS_ROOT.relative_to(PROJECT_ROOT)}/{self.name}/")


# ---------------------------------------------------------------------------
class Runner:
    """Make targets, on the host or through the container."""

    def __init__(self, dry_run: bool = False, quiet: bool = False) -> None:
        self.dry_run = dry_run
        self.quiet = quiet
        self.log_dir = PROJECT_ROOT / "flow" / "logs" / "pdk_extension"

    def _exec(self, argv: list, name: str, env: dict | None = None) -> int:
        pretty = " ".join(argv)
        if self.dry_run:
            print(f"  $ {pretty}")
            return 0
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = self.log_dir / f"{name}.log"
        full = {**os.environ, **(env or {})}
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(f"# cwd: {AION_FLOW}\n# cmd: {pretty}\n\n")
            fh.flush()
            proc = subprocess.Popen(argv, cwd=str(AION_FLOW), env=full,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            assert proc.stdout is not None
            for line in proc.stdout:
                fh.write(line)
                if not self.quiet:
                    print("    " + line.rstrip(), flush=True)
            code = proc.wait()
        if code:
            fail(f"{name} exited {code} — see "
                 f"{log.relative_to(PROJECT_ROOT)}")
        return code

    def host(self, target: str, variables: list, name: str) -> int:
        """A make target that runs on the host (the layout tool self-dockers)."""
        return self._exec(["make", "--no-print-directory", target, *variables],
                          name)

    def contained(self, target: str, variables: list, name: str) -> int:
        """A make target that only exists inside iic-osic-tools."""
        argv = [str(DOCKER_RUN),
                " ".join(["make", "--no-print-directory", target, *variables])]
        return self._exec(argv, name, env={"HOST_PWD": str(PROJECT_ROOT)})


def container_running() -> bool:
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "iic-osic-tools_shell_uid_1000" in out


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------
def stage_characterize(cell: Cell, run: Runner) -> int:
    """Prove the hand-designed netlist computes the reference's function.

    The 3-way check is the point.  `gold` is the truth table aion_char reads
    out of the PDK Liberty, `reference` is the PDK cell's own transistors and
    `custom` is our netlist; ngspice runs all three over every input vector
    and they have to agree.  A 2-way pass would only say the PDK agrees with
    itself.
    """
    head(f"characterize  {cell.name}")
    common = [
        f"NETLIST={container(cell.verilog)}",
        f"BUILD_DIR={container(cell.build / 'steps')}",
        f"CUSTOM={container(cell.spice)}",
    ]
    for target, label in (("aion-char-generate", "testbench generation"),
                          ("aion-char-sv", "SystemVerilog testbenches"),
                          ("aion-char-spice", "ngspice testbenches")):
        if run.contained(target, common, f"{cell.name}.{target}"):
            fail(label + " failed")
            return 1
        ok(label) if not run.dry_run else info(f"would run: {label}")

    if run.dry_run:
        return 0
    if not cell.reference.exists():
        fail(f"no reference netlist at "
             f"{cell.reference.relative_to(PROJECT_ROOT)}")
        return 1

    # The layout baseline wants one file per cell; aion_char writes them all
    # into reference_cells.spice.  Splitting here keeps the layout stage from
    # having to know where characterization put things.
    cell.reference_split.parent.mkdir(parents=True, exist_ok=True)
    text = cell.reference.read_text()
    marker = f".subckt reference_{cell.name} "
    start = text.find(marker)
    if start < 0:
        fail(f"reference_{cell.name} not found in "
             f"{cell.reference.relative_to(PROJECT_ROOT)}")
        return 1
    end = text.find(".ends", start)
    cell.reference_split.write_text(text[start:end] + ".ends\n")
    ok(f"reference netlist -> "
       f"{cell.reference_split.relative_to(PROJECT_ROOT)}")
    return 0


def stage_layout(cell: Cell, run: Runner, jobs: int) -> int:
    """Build the GDS from the generator, then DRC and LVS it.

    No model is called.  The generator is a hand-written source file; if it
    does not exist yet this scaffolds one and stops, which is the same
    contract as the main flow's DRAW_MODE=manual.
    """
    head(f"layout  {cell.name}")
    common = [
        f"CELL={cell.name}",
        f"LAYOUT_NETLIST={cell.spice}",
        f"BUILD_DIR_LAYOUT={cell.build / 'layout'}",
        f"FINAL_DIR={cell.build / 'layout' / 'final'}",
    ]
    if not cell.generator.exists():
        info(f"no generator at {cell.generator.relative_to(PROJECT_ROOT)} — "
             f"scaffolding one")
        if run.host("aion-layout-scaffold", common, f"{cell.name}.scaffold"):
            return 1
        warn("the scaffold is deliberately incomplete: no contacts, no "
             "routing, no taps. Its first verify WILL fail.")
        info(f"finish it by hand, then re-run:  "
             f"scripts/pdk_cell.py {cell.name} --from layout")
        return 2

    info(f"generator: {cell.generator.relative_to(PROJECT_ROOT)}")
    code = run.host("aion-layout-verify", [*common, f"JOBS={jobs}"],
                    f"{cell.name}.verify")
    if run.dry_run:
        # A dry run proves nothing about the layout; saying PASS here would
        # be asserting a verdict no tool reached.
        info("dry run: DRC and LVS were not executed, so there is no verdict")
        return 0
    if code:
        fail("the layout does not verify yet — read the DRC/LVS output above")
        info(f"iterate on {cell.generator.relative_to(PROJECT_ROOT)}, then:  "
             f"scripts/pdk_cell.py {cell.name} --from layout")
        return 2
    ok("RESULT: PASS — DRC and LVS clean against the hand-designed netlist")
    return 0


def stage_post_layout(cell: Cell, run: Runner, corners: str, jobs: int) -> int:
    """PEX, the abutted PDK baseline, Liberty, the comparison and the export.

    Everything here is measured from the drawn cell.  The comparison against
    the abutted baseline is the one number that says whether the hand design
    was worth it, and it is computed from two Liberty files this stage wrote,
    not from a device count.
    """
    head(f"post-layout characterization  {cell.name}")
    if not cell.reference_split.exists() and not run.dry_run:
        fail(f"no baseline at {cell.reference_split.relative_to(PROJECT_ROOT)}"
             f" — run the characterize stage first")
        return 1
    code = run.host("aion-layout-flow", [
        f"CELL={cell.name}",
        f"LAYOUT_NETLIST={cell.spice}",
        f"BUILD_DIR_LAYOUT={cell.build / 'layout'}",
        f"FINAL_DIR={cell.views}",
        f"BASELINE={cell.reference_split}",
        f"CORNERS={corners}",
        f"JOBS={jobs}",
    ], f"{cell.name}.flow")
    if run.dry_run:
        info("dry run: nothing was extracted, characterized or published")
        return 0
    if code:
        fail("the mechanical chain failed")
        return 1
    ok(f"views published to {cell.views.relative_to(PROJECT_ROOT)}")
    _report_views(cell)
    return 0


def _report_views(cell: Cell) -> None:
    """What was published, and whether `make synth`/`make pnr` can use it."""
    required = (".lef", ".lib", ".gds")
    present = {p.suffix for p in cell.views.glob("*")} if cell.views.exists() else set()
    for suffix in required:
        (ok if suffix in present else fail)(f"{cell.name}{suffix}")
    compare = cell.build / "layout" / f"{cell.name}.compare.md"
    if compare.exists():
        for line in compare.read_text().splitlines():
            if line.startswith("COMPARE:") or "row sites" in line:
                info(line.strip())


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Characterize, draw and re-characterize one hand-designed "
                    "PDK-extension cell.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="stages: " + " -> ".join(STAGES) + "\n\n"
               "examples:\n"
               "  scripts/pdk_cell.py AION_mux2i_1\n"
               "  scripts/pdk_cell.py AION_mux2i_1 --from layout\n"
               "  scripts/pdk_cell.py AION_mux2i_1 --only characterize\n")
    parser.add_argument("cell", help="cell name, e.g. AION_mux2i_1")
    parser.add_argument("--from", dest="start", choices=STAGES,
                        default="characterize", help="first stage to run")
    parser.add_argument("--only", choices=STAGES,
                        help="run exactly one stage")
    parser.add_argument("--corners", default="typ",
                        help="Liberty corners to characterize (default: typ; "
                             "'all' publishes one .lib per corner, which "
                             "`make pnr` cannot group)")
    parser.add_argument("--jobs", type=int, default=8,
                        help="parallel ngspice jobs (default: 8)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print every command, run nothing")
    parser.add_argument("--quiet", action="store_true",
                        help="do not echo tool output; it still goes to "
                             "flow/logs/pdk_extension/")
    args = parser.parse_args(argv)

    cell = Cell(args.cell)
    cell.require_sources()
    run = Runner(dry_run=args.dry_run, quiet=args.quiet)

    if not args.dry_run and not container_running():
        raise SystemExit(
            "error: the IIC-OSIC-TOOLS container is not running — every stage "
            "here needs ngspice, magic, netgen or klayout.\n"
            "       docker start iic-osic-tools_shell_uid_1000")

    todo = [args.only] if args.only else list(STAGES[STAGES.index(args.start):])
    print(_c(f"AION PDK extension: {cell.name}", "1"))
    info(f"reference  {cell.verilog.relative_to(PROJECT_ROOT)}")
    info(f"netlist    {cell.spice.relative_to(PROJECT_ROOT)}")
    info(f"stages     {' -> '.join(todo)}")

    for stage in todo:
        if stage == "characterize":
            code = stage_characterize(cell, run)
        elif stage == "layout":
            code = stage_layout(cell, run, args.jobs)
        else:
            code = stage_post_layout(cell, run, args.corners, args.jobs)
        if code == 2:
            # "not drawn yet" is a stopping point, not a failure: the layout
            # is waiting on a human, and saying FAILED would be a lie.
            print()
            warn(f"stopped at {stage}: the cell is not drawn yet")
            return 0
        if code:
            print()
            fail(f"{stage} failed")
            return 1

    print()
    if args.dry_run:
        info(f"{cell.name}: dry run over {', '.join(todo)} — nothing ran")
    else:
        ok(f"{cell.name}: " + ", ".join(todo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
