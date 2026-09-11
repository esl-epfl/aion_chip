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
#                   SPICE.  The generator -- aion_layout_claude/cells/<CELL>.py
#                   -- is the one artifact of this script that is authored
#                   rather than computed, so, exactly like step 6 of the main
#                   flow, this stage either waits for a human to write it or
#                   drives an agent until it verifies:
#
#                     --draw auto (default)  scaffold it if it does not exist,
#                                            then loop verify -> evidence ->
#                                            `claude -p` until RESULT: PASS or
#                                            --max-iters turns are spent.
#                     --draw manual          scaffold it, print the loop, stop.
#
#                   The agent only ever writes the generator.  Every graded
#                   step runs here, on the host, and what the agent says it
#                   did is never the verdict -- the verdict is the RESULT:
#                   line `aion-layout-verify` prints.
#    post-layout    Magic PEX, the abutted PDK-cell baseline, Liberty
#                   characterization from the extracted netlist, the
#                   area/delay comparison against that baseline, and the
#                   view export.  Every number here is measured from the
#                   drawn cell, not projected from a device count.  The views
#                   land in implementation/pdk_extension/<CELL>/, beside the
#                   sources they were drawn from -- one directory per cell,
#                   exactly as step 6 publishes the mined ones.
#
#                   --driver-cell decides what those numbers mean.  The
#                   default stimulus is an ideal linear ramp, i.e. a
#                   zero-impedance driver, which is a fair idealisation for a
#                   cell whose inputs are transistor gates and is not one for
#                   any cell here: a transmission gate's input is a
#                   source/drain, so the driver stays in series with the
#                   channel for the whole transition.  Pass a real cell and
#                   both sides of the comparison are measured against it.
#
#  Nothing is published until the layout verifies, and the verdict is this
#  script's own `verify`, never a claim made anywhere else.
# ================================================================

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

#: One `claude -p` turn, narrated while it runs.  Shared with step 6 of the
#: main flow rather than reimplemented: the streaming, the teed log, the
#: watchdog and the "what did it actually say" parsing are the same problem
#: here, and a second copy of them would be a second copy to keep honest.
#: The host-side LEF sanity check `make pnr` would otherwise discover an hour
#: into detailed routing.  Same one step 6 publishes through.
from collect_cells import check_lef

from flow import agent

AION_FLOW = PROJECT_ROOT / "aion_flow"
DOCKER_RUN = PROJECT_ROOT / "scripts" / "docker_run.sh"
CELLS_ROOT = PROJECT_ROOT / "implementation" / "pdk_extension"
BUILD_ROOT = PROJECT_ROOT / "flow" / "pdk_extension"
LAYOUT_TOOL = AION_FLOW / "tools" / "aion_layout_claude"
LAYOUT_CELLS = LAYOUT_TOOL / "cells"

#: Where the container sees this project.  Same mapping scripts/flow/paths.py
#: uses, repeated here so this script stands alone.
CONTAINER_ROOT = "/foss/designs/aion_chip"

#: ...and the name the aion_flow submodule answers to in there.  The layout
#: tool opens the container itself and defaults its mount to
#: /foss/designs/aion_flow, which on this machine is a DIFFERENT, standalone
#: checkout: every path this script hands it is under aion_chip and simply
#: does not exist there, which is why DRC and LVS "could not run".
CONTAINER_AION_FLOW = f"{CONTAINER_ROOT}/aion_flow"

STAGES = ("characterize", "layout", "post-layout")
DRAW_MODES = ("auto", "manual")

#: Parasitic extraction mode for every cell in this directory: 2, C-coupled.
#:
#: `aion_layout`'s own default is 3, full RC, and it is right there -- wire
#: resistance inside a hand-drawn cell is exactly what a small layout gets
#: wrong.  A PDK-extension cell is graded differently, and two things follow.
#:
#: Mode 3 runs Magic's `extresist`, which splits a resistive net into renamed
#: segments and can leave the `.subckt` port bound to none of them: a floating
#: rail that LVS cannot see, because LVS extracts without resistance.  Every
#: cell here is measured against an *abutted PDK baseline*, and an abutted row
#: of shipped cells is precisely the geometry that trips it -- `AION_mux2i_1`'s
#: 13-site baseline gets the binding and `AION_mux2i_2`'s 14-site one does not,
#: from the same rail network.  `run_pex` refuses such a netlist, so on mode 3
#: whether a cell can be characterized at all depends on how Magic happened to
#: cut a rail.
#:
#: And rail resistance is the parasitic these cells least want modelled.  The
#: characterization deck ties the rail port to ideal ground; in silicon the rail
#: is strapped by the power grid along its whole length and abuts its neighbours
#: on both sides.  Feeding ground in at one label point through hundreds of ohms
#: of rail is less physical than no rail resistance at all, which is why vendor
#: libraries characterize with ideal rails.
#:
#: What mode 2 gives up is signal-net wire resistance, which is real.  Pass
#: `--pex-mode 3` to get it back on a cell whose baseline extracts cleanly --
#: but do it for the whole library or not at all: two cells extracted at
#: different modes are not comparable, and they land in one merged Liberty.
PEX_MODE_DEFAULT = 2

