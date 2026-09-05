# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-01
#  Updated:                   2026-09-04
#  Description:               Helper for `make sim_all` — runs every
#                             simulation stage that has its inputs and
#                             prints a colored summary.
# ================================================================
#
# Stages are skipped, not failed, when the physical flow has not produced
# their netlist yet: a fresh clone can run the RTL stage and nothing else.
#
# The "Timing" column is the point of the table. Only the post-PnR Icarus run
# has any: Verilator ignores $sdf_annotate outright, and a synthesis netlist
# has no placement to derive interconnect delay from. A pass in an untimed row
# says the logic is right, never that the chip will close timing.

import os
import re
import subprocess
import sys
import time

from tabulate import tabulate

# ANSI Color Codes
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[32m"
C_RED = "\033[31m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_MAGENTA = "\033[35m"
C_BG_GREEN = "\033[42m\033[30m"
C_BG_RED = "\033[41m\033[37m"
C_BG_YELLOW = "\033[43m\033[30m"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOW_DIR = os.path.join(PROJECT_ROOT, "flow")
TOPLEVEL = os.environ.get("TOPLEVEL", "tt_um_aion")
SDF_CORNER = os.environ.get("SDF_CORNER", "nom_typ_1p20V_25C")


def netlist(stage):
    return os.path.join(FLOW_DIR, stage, "nl", f"{TOPLEVEL}.nl.v")


# name, make target, make variables, timing?, what must exist first
STAGES = [
    ("RTL (GHDL)", "sim", {}, False, None),
    ("Post-synth (Verilator)", "post_synth_sim", {"TOOL": "verilator"}, False,
     netlist("synth")),
    ("Post-synth (Icarus)", "post_synth_sim", {"TOOL": "icarus"}, False,
     netlist("synth")),
    ("Post-PnR (Verilator)", "post_pnr_sim", {"TOOL": "verilator"}, False,
     netlist("pnr_simple")),
    ("Post-PnR (Icarus + SDF)", "post_pnr_sim", {"TOOL": "icarus"}, True,
     netlist("pnr_simple")),
]

EMPTY = {"total": 0, "pass": 0, "fail": 0, "skip": 0}


def run_stage(name, target, variables, log_dir):
    make_cmd = os.environ.get("MAKE", "make")
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    log_file = os.path.join(log_dir, f"sim_all_{slug}.log")

    cmd = [make_cmd, target] + [f"{k}={v}" for k, v in variables.items()]
    if SDF_CORNER:
        cmd.append(f"SDF_CORNER={SDF_CORNER}")

    print(f"  {C_CYAN}►{C_RESET} Running {C_BOLD}{name}{C_RESET}...", end="", flush=True)
    start = time.time()
    with open(log_file, "w") as log:
        result = subprocess.run(
            cmd, stdout=log, stderr=subprocess.STDOUT, cwd=PROJECT_ROOT
        )
    elapsed = time.time() - start

    status = f"{C_GREEN}Done{C_RESET}" if result.returncode == 0 else f"{C_RED}Failed{C_RESET}"
    print(f"\r  {C_CYAN}✓{C_RESET} Finished {C_BOLD}{name}{C_RESET} [{status}] ({elapsed:.2f}s)  ")
    return result.returncode, elapsed, log_file


def parse_stats(log_file):
    if not os.path.exists(log_file):
        return dict(EMPTY)
    with open(log_file, errors="replace") as f:
        text = f.read()
    m = re.search(r"TESTS=(\d+) PASS=(\d+) FAIL=(\d+) SKIP=(\d+)", text)
    if not m:
        return dict(EMPTY)
    return {
        "total": int(m.group(1)),
        "pass": int(m.group(2)),
        "fail": int(m.group(3)),
        "skip": int(m.group(4)),
    }


def count_annotations(log_file):
    """How much of the SDF Icarus actually applied, for the timed stage."""
    if not os.path.exists(log_file):
        return None
    with open(log_file, errors="replace") as f:
        text = f.read()
    if "sdf_file=" not in text:
        return None
    return {
        "unmatched": len(re.findall(r"Could not find intermodpath", text)),
        "timingcheck": len(re.findall(r"TIMINGCHECK not supported", text)),
    }


def format_row(name, state, stats, elapsed, timing, is_total=False):
    colors = {"pass": C_GREEN, "fail": C_RED, "skip": C_YELLOW}
    labels = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}

    fmt = (lambda x: f"{C_BOLD}{x}{C_RESET}") if is_total else str

    return [
        fmt(name),
        f"{colors[state]}{labels[state]}{C_RESET}",
        f"{C_GREEN if stats['pass'] else C_DIM}{stats['pass']}{C_RESET}",
        f"{C_RED if stats['fail'] else C_DIM}{stats['fail']}{C_RESET}",
        f"{C_YELLOW if stats['skip'] else C_DIM}{stats['skip']}{C_RESET}",
        fmt(stats["total"]),
        (f"{C_GREEN}SDF{C_RESET}" if timing else f"{C_DIM}—{C_RESET}"),
        f"{C_MAGENTA}{elapsed:.2f}s{C_RESET}",
    ]


def main():
    build_dir = os.path.join(PROJECT_ROOT, os.environ.get("BUILD_DIR", ".build"))
    os.makedirs(build_dir, exist_ok=True)

    banner = " AION SIMULATION DASHBOARD "
    print(f"\n{C_BOLD}{C_CYAN}{'═' * 72}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}{banner:^72}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}{'═' * 72}{C_RESET}\n")

    rows = []
    logs = []
    annotations = None
    totals = dict(EMPTY)
    total_time = 0.0
    any_failed = False
    any_skipped = False

    for name, target, variables, timing, requirement in STAGES:
        if requirement and not os.path.isfile(requirement):
            rel = os.path.relpath(requirement, PROJECT_ROOT)
            print(f"  {C_YELLOW}•{C_RESET} Skipping {C_BOLD}{name}{C_RESET} — {rel} does not exist yet")
            rows.append(format_row(name, "skip", dict(EMPTY), 0.0, timing))
            any_skipped = True
            continue

        code, elapsed, log_file = run_stage(name, target, variables, build_dir)
        stats = parse_stats(log_file)
        ok = code == 0 and stats["fail"] == 0 and stats["total"] > 0
        any_failed |= not ok

        rows.append(format_row(name, "pass" if ok else "fail", stats, elapsed, timing))
        logs.append((name, log_file))
        for k in totals:
            totals[k] += stats[k]
        total_time += elapsed
        if timing:
            annotations = count_annotations(log_file)

    rows.append(
        format_row("Total", "fail" if any_failed else "pass", totals, total_time, False, True)
    )

    headers = [
        f"{C_BOLD}Stage{C_RESET}",
        f"{C_BOLD}Status{C_RESET}",
        f"{C_BOLD}Passed{C_RESET}",
        f"{C_BOLD}Failed{C_RESET}",
        f"{C_BOLD}Skipped{C_RESET}",
        f"{C_BOLD}Total{C_RESET}",
        f"{C_BOLD}Timing{C_RESET}",
        f"{C_BOLD}Time{C_RESET}",
    ]

    print()
    print(
        tabulate(
            rows,
            headers=headers,
            tablefmt="rounded_outline",
            colalign=("left", "center", "right", "right", "right", "right", "center", "right"),
        )
    )
    print()

    if annotations is not None:
        print(f"{C_DIM}SDF back-annotation ({SDF_CORNER}):{C_RESET}")
        print(f"  • {annotations['unmatched']} interconnect delays Icarus could not place")
        print(f"  • {annotations['timingcheck']} timing checks ignored — Icarus does not")
        print(f"    apply SDF TIMINGCHECK, so setup/hold is STA's job, not this run's.\n")

    if logs:
        print(f"{C_DIM}Logs:{C_RESET}")
        for name, log_file in logs:
            print(f"  • {name:<26} {os.path.relpath(log_file, PROJECT_ROOT)}")
        print()

    if any_failed:
        print(f" {C_BG_RED}{C_BOLD}  FAIL  {C_RESET} {C_RED}{C_BOLD}One or more simulation stages failed.{C_RESET}\n")
        return 1
    if any_skipped:
        print(f" {C_BG_YELLOW}{C_BOLD}  PASS  {C_RESET} {C_YELLOW}{C_BOLD}Every stage that had its inputs passed; some were skipped.{C_RESET}\n")
        return 0
    print(f" {C_BG_GREEN}{C_BOLD}  PASS  {C_RESET} {C_GREEN}{C_BOLD}All simulation stages passed.{C_RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
