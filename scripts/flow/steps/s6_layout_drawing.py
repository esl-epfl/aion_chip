# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               6_layout_drawing -- draw, verify and publish
#                             a standard cell per AION module
#
#  This is the only step with an irreducible agent in it.  Exactly one
#  artifact is authored rather than computed:
#
#      aion_flow/tools/aion_layout_claude/cells/<CELL>.py
#
#  a module exposing generate(name, tech) -> Cell.  Everything else -- DRC,
#  LVS, PEX, the abutted PDK baseline, characterization, the comparison and
#  the view export -- is mechanical, has exactly one right answer, and is
#  run here, on the host, never by the agent.  The agent's own report is
#  never the verdict: the verdict is this step's own `make verify`.
#
#    DRAW_MODE=manual (default)  scaffold the generator, print the loop, stop.
#                                Re-run the step once you have drawn it.
#    DRAW_MODE=auto              drive `claude -p` with the aion-layout skill
#                                until `RESULT: PASS` or DRAW_MAX_ITERS.
# ================================================================

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import paths
from ..coherence import BLOCKED
from ..config import Config
from ..pex import PexError, write_wrapper
from ..style import Style, color, fail, info, note, ok, warn
from .base import Context, Step, StepFailed

#: What `make pnr` demands of every published cell.
REQUIRED_VIEWS = (".lef", ".lib", ".gds")
RECOMMENDED_VIEWS = (".v", ".spice", ".cdl")