#: What `make pnr` demands of every published cell, and what it merely likes
#: to have -- the same two lists step 6 of the main flow publishes by.
REQUIRED_VIEWS = (".lef", ".lib", ".gds")
RECOMMENDED_VIEWS = (".v", ".spice", ".cdl")

#: Everything publishing owns inside the cell's directory.  Anything here that
#: is not an authored source is a leftover from an earlier run and is removed
#: before the new views land, so a view the exporter stopped writing cannot
#: sit there looking current.
PUBLISHED_SUFFIXES = REQUIRED_VIEWS + RECOMMENDED_VIEWS + (".png",)


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
        return (
            self.build
            / "steps"
            / "aion_char"
            / "tb"
            / "spice"
            / "reference_cells.spice"
        )

    @property
    def reference_split(self) -> Path:
        return self.build / "raw_spice" / f"reference_{self.name}.spice"

    @property
    def provisional_lib(self) -> Path:
        """The floorplan estimate the mapper uses until the cell is drawn."""
        return self.dir / f"{self.name}.provisional.lib"

    @property
    def layout_verilog(self) -> Path:
        """The exporter's generated model, published beside the gold one.

        `aion_layout` writes a `<CELL>.v` of its own, solved from the netlist,
        and that name is already taken here by the hand-written reference the
        netlist was proved against.  The generated model is still worth
        publishing -- it is what a gate-level simulation of the drawn cell
        runs -- so it is published under a name that cannot overwrite it.
        """
        return self.dir / f"{self.name}.layout.v"

    @property
    def authored(self) -> set:
        """The files that belong to the author, and are never published over."""
        return {
            self.verilog.name,
            self.spice.name,
            self.provisional_lib.name,
            self.floorplan.name,
        }

    @property
    def floorplan(self) -> Path:
        """The placement/routing plan, decided by hand before any drawing.

        The `aion-layout` skill opens with "Floorplan first -- do not start
        placing rectangles", and an agent handed no floorplan does that whole
        derivation in extended thinking: the first auto run spent three
        64k-token thinking blocks on it and never reached an edit.  A
        PDK-extension cell is designed by hand, so its floorplan is authored
        by hand too, and the drawer is told to implement it rather than
        rediscover it.
        """
        return self.dir / "FLOORPLAN.md"

    @property
    def evidence(self) -> Path:
        """What the last verify found, as the drawing agent's briefing."""
        return self.build / "layout" / f"{self.name}.evidence.md"

    def require_sources(self) -> None:
        missing = [p for p in (self.verilog, self.spice) if not p.exists()]
        if missing:
            raise SystemExit(
                f"error: {self.name} is missing "
                + ", ".join(str(p.relative_to(PROJECT_ROOT)) for p in missing)
                + f"\n       a PDK-extension cell is authored as {self.name}.v "
                f"(the gold gate-level reference)\n       plus {self.name}.spice "
                f"(the transistor implementation), both under\n       "
                f"{CELLS_ROOT.relative_to(PROJECT_ROOT)}/{self.name}/"
            )


@dataclass(frozen=True)
class Driver:
    """The cell that drives the pin under test during characterization.

    aion_char's default stimulus is an ideal linear ramp, which is a
    zero-impedance driver.  For a static CMOS cell that is a fair idealisation:
    the input is a transistor gate, the capacitance isolates, and the driver's
    job ends with the waveform it produced.  For every cell in
    `implementation/pdk_extension/` it is not -- their inputs reach a pass
    gate's source/drain, so the driver stays in series with the channel for the
    whole transition, and a ramp is the most optimistic driver there is.  Each
    of those cells' SPICE headers says to characterize with a real one; this is
    the flag that does it.

    Only the post-layout stage uses it.  The characterize stage is a functional
    3-way proof over input vectors -- gold, PDK reference and our netlist -- and
    has no timing in it to be flattered.

    The driver must be NON-INVERTING, and the defaults are a buffer's pins.
    aion_char feeds it a ramp that always rises first and derives the expected
    output edge from the cell's own unateness; nothing accounts for a driver
    that flips the pin, so an inverting one inverts every expected level and the
    run dies as "the output still had not settled" with the output pinned at the
    opposite rail -- even at 1 fF, where nothing is too slow to settle.
    """

    cell: str
    in_pin: str = "A"
    out_pin: str = "X"

    @property
    def make_vars(self) -> tuple:
        """The variables `aion-layout-flow` forwards to the layout tool."""
        return (
            f"DRIVER={self.cell}",
            f"DRIVER_IN={self.in_pin}",
            f"DRIVER_OUT={self.out_pin}",
        )

    def describe(self) -> str:
        return f"{self.cell} ({self.in_pin} -> {self.out_pin})"


