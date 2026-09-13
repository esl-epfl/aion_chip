#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-13
#  Description:               Package a hardened run for TinyTapeout and
#                             check that the package can be submitted.
#
#  What TinyTapeout does with a custom GDS
#  ---------------------------------------
#  A design built outside TT's CI is submitted through
#  tt-gds-action/custom_gds, which takes three files -- the GDS, the LEF and
#  the gate-level netlist -- copies them into tt_submission/ and hands them
#  to the precheck. Nothing is placed, routed or connected on TT's side: the
#  GDS has to be the tile already, with the template DEF's pins exactly
#  where the template has them. So the precheck is TT's whole acceptance
#  test, and this runs that precheck, TT's code rather than a paraphrase of
#  it, on exactly the files custom_gds would be given.
#
#  What the precheck does not look at
#  ----------------------------------
#  It checks layout rules, pins, layers and the die. It runs no LVS, no STA
#  and no XOR, so a tile with a short in it passes. LibreLane's own checkers
#  stop such a run -- unless it was made with LENIENT=1, which is exactly
#  when they do not. So before the precheck, the run's metrics.json is read
#  back against SIGNOFF below, and both have to be clean.
#
#  The package
#  -----------
#  --out (default <run>/tt_submission/) receives <top>.gds, <top>.lef and
#  <top>.v (the logical netlist, nl/ -- what TT's own flow submits for
#  ihp-sg13g2, with no power ports), TT's reports in precheck/, and
#  tt_precheck.json with the verdict. Flow step 10 points --out at its own
#  directory. The directory is wiped first, and refused if it holds anything
#  this script did not write.
#
#  Runs inside iic-osic-tools (klayout, gdstk and the PDK live there);
#  `make tt_precheck` sends it. The precheck writes into its own directory
#  and finds its DRC scripts relative to the working directory, so every run
#  gets a private copy of it under --work, and two runs cannot share or
#  inherit each other's reports.
#
#  The IHP deck is the container's PDK, not the version TT pins in
#  tt-gds-action/precheck, so a rule the two versions disagree on is
#  something only TT's run can settle.
# ================================================================

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

#: Metrics the hardening run has to come back clean on, as (key, label).
#: A metric that is missing means the step that measures it did not run --
#: and a check that examined nothing is not a clean check.
SIGNOFF = (
    ("magic__drc_error__count", "Magic DRC errors"),
    ("klayout__drc_error__count", "KLayout DRC errors"),
    ("route__drc_errors", "detailed-routing DRC errors"),
    ("magic__illegal_overlap__count", "illegal overlaps"),
    ("design__xor_difference__count", "Magic/KLayout stream-out XOR differences"),
    ("design__lvs_error__count", "LVS errors"),
    ("design__power_grid_violation__count", "power grid violations"),
    ("design__critical_disconnected_pin__count", "critical disconnected pins"),
    ("timing__setup_vio__count", "setup violations"),
    ("timing__hold_vio__count", "hold violations"),
    ("design__max_slew_violation__count", "max slew violations"),
    ("design__max_cap_violation__count", "max cap violations"),
)

#: Reported, never fatal: neither LibreLane nor TT's precheck stops on these.
ADVISORY = (
    ("route__antenna_violation__count", "antenna violations after routing"),
)

# The precheck locates info.yaml by walking up from the GDS and reads only
# these keys from it for a digital project. The real info.yaml belongs to
# the submission repository; this one exists so the precheck can run here.
INFO_YAML = """\
# Written by scripts/tt_precheck.py for a local run of TT's precheck, which
# reads project.top_module, project.tiles and project.analog_pins from it.
project:
  top_module: "{top}"
  tiles: "{tiles}"
  analog_pins: 0
  uses_3v3: false
pinout: {{}}
"""


def fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def shown(path: Path) -> str:
    """A path as the host sees it: relative to the project root this runs in.

    The container mounts the project somewhere else, so an absolute path
    printed here names a directory that does not exist outside it.
    """
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def package(run_dir: Path, top: str, out: Path) -> dict:
    """Copy the three files custom_gds takes into out/, under TT's names."""
    views = {
        "gds": run_dir / "gds" / f"{top}.gds",
        "lef": run_dir / "lef" / f"{top}.lef",
        "v": run_dir / "nl" / f"{top}.nl.v",
    }
    missing = [shown(path) for path in views.values() if not path.is_file()]
    if missing:
        fail("the run has no " + ", ".join(missing)
             + "\n       harden it first (make pnr_simple, or make pnr).")

    # Wiped so no file from an earlier package can survive into this one --
    # but only if everything in it is something this script writes, so a
    # mistyped --out cannot take a run directory with it.
    owned = {f"{top}.{ext}" for ext in views} | {"precheck", "tt_precheck.json"}
    if out.is_dir():
        stray = sorted(p.name for p in out.iterdir() if p.name not in owned)
        if stray:
            fail(f"refusing to wipe {shown(out)}: it holds "
                 f"{', '.join(stray)}, which this script did not write.")
        shutil.rmtree(out)
    out.mkdir(parents=True)

    packaged = {}
    for ext, source in views.items():
        packaged[ext] = out / f"{top}.{ext}"
        shutil.copyfile(source, packaged[ext])
    return packaged


def signoff(run_dir: Path) -> tuple:
    """(signoff rows, advisory rows), each row (key, label, value, clean)."""
    metrics_file = run_dir / "metrics.json"
    if not metrics_file.is_file():
        fail(f"the run has no {shown(metrics_file)} to sign off against.")
    metrics = json.loads(metrics_file.read_text())

    def rows(table):
        out = []
        for key, label in table:
            value = metrics.get(key)
            out.append((key, label, value, value is not None and value == 0))
        return out

    return rows(SIGNOFF), rows(ADVISORY)