def _fingerprint(path: Path) -> Optional[str]:
    """Content hash of the generator, or None when it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _log(cell: str, target: str, turn: Optional[int]) -> str:
    """Per-turn log name, so an auto run keeps its whole drawing history."""
    return f"{cell}.{target}" if turn is None else f"{cell}.{turn:02d}.{target}"


@dataclass
class CellOutcome:
    cell: str
    state: str                      # drawn | awaiting_draw | failed
    verdict: Optional[str] = None   # RESULT: ...
    compare: Optional[str] = None   # COMPARE: ...
    published: bool = False
    pex_verified: Optional[bool] = None
    detail: str = ""


class LayoutDrawingStep(Step):
    key = "6_layout_drawing"
    title = "Layout drawing"
    summary = "draw a DRC/LVS-clean cell per AION module and publish its views"
    upstream = ("5_gate_minimization",)
    needs_container = True

    def inputs(self, cfg: Config) -> list:
        return [paths.STEP_DIRS["5_gate_minimization"] / "minimized_spice",
                paths.STEP_DIRS["3_rewrite"] / "nl" / "aion_cells.v"]

    def outputs(self, cfg: Config) -> list:
        return [self.outdir, paths.CELLS_DIR]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg = ctx.cfg
        step5 = paths.STEP_DIRS["5_gate_minimization"]
        cells_v = paths.STEP_DIRS["3_rewrite"] / "nl" / "aion_cells.v"
        self.require(step5 / "minimized_spice", cells_v)

        if cfg.LAYOUT_CORNERS != "typ":
            warn(f"LAYOUT_CORNERS={cfg.LAYOUT_CORNERS!r}: the exporter will "
                 "publish one Liberty per corner, and `make pnr` groups cell "
                 "views by file stem — the extra .lib files become phantom "
                 "cells with no LEF and PnR refuses to start.")

        netlists = sorted((step5 / "minimized_spice").glob("*_minimized.spice"))
        if not netlists:
            if self.dry_run:
                info("no minimized netlists yet — nothing to preview")
                return "ok"
            raise StepFailed("step 5 produced no minimized netlists", 2)

        info(f"{len(netlists)} cell(s) to draw   mode={cfg.DRAW_MODE}")
        outcomes = [self._one_cell(ctx, n, cells_v) for n in netlists]
        return self._summarize(ctx, outcomes)

    # -----------------------------------------------------------------
    def _one_cell(self, ctx: Context, netlist: Path, cells_v: Path) -> CellOutcome:
        cfg, run = ctx.cfg, ctx.runner
        cell = netlist.name[: -len("_minimized.spice")]
        baseline = paths.STEP_DIRS["5_gate_minimization"] / "raw_spice" / f"reference_{cell}.spice"
        build = self.ensure(cell)
        final = build / "final"
        generator = paths.LAYOUT_CELLS / f"{cell}.py"

        print()
        print(color(f"  ── {cell} " + "─" * max(0, 60 - len(cell)),
                    Style.CYAN, Style.BOLD))

        if not baseline.exists():
            warn(f"no PDK baseline at {paths.rel_to_project(baseline)} — the "
                 "area/delay comparison cannot run")
            return CellOutcome(cell, "failed", detail="missing baseline netlist")

        common = [
            f"CELL={cell}",
            f"LAYOUT_NETLIST={netlist}",
            f"BUILD_DIR_LAYOUT={build}",
            f"FINAL_DIR={final}",
        ]

        # ---- the generator: scaffold it once, never overwrite a drawing.
        if not generator.exists():
            info(f"scaffolding {paths.rel_to_project(generator)}")
            run.check(run.layout("scaffold", common, name=f"{cell}.scaffold"),
                      f"scaffolding {cell}")
            note("the scaffold is deliberately incomplete: no taps, no "
                 "source/drain contacts, no routing. Its first verify WILL fail.")

        # ---- draw until the layout verifies.
        if cfg.DRAW_MODE == "auto":
            verdict = self._draw_auto(ctx, cell, netlist, build, common)
        else:
            verdict = self._verify(ctx, cell, common)

        if verdict != "RESULT: PASS":
            self._print_manual_instructions(cell, netlist, baseline, build)
            return CellOutcome(cell, "awaiting_draw", verdict=verdict,
                               detail="layout does not verify yet")

        ok(f"{cell}: {verdict}")

        # ---- everything after this point is mechanical.
        flow = run.layout("flow", [
            *common,
            f"BASELINE={baseline}",
            f"CORNERS={cfg.LAYOUT_CORNERS}",
            f"JOBS={cfg.LAYOUT_JOBS}",
        ], name=f"{cell}.flow")

        compare = flow.verdict("COMPARE:")
        result = flow.verdict("RESULT:")
        if result and result != "RESULT: PASS":
            fail(f"{cell}: {result}")
            return CellOutcome(cell, "failed", verdict=result,
                               detail="the mechanical chain re-graded it FAIL")
        if compare is None and not flow.ok:
            # make collapses every failure to exit 2, so an absent verdict
            # line is the only reliable "this did not run" signal.
            fail(f"{cell}: the layout chain did not reach a verdict — "
                 f"see {paths.rel_to_project(flow.log)}")
            return CellOutcome(cell, "failed", detail="no COMPARE: verdict")

        if compare:
            (ok if compare.startswith("COMPARE: WIN") else warn)(f"{cell}: {compare}")

        # A LOSS is a real result, not a crash: the views in final/ are
        # complete either way, and a cell that loses on delay is still a
        # cell you may want to place while you resize it.
        losing = bool(compare and compare.startswith("COMPARE: LOSS"))
        if losing and not cfg.DRAW_PUBLISH_ON_LOSS:
            warn(f"{cell}: not published (lost the comparison, and "
                 "DRAW_PUBLISH_ON_LOSS is off)")
            return CellOutcome(cell, "drawn", verdict="RESULT: PASS",
                               compare=compare, detail="withheld on loss")

        published = self._publish(ctx, cell, final)
        pex_ok = None
        if cfg.LAYOUT_VERIFY_PEX:
            pex_ok = self._verify_pex(ctx, cell, build, cells_v)

        return CellOutcome(cell, "drawn", verdict="RESULT: PASS",
                           compare=compare, published=published,
                           pex_verified=pex_ok)

    # -----------------------------------------------------------------
    def _verify(self, ctx: Context, cell: str, common: list,
                turn: Optional[int] = None) -> Optional[str]:
        """Build + DRC + LVS. The verdict is the RESULT: line, not the status.

        GNU make reports its own failure as exit 2 whatever the recipe
        returned, so FAIL and ERROR are indistinguishable from the status.
        """
        result = ctx.runner.layout("verify", common, name=_log(cell, "verify", turn))
        return result.verdict("RESULT:")

    def _evidence(self, ctx: Context, cell: str, common: list, build: Path,
                  turn: Optional[int] = None) -> str:
        ctx.runner.layout("evidence", common, name=_log(cell, "evidence", turn))
        packet = build / f"{cell}.evidence.md"
        try:
            return packet.read_text()
        except OSError:
            return ""

    # -----------------------------------------------------------------
    def _draw_auto(self, ctx: Context, cell: str, netlist: Path,
                   build: Path, common: list) -> Optional[str]:
        """edit -> verify -> evidence, until it passes or we run out of turns.

        The host runs every graded step. The agent only ever writes the
        generator, and its own account of what it did is not consulted.
        """
        cfg = ctx.cfg
        if self.dry_run:
            info(f"{cell}: would run up to {cfg.DRAW_MAX_ITERS} agent turn(s)")
            return None
        claude = shutil.which("claude")
        if claude is None:
            raise StepFailed(
                "DRAW_MODE=auto needs the `claude` CLI on PATH. Use the "
                "default DRAW_MODE=manual and draw the generator yourself.", 2)

        generator = paths.LAYOUT_CELLS / f"{cell}.py"
        verdict = None
        idle = 0
        for iteration in range(1, cfg.DRAW_MAX_ITERS + 1):
            verdict = self._verify(ctx, cell, common, turn=iteration)
            if verdict == "RESULT: PASS":
                ok(f"{cell}: verified after {iteration - 1} agent turn(s)")
                return verdict

            info(f"{cell}: turn {iteration}/{cfg.DRAW_MAX_ITERS} "
                 f"({verdict or 'no verdict'})")
            evidence = self._evidence(ctx, cell, common, build, turn=iteration)
            before = _fingerprint(generator)
            report = self._agent_turn(ctx, claude, cell, netlist, build,
                                      evidence, iteration)

            # The artifact on disk is the only thing that counts: an agent
            # that timed out after writing a complete generator has done the
            # work, and one that exited cleanly without touching the file has
            # not. Repeated no-ops mean the agent cannot run at all (no
            # quota, no credentials, a refusal) -- looping on that just burns
            # wall clock and says "did not converge" about something that
            # never started.
            if _fingerprint(generator) == before:
                idle += 1
                warn(f"{cell}: turn {iteration} left the generator unchanged")
                if report:
                    note(f"  agent said: {report}")
                if idle >= 2:
                    fail(f"{cell}: the drawing agent made no change twice in a "
                         f"row — it is not running, not that the layout is hard")
                    note(f"  check {paths.rel_to_project(self.log_dir)}/"
                         f"{cell}.agent.*.log")
                    return verdict
            else:
                idle = 0

        # One last graded check after the final turn.
        verdict = self._verify(ctx, cell, common, turn=cfg.DRAW_MAX_ITERS + 1)
        if verdict == "RESULT: PASS":
            return verdict
        warn(f"{cell}: still {verdict or 'unverified'} after "
             f"{cfg.DRAW_MAX_ITERS} turn(s)")
        return verdict

    def _agent_turn(self, ctx: Context, claude: str, cell: str, netlist: Path,
                    build: Path, evidence: str, iteration: int) -> str:
        cfg = ctx.cfg
        generator = paths.LAYOUT_CELLS / f"{cell}.py"
        tool = paths.LAYOUT_TOOL

        def cmd(target: str) -> str:
            return (f"make -C {tool} {target} CELL={cell} "
                    f"NETLIST={netlist} BUILD_DIR={build}")

        prompt = "\n".join([
            f"Use the aion-layout skill. Draw the standard-cell layout for {cell}.",
            "",
            f"The ONLY file you may create or edit is:",
            f"    {generator}",
            "",
            f"Its target netlist is {netlist}.",
            "NEVER modify the netlist: it is the source of truth for both the "
            "connectivity and the device widths that LVS grades against.",
            "Never hand-edit a GDS, a report, or anything under the build "
            "directory — they are evidence.",
            "",
            "Check your work, and read what to fix, with:",
            f"    {cmd('verify')}",
            f"    {cmd('evidence')}",
            "Work until `verify` prints `RESULT: PASS` at the start of a line.",
            "Exactly one line of that output starts at column 0 and it is the "
            "verdict; everything else is indented. Do not read make's exit "
            "status as the verdict — make reports its own failure as 2 "
            "whatever the tool returned.",
            "",
            f"This is turn {iteration} of {cfg.DRAW_MAX_ITERS}. Stop as soon as "
            "the generator is written and verifying; do not report success you "
            "have not seen a RESULT: PASS for.",
            "",
            "=== evidence from the last verify ===",
            evidence[:60000] if evidence else "(no evidence packet yet)",
        ])

        argv = [
            claude, "-p", prompt,
            "--permission-mode", "acceptEdits",
            "--allowedTools",
            "Read,Edit,Write,Glob,Grep,Bash(make:*),Bash(python3:*),Bash(sed:*),Bash(cat:*)",
            "--add-dir", str(build),
        ]
        if cfg.DRAW_MODEL:
            argv += ["--model", cfg.DRAW_MODEL]

        env = dict(os.environ)
        # Without this the agent's own `make` would drive the container
        # against the other aion_flow checkout.
        env["AION_CONTAINER_MOUNT"] = paths.CONTAINER_AION_FLOW

        log = self.log_dir / f"{cell}.agent.{iteration}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        print(color(f"  ▶ claude -p  (turn {iteration}, {cell})", Style.WHITE))
        try:
            done = subprocess.run(
                argv, cwd=str(paths.AION_FLOW), env=env,
                capture_output=True, text=True, errors="replace",
                timeout=cfg.DRAW_TIMEOUT,
            )
            log.write_text((done.stdout or "") + (done.stderr or ""))
            status = done.returncode
        except subprocess.TimeoutExpired as exc:
            log.write_text(str(exc.stdout or "") + str(exc.stderr or ""))
            status = 124

        # A timed-out or non-zero agent that still wrote a complete generator
        # has produced the artifact; the caller grades the file, not this.
        note(f"agent exit {status}   log {paths.rel_to_project(log)}")
        if status == 0:
            return ""
        try:
            said = log.read_text().strip().splitlines()
        except OSError:
            said = []
        return said[-1][:200] if said else f"exit {status}, no output"

    # -----------------------------------------------------------------
    def _publish(self, ctx: Context, cell: str, final: Path) -> bool:
        """Copy the exported views into implementation/cells/<CELL>/.

        `make pnr` discovers cells by walking that directory and grouping by
        file STEM, so one stray recognised file is a phantom cell with
        missing views and PnR refuses to start. One directory per cell keeps
        it tidy; the grouping is by stem either way.
        """
        if not final.is_dir():
            warn(f"{cell}: nothing exported to {paths.rel_to_project(final)}")
            return False

        rejected = sorted(final.glob("*.rejected"))
        if rejected:
            fail(f"{cell}: the exporter REFUSED to publish — "
                 + ", ".join(p.name for p in rejected))
            note("that is a real finding about the cell (usually the Verilog "
                 "function solved from the netlist disagreeing with the "
                 "Liberty), not something to work around")
            return False

        views = {p.suffix.lower(): p for p in final.iterdir() if p.is_file()}
        missing = [ext for ext in REQUIRED_VIEWS if ext not in views]
        if missing:
            fail(f"{cell}: cannot publish, missing {', '.join(missing)}")
            return False

        libs = sorted(final.glob("*.lib"))
        if len(libs) > 1:
            fail(f"{cell}: {len(libs)} Liberty files exported. `make pnr` "
                 "groups views by file stem, so per-corner libs become "
                 "phantom cells. Re-run with LAYOUT_CORNERS=typ.")
            return False

        target = paths.CELLS_DIR / cell
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        copied = []
        for path in sorted(final.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in REQUIRED_VIEWS + RECOMMENDED_VIEWS:
                continue
            shutil.copy2(path, target / path.name)
            copied.append(path.name)

        ok(f"{cell}: published {len(copied)} view(s) to "
           f"{paths.rel_to_project(target)}")
        note("  " + "  ".join(copied))
        ctx.note(f"{cell} published to implementation/cells/{cell}/")
        return True

    # -----------------------------------------------------------------
    def _verify_pex(self, ctx: Context, cell: str, build: Path,
                    cells_v: Path) -> Optional[bool]:
        """Re-prove the cell's function on the EXTRACTED netlist.

        LVS already proved layout == schematic. This proves the extracted
        netlist still computes the module's function, over every input
        vector, against both the Liberty gold model and the PDK-cell
        reference — which is the one thing aion_layout's own characterize
        pass explicitly does not check.
        """
        candidates = sorted((build / "pex").glob(f"{cell}_pex*.spice")) or \
                     sorted((build / "pex").glob("*.spice"))
        if not candidates:
            warn(f"{cell}: no PEX netlist to verify")
            return None
        pex = candidates[0]

        wrapper = build / "verify" / f"{cell}_pex_wrapped.spice"
        try:
            write_wrapper(pex, cell, cells_v, wrapper)
        except PexError as exc:
            fail(f"{cell}: cannot build a PEX verification wrapper — {exc}")
            ctx.note(f"{cell} PEX verify skipped: {exc}")
            return False

        info(f"{cell}: verifying the extracted netlist in SPICE")
        result = ctx.runner.container("aion-char-verify-spice", [
            f"CELL={cell}",
            f"SPICE={paths.container(wrapper)}",
            # Required: without it this target falls back to an aion_opt
            # netlist path that does not exist in this project.
            f"NETLIST={paths.container(cells_v)}",
            # A separate build root: a per-cell verification regenerates the
            # aion_char testbench tree restricted to that one module.
            f"BUILD_DIR={paths.container(build / 'verify')}",
        ], name=f"{cell}.pex-verify")

        if result.ok:
            ok(f"{cell}: extracted layout matches the cell function")
            return True
        for line in result.output.splitlines():
            if line.startswith("[FAIL]"):
                warn("  " + line.strip())
        fail(f"{cell}: PEX SPICE verification FAILED — "
             f"see {paths.rel_to_project(result.log)}")
        ctx.note(f"{cell} PEX verification failed")
        return False

    # -----------------------------------------------------------------
    def _print_manual_instructions(self, cell: str, netlist: Path,
                                   baseline: Path, build: Path) -> None:
        tool = paths.LAYOUT_TOOL
        print()
        warn(f"{cell} is waiting for a layout")
        note(f"draw   {paths.rel_to_project(paths.LAYOUT_CELLS / f'{cell}.py')}")
        note("then, from anywhere:")
        note(f"  export AION_CONTAINER_MOUNT={paths.CONTAINER_AION_FLOW}")
        note(f"  make -C {tool} verify   CELL={cell} NETLIST={netlist} BUILD_DIR={build}")
        note(f"  make -C {tool} evidence CELL={cell} NETLIST={netlist} BUILD_DIR={build}")
        note("repeat until `RESULT: PASS`, then re-run:")
        note("  python flow.py 6")
        note("or let an agent do it:  python flow.py 6 --draw auto")

    # -----------------------------------------------------------------
    def _write_cells_core(self, cells: list) -> None:
        """Regenerate flow/aion_cells.core to name the published models.

        The post-PnR netlist instantiates the AION cells as leaf cells, so
        post_pnr_sim_ai needs their Verilog. That set is only known once this
        step has run, which is why the core file is generated rather than
        tracked with a fixed list.
        """
        core = paths.FLOW_DIR / "aion_cells.core"
        models = []
        for cell in sorted(cells):
            model = self.outdir / cell / "final" / f"{cell}.v"
            if model.exists():
                models.append(str(model.relative_to(paths.FLOW_DIR)))

        files = models or ["no_aion_cells.v"]
        listed = "\n".join(f"      - {f}" for f in files)
        summary = (f"{len(models)} drawn cell(s): " + ", ".join(sorted(cells))
                   if models else
                   "no cell has been drawn yet -- this is the placeholder")

        core.write_text(f"""CAPI=2:

# ================================================================
#  Project:     AION
#  Author:      Filippo Quadri <filippo.quadri@epfl.ch>
#  Description: Simulation models of the drawn AION standard cells
#
#  GENERATED BY FLOW STEP 6 -- edits here are overwritten by `./flow.py 6`.
# ================================================================

name: epfl:aion:flow_cells:1.0.0
description: |
  Verilog models of the AION cells flow step 6 drew and published.
  Currently: {summary}.

  The post-PnR netlist instantiates the AION cells as LEAF cells -- LibreLane
  hardens them as macros and the SDF carries one CELL entry per instance -- so
  a post-PnR simulation needs the behavioural model `aion_layout export` solved
  from the transistor netlist, not the structural view in
  3_rewrite/nl/aion_cells.v that step 3's simulation uses.

filesets:
  models:
    files:
{listed}
    file_type: verilogSource

targets:
  default: &default
    filesets:
      - models
    toplevel: tt_um_aion
""")
        if models:
            ok(f"wrote {paths.rel_to_project(core)} with {len(models)} model(s)")
        else:
            note(f"{paths.rel_to_project(core)} left at its placeholder — "
                 "a post-PnR AI simulation cannot elaborate yet")

    # -----------------------------------------------------------------
    def _summarize(self, ctx: Context, outcomes: list) -> str:
        drawn = [o for o in outcomes if o.state == "drawn"]
        waiting = [o for o in outcomes if o.state == "awaiting_draw"]
        broken = [o for o in outcomes if o.state == "failed"]
        published = [o for o in drawn if o.published]
        self._write_cells_core([o.cell for o in published])

        print()
        info(f"{len(published)}/{len(outcomes)} cell(s) published to "
             f"{paths.rel_to_project(paths.CELLS_DIR)}")
        for outcome in outcomes:
            bits = [outcome.state]
            if outcome.compare:
                bits.append(outcome.compare.replace("COMPARE: ", ""))
            if outcome.pex_verified is True:
                bits.append("pex ok")
            elif outcome.pex_verified is False:
                bits.append("pex FAILED")
            note(f"  {outcome.cell:<34} {'  '.join(bits)}")
            ctx.note(f"{outcome.cell}: {'  '.join(bits)}")

        if broken:
            raise StepFailed(
                f"{len(broken)} cell(s) failed: "
                + ", ".join(o.cell for o in broken), 1)
        if waiting:
            warn(f"{len(waiting)} cell(s) still need a layout. Step 7 will "
                 "REFUSE to run until they are published, because the netlist "
                 "instantiates them and PnR has no views to place.")
            return BLOCKED
        if not published:
            raise StepFailed("no cell was published; step 7 has nothing to place", 1)
        return "ok"


STEP = LayoutDrawingStep()