@dataclass
class Draw:
    """How the layout stage gets its generator written."""

    mode: str = "auto"
    max_iters: int = 12
    timeout: int = 1800  # seconds per turn; one turn is DRC + LVS + edits
    model: str | None = "claude-opus-5"
    effort: str | None = "max"
    stream: bool = True
    claude: str = "claude"  # resolved on PATH before the stage starts


# ---------------------------------------------------------------------------
@dataclass
class Result:
    """What one make invocation returned, and what it said."""

    returncode: int
    output: str
    log: Path | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def verdict(self, prefix: str) -> str | None:
        """The aion_layout convention: exactly one line starts at column 0 and
        it is the verdict (`RESULT: PASS`, `COMPARE: WIN`).  Everything else is
        indented, so an anchored match is unambiguous -- and it has to be one,
        because GNU make reports its own failure as exit 2 whatever the recipe
        returned, which makes FAIL and ERROR indistinguishable from the status.
        """
        for line in self.output.splitlines():
            if line.startswith(prefix):
                return line.strip()
        return None


class Runner:
    """Make targets, on the host or through the container."""

    def __init__(self, dry_run: bool = False, quiet: bool = False) -> None:
        self.dry_run = dry_run
        self.quiet = quiet
        self.log_dir = PROJECT_ROOT / "flow" / "logs" / "pdk_extension"

    def _exec(self, argv: list, name: str, env: dict | None = None) -> Result:
        pretty = " ".join(argv)
        if self.dry_run:
            print(f"  $ {pretty}")
            return Result(0, "")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = self.log_dir / f"{name}.log"
        full = {**os.environ, **(env or {})}
        lines = []
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(f"# cwd: {AION_FLOW}\n# cmd: {pretty}\n\n")
            fh.flush()
            proc = subprocess.Popen(
                argv,
                cwd=str(AION_FLOW),
                env=full,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                fh.write(line)
                lines.append(line)
                if not self.quiet:
                    print("    " + line.rstrip(), flush=True)
            code = proc.wait()
        if code:
            fail(f"{name} exited {code} — see {log.relative_to(PROJECT_ROOT)}")
        return Result(code, "".join(lines), log)

    def host(self, target: str, variables: list, name: str) -> Result:
        """A make target that runs on the host (the layout tool self-dockers).

        AION_CONTAINER_MOUNT is not optional: the layout tool opens the
        container per step and would otherwise run every containerised
        half -- DRC, LVS, PEX, characterization, the LEF export -- inside the
        standalone aion_flow checkout, where none of this cell's files exist.
        """
        return self._exec(
            ["make", "--no-print-directory", target, *variables],
            name,
            env={"AION_CONTAINER_MOUNT": CONTAINER_AION_FLOW},
        )

    def contained(self, target: str, variables: list, name: str) -> Result:
        """A make target that only exists inside iic-osic-tools."""
        argv = [
            str(DOCKER_RUN),
            " ".join(["make", "--no-print-directory", target, *variables]),
        ]
        return self._exec(argv, name, env={"HOST_PWD": str(PROJECT_ROOT)})


def container_running() -> bool:
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
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
    for target, label in (
        ("aion-char-generate", "testbench generation"),
        ("aion-char-sv", "SystemVerilog testbenches"),
        ("aion-char-spice", "ngspice testbenches"),
    ):
        if not run.contained(target, common, f"{cell.name}.{target}").ok:
            fail(label + " failed")
            return 1
        ok(label) if not run.dry_run else info(f"would run: {label}")

    if run.dry_run:
        return 0
    if not cell.reference.exists():
        fail(f"no reference netlist at {cell.reference.relative_to(PROJECT_ROOT)}")
        return 1

    # The layout baseline wants one file per cell; aion_char writes them all
    # into reference_cells.spice.  Splitting here keeps the layout stage from
    # having to know where characterization put things.
    cell.reference_split.parent.mkdir(parents=True, exist_ok=True)
    text = cell.reference.read_text()
    marker = f".subckt reference_{cell.name} "
    start = text.find(marker)
    if start < 0:
        fail(
            f"reference_{cell.name} not found in "
            f"{cell.reference.relative_to(PROJECT_ROOT)}"
        )
        return 1
    end = text.find(".ends", start)
    cell.reference_split.write_text(text[start:end] + ".ends\n")
    ok(f"reference netlist -> {cell.reference_split.relative_to(PROJECT_ROOT)}")
    return 0


def stage_layout(cell: Cell, run: Runner, jobs: int, draw: Draw) -> int:
    """Build the GDS from the generator, then DRC and LVS it.

    The generator is the one authored artifact in this script, and this is
    where it comes from.  `--draw manual` scaffolds it and stops, the way the
    main flow's DRAW_MODE=manual does.  `--draw auto`, the default, drives the
    same agent step 6 does until the layout verifies -- the cell is
    hand-designed, its *drawing* need not be.

    Either way the verdict is the RESULT: line of a host-side verify, never
    the agent's own account of what it did.
    """
    head(f"layout  {cell.name}")
    common = [
        f"CELL={cell.name}",
        f"LAYOUT_NETLIST={cell.spice}",
        f"BUILD_DIR_LAYOUT={cell.build / 'layout'}",
        f"FINAL_DIR={cell.build / 'layout' / 'final'}",
        f"JOBS={jobs}",
    ]
    if not cell.generator.exists():
        info(
            f"no generator at {cell.generator.relative_to(PROJECT_ROOT)} — "
            f"scaffolding one"
        )
        if not run.host("aion-layout-scaffold", common, f"{cell.name}.scaffold").ok:
            return 1
        warn(
            "the scaffold is deliberately incomplete: no contacts, no "
            "routing, no taps. Its first verify WILL fail."
        )
        if draw.mode != "auto":
            info(
                f"finish it by hand, then re-run:  "
                f"scripts/pdk_cell.py {cell.name} --from layout"
            )
            return 2

    info(f"generator: {cell.generator.relative_to(PROJECT_ROOT)}")

    if run.dry_run:
        if draw.mode == "auto":
            info(
                f"would run up to {draw.max_iters} agent turn(s) against "
                f"{cell.generator.relative_to(PROJECT_ROOT)}"
            )
        run.host("aion-layout-verify", common, f"{cell.name}.verify")
        # A dry run proves nothing about the layout; saying PASS here would
        # be asserting a verdict no tool reached.
        info("dry run: DRC and LVS were not executed, so there is no verdict")
        return 0

    if draw.mode == "auto":
        verdict = _draw_auto(cell, run, common, draw)
    else:
        verdict = _verify(cell, run, common).verdict("RESULT:")

    if verdict != "RESULT: PASS":
        fail(
            f"the layout does not verify yet ({verdict or 'no verdict'}) — "
            f"read the DRC/LVS output above"
        )
        info(
            f"iterate on {cell.generator.relative_to(PROJECT_ROOT)}, then:  "
            f"scripts/pdk_cell.py {cell.name} --from layout"
        )
        return 2
    ok("RESULT: PASS — DRC and LVS clean against the hand-designed netlist")
    return 0


def _verify(cell: Cell, run: Runner, common: list, turn: int | None = None) -> Result:
    """Build + DRC + LVS.  One log per turn, so an auto run keeps its history."""
    return run.host("aion-layout-verify", common, _log(cell, "verify", turn))


def _evidence(cell: Cell, run: Runner, common: list, turn: int) -> str:
    """The packet the last verify wrote: what failed, and where."""
    run.host("aion-layout-evidence", common, _log(cell, "evidence", turn))
    try:
        return cell.evidence.read_text()
    except OSError:
        return ""


def _log(cell: Cell, target: str, turn: int | None) -> str:
    return (
        f"{cell.name}.{target}" if turn is None else f"{cell.name}.{turn:02d}.{target}"
    )


def _fingerprint(path: Path) -> str | None:
    """Content hash of the generator, or None when it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _draw_auto(cell: Cell, run: Runner, common: list, draw: Draw) -> str | None:
    """verify -> evidence -> agent turn, until it passes or turns run out.

    Every graded step is run here.  The agent only ever writes the generator,
    and the file on disk is the only thing that counts: a turn that timed out
    after writing a complete generator has done the work, and one that exited
    cleanly without touching the file has not.  Twice in a row untouched means
    the agent is not running at all -- no credentials, no quota, a refusal --
    and looping on that burns half an hour per turn to say "did not converge"
    about something that never started.
    """
    verdict = None
    idle = 0
    for turn in range(1, draw.max_iters + 1):
        verdict = _verify(cell, run, common, turn).verdict("RESULT:")
        if verdict == "RESULT: PASS":
            ok(f"verified after {turn - 1} agent turn(s)")
            return verdict

        info(f"turn {turn}/{draw.max_iters}  ({verdict or 'no verdict'})")
        evidence = _evidence(cell, run, common, turn)
        before = _fingerprint(cell.generator)
        report = _agent_turn(cell, run, draw, evidence, turn)

        if _fingerprint(cell.generator) == before:
            idle += 1
            warn(f"turn {turn} left the generator unchanged")
            if report:
                info(f"agent said: {report}")
            if idle >= 2:
                fail(
                    "the drawing agent made no change twice in a row — it is "
                    "not running, not that the layout is hard"
                )
                info(
                    f"check {run.log_dir.relative_to(PROJECT_ROOT)}/"
                    f"{cell.name}.agent.*.log"
                )
                return verdict
        else:
            idle = 0

    # One last graded check after the final turn.
    verdict = _verify(cell, run, common, draw.max_iters + 1).verdict("RESULT:")
    if verdict != "RESULT: PASS":
        warn(f"still {verdict or 'unverified'} after {draw.max_iters} turn(s)")
    return verdict


def _floorplan_section(cell: Cell) -> list:
    """The hand-authored plan, verbatim, or an instruction to derive one.

    Verbatim on purpose: it carries coordinates, and a summary of a coordinate
    is a wrong coordinate.
    """
    try:
        text = cell.floorplan.read_text()
    except OSError:
        return [
            "There is no FLOORPLAN.md for this cell. Derive the floorplan the "
            "skill's way -- shared diffusion, columns, width, then draw -- and "
            "write it into the generator's module docstring so the next turn "
            "does not derive it again.",
            "",
        ]
    return [
        f"=== THE FLOORPLAN — {cell.floorplan}, authored by hand ===",
        "The STRUCTURE in it -- device pairing, diffusion segments, node and "
        "gate coordinates, cell width, taps, pins -- is a DECISION. Implement "
        "it; do not re-derive it and do not silently depart from it. If you "
        "believe one of those numbers is wrong, say so in your final message "
        "and stop: changing it is the author's call, not yours.",
        "The ROUTING is deliberately NOT decided, and the document says which "
        "section is which. Do not try to settle it analytically before you "
        "draw -- that is a trap this cell has already cost two agent turns. "
        "Draw the structure, run verify, and let the evidence packet's "
        "cross-net overlap table drive the routing. A broken GDS on disk beats "
        "a plan that is not on disk.",
        "",
        text[:40000],
        "",
    ]


def _agent_turn(cell: Cell, run: Runner, draw: Draw, evidence: str, turn: int) -> str:
    """One `claude -p`, narrated while it runs.  Grades nothing."""

    def cmd(target: str) -> str:
        return (
            f"make -C {LAYOUT_TOOL} {target} CELL={cell.name} "
            f"NETLIST={cell.spice} BUILD_DIR={cell.build / 'layout'}"
        )

    prompt = "\n".join(
        [
            f"Use the aion-layout skill. Draw the standard-cell layout for "
            f"{cell.name}.",
            "",
            "The ONLY file you may create or edit is:",
            f"    {cell.generator}",
            "",
            f"Its target netlist is {cell.spice}.",
            "That netlist is a HAND-DESIGNED PDK-extension cell: it is the source "
            "of truth for both the connectivity and the device widths that LVS "
            "grades against, it has already been proven in SPICE against the PDK "
            "reference, and you must NEVER modify it.",
            "Never hand-edit a GDS, a report, or anything under the build "
            "directory — they are evidence.",
            "",
            "Check your work, and read what to fix, with:",
            f"    {cmd('verify')}",
            f"    {cmd('evidence')}",
            "Run them EARLY and often. They take about three minutes and they "
            "answer questions about geometry that reasoning answers slowly and "
            "worse. Write an incomplete generator and verify it rather than "
            "thinking a complete one through: your first verify should happen "
            "before the cell is finished, not after.",
            "Work until `verify` prints `RESULT: PASS` at the start of a line.",
            "Exactly one line of that output starts at column 0 and it is the "
            "verdict; everything else is indented. Do not read make's exit status "
            "as the verdict — make reports its own failure as 2 whatever the tool "
            "returned.",
            "",
            f"This is turn {turn} of {draw.max_iters}. Stop as soon as the "
            "generator is written and verifying; do not report success you have "
            "not seen a RESULT: PASS for.",
            "",
            *_floorplan_section(cell),
            "=== evidence from the last verify ===",
            evidence[:60000] if evidence else "(no evidence packet yet)",
        ]
    )

    argv = [
        draw.claude,
        "-p",
        prompt,
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Read,Edit,Write,Glob,Grep,Bash(make:*),Bash(python3:*),Bash(sed:*),Bash(cat:*)",
        "--add-dir",
        str(cell.build / "layout"),
    ]
    if draw.model:
        argv += ["--model", draw.model]
    if draw.effort:
        argv += ["--effort", draw.effort]

    env = dict(os.environ)
    # Without this the agent's own `make verify` would drive the container
    # against the other aion_flow checkout, and every DRC and LVS it ran
    # would come back "could not run".
    env["AION_CONTAINER_MOUNT"] = CONTAINER_AION_FLOW

    log = run.log_dir / f"{cell.name}.agent.{turn}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    info(_c(f"▶ claude -p  (turn {turn}/{draw.max_iters}, {cell.name})", "37"))
    info(f"live log {log.relative_to(PROJECT_ROOT)}")

    result = agent.run(
        argv,
        cwd=AION_FLOW,
        env=env,
        log=log,
        timeout=draw.timeout,
        stream=draw.stream,
        echo=not run.quiet,
    )
    info(
        f"agent exit {result.returncode} in {result.duration_s:.0f}s   "
        f"log {log.relative_to(PROJECT_ROOT)}"
    )
    return result.report or (
        f"exit {result.returncode}, no output" if not result.ok else ""
    )


def stage_post_layout(
    cell: Cell,
    run: Runner,
    corners: str,
    jobs: int,
    driver: Driver | None = None,
    pex_mode: int = PEX_MODE_DEFAULT,
) -> int:
    """PEX, the abutted PDK baseline, Liberty, the comparison and the export.

    Everything here is measured from the drawn cell.  The comparison against
    the abutted baseline is the one number that says whether the hand design
    was worth it, and it is computed from two Liberty files this stage wrote,
    not from a device count.

    `driver` is what those two Liberty files were measured *with*.  Left unset
    it is aion_char's ideal linear ramp, which is right for static CMOS and
    wrong for every cell in this directory: a ramp is a zero-impedance driver,
    and a transmission gate's input is a source/drain, so the driver stays in
    series with the channel for the whole transition and the ramp flatters the
    cell by exactly the effect NLDM cannot express.  `aion_layout flow` applies
    it to the candidate and the baseline both -- comparing one stimulus against
    the other would be a rigged comparison.
    """
    head(f"post-layout characterization  {cell.name}")
    if not cell.reference_split.exists() and not run.dry_run:
        fail(
            f"no baseline at {cell.reference_split.relative_to(PROJECT_ROOT)}"
            f" — run the characterize stage first"
        )
        return 1
    if driver is None:
        warn(
            "no --driver-cell: the Liberty will be measured against an ideal "
            "ramp, which over-states any cell with a pass gate on an input path"
        )
    else:
        info(f"stimulus   {driver.describe()} (candidate and baseline both)")
    result = run.host(
        "aion-layout-flow",
        [
            f"CELL={cell.name}",
            f"LAYOUT_NETLIST={cell.spice}",
            f"BUILD_DIR_LAYOUT={cell.build / 'layout'}",
            f"FINAL_DIR={cell.build / 'layout' / 'final'}",
            f"BASELINE={cell.reference_split}",
            f"CORNERS={corners}",
            f"JOBS={jobs}",
            f"PEX_MODE={pex_mode}",
            *(driver.make_vars if driver else ()),
        ],
        f"{cell.name}.flow",
    )
    if run.dry_run:
        info("dry run: nothing was extracted, characterized or published")
        return 0
    if not result.ok:
        fail("the mechanical chain failed")
        return 1
    verdict = result.verdict("RESULT:")
    if verdict and verdict != "RESULT: PASS":
        fail(f"the mechanical chain re-graded the cell {verdict}")
        return 1
    if not _publish_views(cell):
        return 1
    _report_views(cell)
    return 0


def _publish_views(cell: Cell) -> bool:
    """Copy the exported views into `implementation/pdk_extension/<CELL>/`.

    The same shape step 6 of the main flow publishes in -- one directory per
    cell, every view beside every other -- except that here the directory is
    not empty: it already holds the files the cell was *authored* from.  So
    two of the exported views cannot be written under the names the exporter
    gave them.

      `<CELL>.v`      is a model `aion_layout` generates from the netlist, and
                      `<CELL>.v` here is the hand-written gold reference that
                      netlist was proved against.  Published as
                      `<CELL>.layout.v`.
      `<CELL>.spice`  is the exporter's copy of the hand design itself.  It is
                      the same file that went in, so it is not published at
                      all; if it ever differs it is published beside the
                      original, under `.layout.spice`, and said out loud --
                      the netlist is the design and LVS grades the drawing
                      against it.

    `aion_layout flow` writes the views to its own `BUILD_DIR/final` and does
    not take a destination, which is why they are copied out here.  It matters
    because `merge_lib.py` reads the *measured* Liberty from
    `<CELL>/<CELL>.lib`, and until it is there the mapper keeps choosing this
    cell on the provisional area estimate.

    A `.rejected` file is the exporter refusing to publish, which is a finding
    about the cell; nothing is copied on top of a refusal.
    """
    final = cell.build / "layout" / "final"
    if not final.is_dir():
        fail(f"nothing exported to {final.relative_to(PROJECT_ROOT)}")
        return False
    rejected = sorted(final.glob("*.rejected"))
    if rejected:
        fail("the exporter REFUSED to publish — " + ", ".join(p.name for p in rejected))
        info("that is a real finding about the cell, not something to work around")
        return False

    written = [p for p in sorted(final.iterdir()) if p.is_file()]
    views = {p.suffix.lower(): p for p in written}
    missing = [ext for ext in REQUIRED_VIEWS if ext not in views]
    if missing:
        fail(f"cannot publish, missing {', '.join(missing)}")
        return False

    libs = [p for p in written if p.suffix.lower() == ".lib"]
    if len(libs) > 1:
        fail(
            f"{len(libs)} Liberty files exported. `merge_lib.py` and "
            "`make pnr` both look for exactly <CELL>.lib, so per-corner "
            "libs cannot be published. Re-run with --corners typ."
        )
        return False

    # The exporter grades the abstract too, and a cell that fails there
    # arrives as a .rejected above.  This re-grades it on the host, because
    # publishing is the last moment a bad abstract is cheap: past here it
    # survives placement and kills detailed routing an hour into PnR.
    problems = check_lef(str(views[".lef"]))
    if problems:
        fail("cannot publish, the LEF would fail PnR")
        for problem in problems:
            info(f"  {problem}")
        return False

    cell.dir.mkdir(parents=True, exist_ok=True)
    _clear_published(cell)

    copied = []
    for path in written:
        target = _published_name(cell, path)
        if target is None:
            continue
        shutil.copy2(path, cell.dir / target)
        copied.append(target)

    png = _render_png(cell, views[".gds"])
    if png is not None:
        copied.append(png.name)

    ok(f"published {len(copied)} view(s) to {cell.dir.relative_to(PROJECT_ROOT)}")
    info("  " + "  ".join(copied))
    return True


def _published_name(cell: Cell, path: Path) -> str | None:
    """What an exported view is called once it sits next to the sources.

    None means "do not publish this one": either it is not a view `make pnr`
    or `merge_lib.py` has any use for, or it is the exporter handing back a
    file the author owns.
    """
    suffix = path.suffix.lower()
    if suffix not in REQUIRED_VIEWS + RECOMMENDED_VIEWS:
        return None
    if suffix == ".v":
        return cell.layout_verilog.name
    if suffix == ".spice":
        if path.read_bytes() == cell.spice.read_bytes():
            # The exporter's copy of the hand design.  Publishing it would
            # write the file over itself; the source IS the view.
            return None
        warn(
            f"the exported {path.name} is not the netlist that went in — "
            f"publishing it as {cell.name}.layout.spice, leaving the hand "
            f"design alone"
        )
        return f"{cell.name}.layout.spice"
    return path.name


def _clear_published(cell: Cell) -> None:
    """Remove what an earlier publish left, and nothing the author wrote."""
    for path in sorted(cell.dir.iterdir()):
        if not path.is_file() or path.name in cell.authored:
            continue
        if path.suffix.lower() in PUBLISHED_SUFFIXES:
            path.unlink()


def _render_png(cell: Cell, gds: Path) -> Path | None:
    """Draw the published cell to a PNG beside its views.

    Documentation, not a view -- and never allowed to fail a publish: the
    views are the deliverable and the picture is not, so a host without
    klayout or Pillow publishes the cell and says why there is no image.
    """
    try:
        from gds_to_image import render_gds
    except ImportError as exc:
        warn(f"no layout PNG, {exc}")
        return None

    png = cell.dir / f"{cell.name}.png"
    try:
        render_gds(str(gds), str(png), title=cell.name)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        warn(f"could not render {png.name} — {exc}")
        return None
    return png


def _report_views(cell: Cell) -> None:
    """What was published, and whether `make synth`/`make pnr` can use it."""
    present = {p.suffix.lower() for p in cell.dir.glob("*") if p.is_file()}
    for suffix in REQUIRED_VIEWS:
        (ok if suffix in present else fail)(f"{cell.name}{suffix}")
    compare = cell.build / "layout" / f"{cell.name}.compare.md"
    if compare.exists():
        for line in compare.read_text().splitlines():
            if line.startswith("COMPARE:") or "row sites" in line:
                info(line.strip())
    info(
        f"re-run scripts/merge_lib.py so the mapper sees the measured "
        f"{cell.name}.lib instead of the provisional estimate"
    )


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
        "  scripts/pdk_cell.py AION_mux2i_1 --only characterize\n"
        "  scripts/pdk_cell.py AION_mux2i_1 --from layout --draw manual\n"
        "  scripts/pdk_cell.py AION_mux4i_1 --driver-cell sg13g2_buf_2\n",
    )
    parser.add_argument("cell", help="cell name, e.g. AION_mux2i_1")
    parser.add_argument(
        "--from",
        dest="start",
        choices=STAGES,
        default="characterize",
        help="first stage to run",
    )
    parser.add_argument("--only", choices=STAGES, help="run exactly one stage")
    parser.add_argument(
        "--draw",
        choices=DRAW_MODES,
        default="auto",
        help="how the layout generator gets written: auto "
        "drives `claude -p` until the layout verifies "
        "(default), manual scaffolds it and stops",
    )
    parser.add_argument(
        "--max-iters",
        type=int,
        default=12,
        help="agent turns before giving up (default: 12)",
    )
    parser.add_argument(
        "--draw-timeout",
        type=int,
        default=8400,
        help="seconds per agent turn (default: 3600)",
    )
    parser.add_argument(
        "--model",
        default="claude-opus-5",
        help="model that draws (default: claude-opus-5)",
    )
    parser.add_argument(
        "--effort",
        default="max",
        choices=("low", "medium", "high", "xhigh", "max", "none"),
        help="how hard it thinks per turn (default: low)",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="do not narrate the agent turn while it runs; it still goes to the log",
    )
    parser.add_argument(
        "--corners",
        default="typ",
        help="Liberty corners to characterize (default: typ; "
        "'all' publishes one .lib per corner, which "
        "`make pnr` cannot group)",
    )
    parser.add_argument(
        "--driver-cell",
        metavar="NAME",
        help="characterize against this NON-INVERTING cell instead of an ideal "
        "ramp, the way vendor libraries do (e.g. sg13g2_buf_2; an "
        "inverter breaks the run). Every cell here has a "
        "pass gate on an input path, where a ramp is a zero-impedance "
        "driver and so the most optimistic case there is. Applied to the "
        "candidate and the baseline both. Post-layout stage only",
    )
    parser.add_argument(
        "--driver-input",
        metavar="PIN",
        default="A",
        help="input pin of --driver-cell (default: A)",
    )
    parser.add_argument(
        "--driver-output",
        metavar="PIN",
        default="X",
        help="output pin of --driver-cell (default: X, the sg13g2 buffers' output)",
    )
    parser.add_argument(
        "--pex-mode",
        type=int,
        default=PEX_MODE_DEFAULT,
        choices=(1, 2, 3),
        help=f"parasitic extraction mode: 1 C-decoupled, 2 C-coupled, 3 full RC "
        f"(default here: {PEX_MODE_DEFAULT}, C-coupled -- see PEX_MODE_DEFAULT "
        f"for why the extension differs from aion_layout's own default of 3). "
        f"Applies to the candidate and the abutted baseline both",
    )
    parser.add_argument(
        "--jobs", type=int, default=8, help="parallel ngspice jobs (default: 8)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print every command, run nothing"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not echo tool output; it still goes to flow/logs/pdk_extension/",
    )
    args = parser.parse_args(argv)

    cell = Cell(args.cell)
    cell.require_sources()
    run = Runner(dry_run=args.dry_run, quiet=args.quiet)
    driver = (
        Driver(args.driver_cell, args.driver_input, args.driver_output)
        if args.driver_cell
        else None
    )
    draw = Draw(
        mode=args.draw,
        max_iters=args.max_iters,
        timeout=args.draw_timeout,
        model=args.model or None,
        effort=None if args.effort == "none" else args.effort,
        stream=not args.no_stream,
    )

    if not args.dry_run and not container_running():
        raise SystemExit(
            "error: the IIC-OSIC-TOOLS container is not running — every stage "
            "here needs ngspice, magic, netgen or klayout.\n"
            "       docker start iic-osic-tools_shell_uid_1000"
        )

    todo = [args.only] if args.only else list(STAGES[STAGES.index(args.start) :])

    if "layout" in todo and draw.mode == "auto" and not args.dry_run:
        # Resolved before anything runs: "there is no claude on PATH" should
        # stop the script now, not after the characterize stage has spent
        # twenty minutes in ngspice.
        found = shutil.which(draw.claude)
        if found is None:
            raise SystemExit(
                "error: --draw auto needs the `claude` CLI on PATH. Use "
                "--draw manual and draw the generator yourself."
            )
        draw.claude = found

    print(_c(f"AION PDK extension: {cell.name}", "1"))
    info(f"reference  {cell.verilog.relative_to(PROJECT_ROOT)}")
    info(f"netlist    {cell.spice.relative_to(PROJECT_ROOT)}")
    info(f"stages     {' -> '.join(todo)}")
    if "post-layout" in todo:
        info(
            "stimulus   "
            + (driver.describe() if driver else "ideal ramp (no --driver-cell)")
        )
    if "layout" in todo:
        info(
            f"draw       {draw.mode}"
            + (
                f"  (up to {draw.max_iters} turn(s), model {draw.model})"
                if draw.mode == "auto"
                else ""
            )
        )

    for stage in todo:
        if stage == "characterize":
            code = stage_characterize(cell, run)
        elif stage == "layout":
            code = stage_layout(cell, run, args.jobs, draw)
        else:
            code = stage_post_layout(
                cell, run, args.corners, args.jobs, driver, args.pex_mode
            )
        if code == 2:
            # "not drawn yet" is a stopping point, not a failure: the layout
            # is waiting on another turn or on a human, and saying FAILED
            # would be a lie.  Nothing downstream may run on it either way --
            # post-layout would characterize a cell that does not verify.
            print()
            warn(f"stopped at {stage}: the cell does not verify yet")
            if stage == "layout" and draw.mode == "auto":
                info(
                    f"the drawing agent did not converge in "
                    f"{draw.max_iters} turn(s); re-running continues from the "
                    f"generator as it stands"
                )
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
