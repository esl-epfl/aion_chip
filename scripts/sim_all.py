# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-01
#  Description:               Helper for `make sim_all` — runs icarus
#                             and verilator simulations and prints a
#                             colored summary.
# ================================================================

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


def run_make(target, tool=None):
    core = os.environ.get("CORE", "aion")
    build_dir = os.environ.get("BUILD_DIR", ".build")
    make_cmd = os.environ.get("MAKE", "make")

    log_file = os.path.join(build_dir, f"sim_all_{target}_{core}.log")
    cmd = [make_cmd, target, f"CORE={core}"]
    if tool:
        cmd.append(f"TOOL={tool}")

    display_name = f"{target} ({tool})" if tool else target
    print(
        f"  {C_CYAN}►{C_RESET} Executing {C_BOLD}{display_name}{C_RESET}...",
        end="",
        flush=True,
    )

    os.makedirs(build_dir, exist_ok=True)
    start = time.time()

    with open(log_file, "w") as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)

    elapsed = time.time() - start
    status = (
        f"{C_GREEN}Done{C_RESET}"
        if result.returncode == 0
        else f"{C_RED}Failed{C_RESET}"
    )
    print(
        f"\r  {C_CYAN}✓{C_RESET} Finished {C_BOLD}{display_name}{C_RESET} [{status}] ({elapsed:.2f}s)"
    )

    return result.returncode, elapsed, log_file


def parse_stats(log_file):
    if not os.path.exists(log_file):
        return {"total": 0, "pass": 0, "fail": 0, "skip": 0}

    with open(log_file) as f:
        text = f.read()

    m = re.search(r"TESTS=(\d+) PASS=(\d+) FAIL=(\d+) SKIP=(\d+)", text)
    if m:
        return {
            "total": int(m.group(1)),
            "pass": int(m.group(2)),
            "fail": int(m.group(3)),
            "skip": int(m.group(4)),
        }
    return {"total": 0, "pass": 0, "fail": 0, "skip": 0}


def format_row(name, ok, stats, elapsed, is_total=False):
    status_text = "PASS" if ok else "FAIL"
    status_color = C_GREEN if ok else C_RED

    fmt = (lambda x: f"{C_BOLD}{x}{C_RESET}") if is_total else (lambda x: str(x))

    # Apply conditional colors to stats columns
    pass_str = f"{C_GREEN if stats['pass'] > 0 else C_DIM}{stats['pass']}{C_RESET}"
    fail_str = f"{C_RED if stats['fail'] > 0 else C_DIM}{stats['fail']}{C_RESET}"
    skip_str = f"{C_YELLOW if stats['skip'] > 0 else C_DIM}{stats['skip']}{C_RESET}"

    return [
        fmt(name),
        f"{status_color}{status_text}{C_RESET}",
        pass_str,
        fail_str,
        skip_str,
        fmt(stats["total"]),
        f"{C_MAGENTA}{elapsed:.2f}s{C_RESET}",
    ]


def main():
    core = os.environ.get("CORE", "fll")

    # Header Banner
    banner_text = f" AION {core.upper()} SIMULATION DASHBOARD "
    print(f"\n{C_BOLD}{C_CYAN}{'═' * 60}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}{banner_text:^60}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}{'═' * 60}{C_RESET}\n")

    # Run simulations
    icarus_exit, icarus_time, icarus_log = run_make("sim", "icarus")
    icarus_stats = parse_stats(icarus_log)

    verilator_exit, verilator_time, verilator_log = run_make("sim_verilator")
    verilator_stats = parse_stats(verilator_log)

    # Calculate individual status
    icarus_ok = (icarus_exit == 0) and (icarus_stats["fail"] == 0)
    verilator_ok = (verilator_exit == 0) and (verilator_stats["fail"] == 0)

    # Total status calculations
    total_stats = {
        "pass": icarus_stats["pass"] + verilator_stats["pass"],
        "fail": icarus_stats["fail"] + verilator_stats["fail"],
        "skip": icarus_stats["skip"] + verilator_stats["skip"],
        "total": icarus_stats["total"] + verilator_stats["total"],
    }
    total_time = icarus_time + verilator_time
    overall_ok = (total_stats["fail"] == 0) and icarus_ok and verilator_ok

    # Build Table Rows
    table_data = [
        format_row("Icarus", icarus_ok, icarus_stats, icarus_time),
        format_row("Verilator", verilator_ok, verilator_stats, verilator_time),
        format_row("Total", overall_ok, total_stats, total_time, is_total=True),
    ]

    headers = [
        f"{C_BOLD}Target{C_RESET}",
        f"{C_BOLD}Status{C_RESET}",
        f"{C_BOLD}Passed{C_RESET}",
        f"{C_BOLD}Failed{C_RESET}",
        f"{C_BOLD}Skipped{C_RESET}",
        f"{C_BOLD}Total{C_RESET}",
        f"{C_BOLD}Time{C_RESET}",
    ]

    # Render via Tabulate using fancy grid format
    print()
    print(
        tabulate(
            table_data,
            headers=headers,
            tablefmt="rounded_outline",
            colalign=("left", "center", "right", "right", "right", "right", "right"),
        )
    )
    print()

    # Output Logs Information
    print(f"{C_DIM}Logs:{C_RESET}")
    print(f"  • Icarus:    {icarus_log}")
    print(f"  • Verilator: {verilator_log}\n")

    # Final Summary Status Badge
    if overall_ok:
        print(
            f" {C_BG_GREEN}{C_BOLD}  PASS  {C_RESET} {C_GREEN}{C_BOLD}All simulation suites passed successfully!{C_RESET}\n"
        )
        return 0
    else:
        print(
            f" {C_BG_RED}{C_BOLD}  FAIL  {C_RESET} {C_RED}{C_BOLD}One or more simulation tests failed.{C_RESET}\n"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
