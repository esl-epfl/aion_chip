# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The AION chip flow handler
#
#    python flow.py                 run every step
#    python flow.py 3               run 3_rewrite alone
#    python flow.py 2..5            run a range
#    python flow.py 6 --draw auto   let an agent draw the layouts
#    python flow.py 6 --draw auto -j 3   ... three cells at a time
#    python flow.py status          is this flow coherent?
#
#  Each step is independent: it reads what it declares, writes under
#  flow/<step>/, and records both in flow/coherence.json.  Re-running one
#  step marks everything downstream STALE — loudly, but nothing is blocked,
#  because re-running a single step is the normal way to work here.
# ================================================================

from __future__ import annotations

import argparse
import sys
import time
import traceback
from typing import Optional

from . import paths, steps
from .coherence import (BLOCKED, Coherence, FAILED, MISSING, OK, STALE,
                        Verdict, badge, now)
from .config import Config
from .runner import Runner, StepFailed, container_running, terminate_all
from .style import Style, banner, color, fail, info, note, ok, section, title, warn

EPILOG = """\
steps
  1  1_synth               synthesis + post-synthesis simulation
  2  2_pattern_extraction  mine the AION cell library
  3  3_rewrite             substitute the cells + LEC + simulation
  4  4_characterization    exhaustive SV and SPICE testbenches
  5  5_gate_minimization   one CMOS stack per cell, proved in SPICE
  6  6_layout_drawing      draw, verify and publish the cell views
  7  7_pnr                 place and route + post-PnR simulation

examples
  python flow.py                          the whole chain
  python flow.py 2 --set ELITE_COUNT=2    mine, keep the best two cells
  python flow.py 3 --set REWRITE_CELLS=flow/my_cells.v
  python flow.py 6 --draw auto            agent-drawn layouts
  python flow.py 6 --draw auto -j 3       three of them at once
  python flow.py status                   per-step timestamps and staleness
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flow.py",
        description="Run the AION chip flow, one step or all of it.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("steps", nargs="*", metavar="STEP",
                        help="step numbers, names, or ranges like 2..5. "
                             "'status' prints the coherence table.")
    parser.add_argument("--set", dest="settings", action="append", default=[],
                        metavar="NAME=VALUE",
                        help="override a flow variable (see scripts/flow/config.py)")
    parser.add_argument("--draw", choices=("manual", "auto"),
                        help="step 6: draw the layouts by hand (default) or "
                             "with an agent")
    parser.add_argument("--draw-jobs", "-j", type=int, metavar="N",
                        dest="draw_jobs",
                        help="step 6: cells to work on at once (default 1). "
                             "With --draw auto that is N agent sessions in "
                             "parallel; every line is tagged with its cell.")
    parser.add_argument("--lenient", action="store_true",
                        help="downgrade LibreLane's hard checkers to warnings")
    parser.add_argument("--force", action="store_true",
                        help="do not warn about stale upstream steps")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the commands without running them")
    parser.add_argument("--quiet", action="store_true",
                        help="do not echo tool output (it still goes to the logs)")
    parser.add_argument("--timeout", type=int, metavar="SECONDS",
                        help="per-command wall clock (default 4h)")
    parser.add_argument("--status", action="store_true",
                        help="print the coherence table and exit")
    parser.add_argument("--list", action="store_true",
                        help="list the steps and exit")
    return parser


# ---------------------------------------------------------------------
def print_status(coherence: Coherence) -> int:
    title(" flow coherence ")
    header = f"  {'step':<24}{'state':<12}{'finished':<28}notes"
    print(color(header, Style.BOLD))
    print(color("  " + "-" * 92, Style.DIM))

    incoherent = 0
    stale = set()
    for step in steps.STEPS:
        # Transitive, not just direct: if synthesis re-ran, step 6's layouts
        # were drawn from a netlist that no longer exists even though step 6's
        # own direct input (step 5) has not been touched since.
        upstream = list(steps.upstream_of(step))
        verdict = coherence.check(step.key, upstream)
        if verdict.state == OK and stale.intersection(upstream):
            verdict = Verdict(step.key, STALE,
                              [f"depends on {u}, which is stale"
                               for u in sorted(stale.intersection(upstream))])
        record = coherence.get(step.key) or {}
        finished = record.get("finished", "—")
        notes = "; ".join(record.get("notes", [])[:2])
        print(f"  {step.key:<24}{badge(verdict.state)}  {finished:<26}"
              + color(notes[:36], Style.DIM))
        for reason in verdict.reasons:
            print(color(f"      · {reason}", Style.YELLOW))
        if verdict.state in (STALE, FAILED, MISSING):
            stale.add(step.key)
        if verdict.state in (STALE, FAILED):
            incoherent += 1

    print()
    if incoherent:
        warn(f"{incoherent} step(s) no longer describe what is on disk")
        note("re-run them, or accept that flow/ mixes results from "
             "different inputs")
    else:
        ok("every recorded step is consistent with its inputs")
    print()
    return 1 if incoherent else 0


def print_list() -> int:
    title(" aion flow ")
    for step in steps.STEPS:
        number = step.key.split("_", 1)[0]
        print(f"  {color(number, Style.CYAN, Style.BOLD)}  "
              f"{color(step.key, Style.BOLD):<34} {step.summary}")
        if step.upstream:
            note(f"    after: {', '.join(step.upstream)}")
    print()
    return 0


# ---------------------------------------------------------------------
def warn_if_stale(coherence: Coherence, step, selected: set, done: set,
                  force: bool) -> None:
    """Say, loudly, when a step is about to run on stale ground."""
    problems = []
    for key in steps.upstream_of(step):
        if key in done:
            continue        # already re-run, earlier in this same invocation
        verdict = coherence.check(key, steps.upstream_of(steps.BY_KEY[key]))
        if verdict.state in (STALE, MISSING, FAILED):
            problems.append((key, verdict))
        elif key in selected:
            # Selected, but scheduled AFTER this step: running them in this
            # order means this step reads the old artifacts and the upstream
            # re-run then invalidates what it just produced.
            problems.append((key, Verdict(key, STALE,
                                          ["queued to re-run AFTER this step"])))
    if not problems or force:
        return

    print()
    print(color(banner(" STALE UPSTREAM ", fill="!"), Style.BG_YELLOW,
                Style.BOLD))
    for key, verdict in problems:
        warn(f"{key} is {verdict.state.upper()}")
        for reason in verdict.reasons:
            note(f"  · {reason}")
    note("running anyway — the result will not describe one coherent chip")
    print()


def run_step(step, ctx, coherence: Coherence, index: int, total: int) -> str:
    cfg = ctx.cfg
    print()
    print(color(banner(f" STEP {index}/{total}: {step.title.upper()} ", fill="═"),
                Style.CYAN, Style.BOLD))
    note(step.summary)
    note(f"output → {paths.rel_to_project(step.outdir)}/")
    print()

    step.dry_run = ctx.runner.dry_run
    step.ensure()
    started = now()
    began = time.monotonic()
    ctx.notes.clear()
    ctx.runner.commands.clear()
    ctx.runner.log_dir = step.log_dir

    try:
        status = step.run(ctx) or OK
    except StepFailed as exc:
        if ctx.runner.dry_run:
            warn(f"{step.key}: {exc}")
            return OK
        coherence.record(step.key, status=FAILED, started=started,
                         inputs=step.inputs(cfg), outputs=step.outputs(cfg),
                         config=cfg.digest(), notes=ctx.notes + [str(exc)],
                         commands=list(ctx.runner.commands))
        print()
        print(color(banner(" FAILED ", fill="═"), Style.BG_RED, Style.WHITE,
                    Style.BOLD))
        fail(f"{step.key}: {exc}")
        if exc.log:
            note(f"log: {paths.rel_to_project(exc.log)}")
        print()
        raise

    elapsed = time.monotonic() - began
    if ctx.runner.dry_run:
        return status
    coherence.record(step.key, status=status, started=started,
                     inputs=step.inputs(cfg), outputs=step.outputs(cfg),
                     config=cfg.digest(), notes=list(ctx.notes),
                     commands=list(ctx.runner.commands))

    print()
    if status == BLOCKED:
        warn(f"{step.key} is waiting on you ({elapsed:.0f}s)")
    else:
        ok(f"{step.key} completed in {elapsed:.0f}s")
    return status


# ---------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    tokens = list(args.steps)
    if args.list:
        return print_list()

    coherence = Coherence()
    if args.status or (len(tokens) == 1 and tokens[0].lower() == "status"):
        return print_status(coherence)

    cfg = Config()
    try:
        cfg.apply(args.settings)
    except ValueError as exc:
        parser.error(str(exc))
    if args.draw:
        cfg.DRAW_MODE = args.draw
    if args.draw_jobs is not None:
        if args.draw_jobs < 1:
            parser.error("--draw-jobs must be at least 1")
        cfg.DRAW_JOBS = args.draw_jobs
    if args.lenient:
        cfg.LENIENT = True
    if args.timeout:
        cfg.TIMEOUT = args.timeout

    try:
        chosen = steps.expand(tokens)
    except KeyError as exc:
        parser.error(str(exc).strip('"'))

    if cfg.LENIENT:
        warn("LENIENT is on: LibreLane's hard checkers are warnings. "
             "Never sign off a run made this way.")

    if any(s.needs_container for s in chosen) and not args.dry_run:
        if not container_running():
            fail(f"the EDA container {paths.CONTAINER_NAME} is not running")
            note("start iic-osic-tools first; DRC, LVS, PEX, ngspice, "
                 "kepler-formal and librelane all live in it")
            return 4

    runner = Runner(log_dir=paths.FLOW_DIR, timeout=cfg.TIMEOUT,
                    dry_run=args.dry_run, quiet=args.quiet)
    ctx = steps.Context(cfg=cfg, runner=runner, coherence=coherence,
                        force=args.force)

    title(" AION FLOW ")
    info(f"{len(chosen)} step(s): " + ", ".join(s.key for s in chosen))
    if cfg._overrides:
        info("overrides: " + "  ".join(f"{k}={v}" for k, v in cfg._overrides.items()))
    if args.dry_run:
        warn("dry run — nothing will be executed")

    selected = {s.key for s in chosen}
    done: set = set()
    blocked: list = []
    for index, step in enumerate(chosen, start=1):
        warn_if_stale(coherence, step, selected, done, args.force)
        try:
            status = run_step(step, ctx, coherence, index, len(chosen))
        except StepFailed as exc:
            code = exc.returncode or 1
            if code < 0:
                # A negative status is "child died on signal -code"; returning
                # it makes the shell report 256+code, which reads as neither.
                fail(f"{step.key} was killed by signal {-code} "
                     f"(the usual cause is the OOM killer)")
                return 1
            return code if 0 < code < 256 else 1
        except KeyboardInterrupt:
            # Every child is in its own process group, so the terminal's
            # SIGINT reached this process only.
            print()
            killed = terminate_all()
            fail(f"interrupted — killed {killed} running command(s)"
                 if killed else "interrupted")
            return 130
        except Exception:                                # pragma: no cover
            traceback.print_exc()
            fail(f"{step.key} raised an unexpected error")
            return 1
        done.add(step.key)
        if status == BLOCKED:
            blocked.append(step.key)

    print()
    if blocked:
        print(color(banner(" WAITING ", fill="═"), Style.CYAN, Style.BOLD))
        warn("finished, but these steps need you: " + ", ".join(blocked))
        note("exit status 3 — the flow did not fail, and it is not done")
    else:
        print(color(banner(" SUCCESS ", fill="═"), Style.GREEN, Style.BOLD))
        ok(f"{len(chosen)} step(s) completed")
    note(f"coherence: {paths.rel_to_project(paths.COHERENCE_FILE)}")
    print()
    return 3 if blocked else 0


if __name__ == "__main__":                               # pragma: no cover
    sys.exit(main())