def results(report_dir: Path) -> list:
    """(check, error message or None) for every check the precheck ran."""
    xml = report_dir / "results.xml"
    if not xml.is_file():
        return []
    rows = []
    for case in ET.parse(xml).getroot().iter("testcase"):
        error = case.find("error")
        rows.append((case.get("name"),
                     None if error is None else error.get("message")))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package a hardened run for TinyTapeout's custom_gds "
        "submission, check its signoff metrics and run TT's precheck on it.")
    parser.add_argument("run_dir", type=Path,
                        help="a saved hardening run: flow/pnr_simple or flow/7_pnr")
    parser.add_argument("--out", type=Path, default=None,
                        help="where the package goes (default: RUN_DIR/tt_submission)")
    parser.add_argument("--top", default="tt_um_aion",
                        help="top module, which names every file (default: %(default)s)")
    parser.add_argument("--tiles", default="4x2",
                        help="tile size the precheck picks its DEF template by "
                        "(default: %(default)s)")
    parser.add_argument("--tt-tools", type=Path, required=True,
                        help="checkout of TinyTapeout/tt-support-tools")
    parser.add_argument("--work", type=Path, required=True,
                        help="scratch directory for the precheck; wiped first")
    parser.add_argument("--pdk", default=os.environ.get("PDK") or "ihp-sg13g2")
    parser.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT"))
    args = parser.parse_args()

    if not args.pdk_root or shutil.which("klayout") is None:
        fail("no PDK_ROOT or no klayout: this runs inside iic-osic-tools "
             "(make tt_precheck starts it there).")
    precheck_src = args.tt_tools / "precheck" / "precheck.py"
    if not precheck_src.is_file():
        fail(f"no {precheck_src} -- `make tt_precheck` fetches tt-support-tools.")

    run_dir = args.run_dir.resolve()
    out = (args.out if args.out is not None else run_dir / "tt_submission").resolve()
    submission = package(run_dir, args.top, out)
    print(f"[tt_precheck] package: {shown(out)}")
    for path in submission.values():
        print(f"  {path.name}")

    # ------------------------------------------------------------------
    # The run's own signoff, which the precheck cannot see
    # ------------------------------------------------------------------
    checked, advisory = signoff(run_dir)
    print(f"\n[tt_precheck] signoff: {shown(run_dir / 'metrics.json')}")
    for _, label, value, clean in checked:
        shown_value = "not reported" if value is None else value
        print(f"  {'PASS' if clean else 'FAIL'}  {label}: {shown_value}")
    for _, label, value, clean in advisory:
        shown_value = "not reported" if value is None else value
        print(f"  {'PASS' if clean else 'WARN'}  {label}: {shown_value}")
    signoff_clean = all(row[3] for row in checked)

    # ------------------------------------------------------------------
    # TT's precheck, on a private copy
    #
    # Next to the tech/ tree it reads the template DEF from (as
    # ../tech/<pdk>/def), and with a project directory whose info.yaml the
    # precheck finds by walking up from the GDS.
    # ------------------------------------------------------------------
    work = args.work.resolve()
    shutil.rmtree(work, ignore_errors=True)
    tools = work / "tt"
    shutil.copytree(args.tt_tools / "precheck", tools / "precheck",
                    ignore=shutil.ignore_patterns("reports"))
    shutil.copytree(args.tt_tools / "tech" / args.pdk, tools / "tech" / args.pdk)
    (tools / "precheck" / "reports").mkdir()
    project = work / "project"
    (project / "tt_submission").mkdir(parents=True)
    (project / "info.yaml").write_text(
        INFO_YAML.format(top=args.top, tiles=args.tiles))
    for path in submission.values():
        shutil.copyfile(path, project / "tt_submission" / path.name)

    gds = project / "tt_submission" / f"{args.top}.gds"
    print(f"\n[tt_precheck] precheck: {args.pdk}, {args.tiles} tile, "
          f"PDK_ROOT={args.pdk_root}")
    env = dict(os.environ, PDK=args.pdk, PDK_ROOT=args.pdk_root)
    start = time.time()
    status = subprocess.run(
        [sys.executable, "precheck.py", "--gds", str(gds), "--tech", args.pdk],
        cwd=tools / "precheck", env=env).returncode
    elapsed = time.time() - start

    reports = out / "precheck"
    shutil.copytree(tools / "precheck" / "reports", reports)

    rows = results(reports)
    print()
    print(f"[tt_precheck] {len(rows)} checks in {elapsed / 60:.1f} min "
          f"-- reports in {shown(reports)}")
    for name, error in rows:
        print(f"  {'PASS' if error is None else 'FAIL'}  {name}"
              + ("" if error is None else f": {error}"))
    if not rows:
        print("  the precheck wrote no results.xml -- read its log above.")
    precheck_clean = status == 0 and bool(rows)

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------
    stamps = sorted(args.tt_tools.glob(".fetched-*"))
    verdict = {
        "run": shown(run_dir),
        "top": args.top,
        "tiles": args.tiles,
        "pdk": args.pdk,
        "tt_support_tools": stamps[-1].name.removeprefix(".fetched-") if stamps else None,
        "signoff": [{"metric": key, "label": label, "value": value, "clean": clean}
                    for key, label, value, clean in checked],
        "advisory": [{"metric": key, "label": label, "value": value, "clean": clean}
                     for key, label, value, clean in advisory],
        "precheck": [{"check": name, "passed": error is None, "error": error}
                     for name, error in rows],
        "signoff_clean": signoff_clean,
        "precheck_clean": precheck_clean,
        "submittable": signoff_clean and precheck_clean,
    }
    (out / "tt_precheck.json").write_text(json.dumps(verdict, indent=2) + "\n")

    if not verdict["submittable"]:
        reasons = []
        if not signoff_clean:
            reasons.append("the run's signoff is not clean")
        if not precheck_clean:
            reasons.append("TT's precheck failed")
        print(f"\nNot submittable: {' and '.join(reasons)}.", file=sys.stderr)
        sys.exit(status or 1)
    print(f"\n[tt_precheck] {args.top} is submittable: signoff clean, TT's "
          f"precheck passed. Submit {shown(out)}/ through custom_gds.")


if __name__ == "__main__":
    main()
