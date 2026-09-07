#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Compare two hardened runs of the same design and
#                             say what the AI cells actually bought.
#
#  Two questions, and they have different answers:
#
#    1. The chip.     flow/7_pnr (AION cells) against flow/pnr_simple (PDK
#                     cells only).  Same RTL, same SDC, same die, same PDK --
#                     the substitution is the only variable, so the deltas
#                     are attributable to it.
#    2. The promise.  Step 3 predicts an area saving from AREA_FACTOR, which
#                     is an assumption made before anything was drawn.  Step 6
#                     draws the cells and their Liberty carries the real area.
#                     Those two numbers are not the same number, and the gap
#                     between them is the most useful line in this report.
#
#  Nothing here is measured by this script: every value is read from a
#  metrics.json LibreLane wrote or a Liberty the exporter wrote.  What is
#  added is the pairing, the direction of "better", and the refusal to
#  compare two runs that were not configured the same way.
# ================================================================

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# Site geometry, so an area can also be quoted in placement sites -- which is
# the unit a floorplan is actually built from, and the one that makes "8 sites
# instead of 9" legible where "14.5152 um2" is not.
from gds_to_image import ROW_HEIGHT_UM, SITE_WIDTH_UM

SITE_AREA_UM2 = SITE_WIDTH_UM * ROW_HEIGHT_UM

#: Corners, slowest first: the slow corner sets setup and the fast one sets
#: hold, so a single "worst slack" number hides which corner it came from.
CORNERS = ("nom_slow_1p08V_125C", "nom_typ_1p20V_25C", "nom_fast_1p32V_m40C")

#: Two runs are comparable only if these agree. A different die, clock or
#: synthesis strategy makes every delta below meaningless, so the report
#: refuses rather than printing numbers nobody should act on.
COMPARABLE_KEYS = ("DESIGN_NAME", "PDK", "STD_CELL_LIBRARY", "FP_SIZING",
                   "DIE_AREA", "CORE_AREA", "CLOCK_PERIOD",
                   "PL_TARGET_DENSITY_PCT", "SYNTH_STRATEGY")


@dataclass
class Metric:
    """One comparable number, and which direction counts as better."""

    key: str
    label: str
    unit: str = ""
    better: str = "lower"       # lower | higher | zero | none
    scale: float = 1.0
    digits: int = 1

    def value(self, metrics: dict) -> Optional[float]:
        raw = metrics.get(self.key)
        return None if raw is None else float(raw) * self.scale

    def show(self, value: Optional[float]) -> str:
        if value is None:
            return "—"
        return f"{value:,.{self.digits}f}"


AREA_METRICS = [
    Metric("design__instance__area__stdcell", "Standard cell area", "um²", digits=1),
    Metric("design__instance__utilization", "Core utilization", "%",
           scale=100.0, digits=2),
    Metric("design__instance__count", "Placed instances", "", digits=0),
    Metric("design__instance__count__class:fill_cell", "…of which fill", "",
           better="none", digits=0),
    Metric("design__instance__count__class:antenna_cell", "…of which antenna diodes",
           "", digits=0),
]

TIMING_METRICS = [
    Metric("timing__setup__ws", "Setup worst slack", "ns", better="higher", digits=3),
    Metric("timing__hold__ws", "Hold worst slack", "ns", better="higher", digits=3),
    Metric("timing__setup_vio__count", "Setup violations", "", better="zero", digits=0),
    Metric("timing__hold_vio__count", "Hold violations", "", better="zero", digits=0),
]

POWER_METRICS = [
    Metric("power__total", "Total power", "mW", scale=1000.0, digits=3),
    Metric("power__internal__total", "…internal", "mW", scale=1000.0, digits=3),
    Metric("power__switching__total", "…switching", "mW", scale=1000.0, digits=3),
    Metric("power__leakage__total", "…leakage", "uW", scale=1e6, digits=3),
]

ROUTE_METRICS = [
    Metric("route__wirelength", "Routed wirelength", "um", digits=0),
    Metric("global_route__wirelength", "Global route wirelength", "um", digits=0),
    Metric("route__drc_errors", "Router DRC errors", "", better="zero", digits=0),
    Metric("route__antenna_violation__count", "Antenna violations", "",
           better="zero", digits=0),
]

SIGNOFF_METRICS = [
    Metric("magic__drc_error__count", "Magic DRC", "", better="zero", digits=0),
    Metric("klayout__drc_error__count", "KLayout DRC", "", better="zero", digits=0),
    Metric("design__lvs_error__count", "LVS errors", "", better="zero", digits=0),
]

SECTIONS: List[Tuple[str, List[Metric]]] = [
    ("Area", AREA_METRICS),
    ("Timing", TIMING_METRICS),
    ("Power", POWER_METRICS),
    ("Routing", ROUTE_METRICS),
    ("Signoff", SIGNOFF_METRICS),
]

#: Deltas smaller than this are noise from placement seeds, not a result.
TIE_BAND_PCT = 0.5


# =====================================================================
# Reading a run
# =====================================================================
@dataclass
class Run:
    name: str
    directory: Path
    metrics: dict
    config: dict = field(default_factory=dict)

    @property
    def die_um(self) -> Optional[Tuple[float, float]]:
        die = self.config.get("DIE_AREA")
        if not die or len(die) != 4:
            return None
        return (die[2] - die[0], die[3] - die[1])


def load_run(directory: Path, name: str) -> Run:
    """A hardened run: its metrics, and the config LibreLane resolved for it."""
    directory = Path(directory)
    metrics_file = directory / "metrics.json"
    if not metrics_file.is_file():
        raise FileNotFoundError(
            f"{metrics_file} does not exist, so there is nothing to compare")
    metrics = json.loads(metrics_file.read_text())

    config = {}
    resolved = directory / "logs" / "resolved.json"
    if resolved.is_file():
        try:
            config = json.loads(resolved.read_text())
        except json.JSONDecodeError:
            config = {}
    return Run(name=name, directory=directory, metrics=metrics, config=config)


def comparability(aion: Run, baseline: Run) -> List[str]:
    """Configuration differences that invalidate a comparison.

    An empty list means the substitution really is the only variable between
    the two runs. EXTRA_LEFS and EXTRA_LIBS differ by design -- that IS the
    substitution -- so they are not checked.
    """
    problems = []
    for key in COMPARABLE_KEYS:
        left, right = aion.config.get(key), baseline.config.get(key)
        if left is None and right is None:
            continue
        if left != right:
            problems.append(f"{key}: {aion.name}={left!r}, {baseline.name}={right!r}")
    return problems


# =====================================================================
# The promise: what step 3 predicted, against what step 6 drew
# =====================================================================
_CELL_RE = re.compile(r"^\s*cell\s*\(\s*\"?([A-Za-z_][\w$]*)\"?\s*\)\s*\{")
_AREA_RE = re.compile(r"^\s*area\s*:\s*([0-9.eE+-]+)\s*;")


def liberty_area(path: Path, cell: Optional[str] = None) -> Optional[float]:
    """The `area` of a cell in a Liberty file.

    Scoped to the cell group on purpose: a Liberty also carries `area`
    attributes on pins and on the library defaults, and the first one in the
    file is not the cell's.
    """
    inside = False
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    for line in text.splitlines():
        match = _CELL_RE.match(line)
        if match:
            inside = cell is None or match.group(1) == cell
            continue
        if inside:
            found = _AREA_RE.match(line)
            if found:
                return float(found.group(1))
    return None


@dataclass
class CellRow:
    """One AION cell: what it replaced, what was promised, what was drawn."""

    cell: str
    occurrences: int
    original_area: float        # the PDK cells it replaced, in total
    predicted_area: float       # step 3's estimate, in total
    actual_area: Optional[float] = None      # step 6's Liberty, in total
    published: bool = True

    @property
    def original_each(self) -> float:
        return self.original_area / self.occurrences if self.occurrences else 0.0

    @property
    def actual_each(self) -> Optional[float]:
        if self.actual_area is None or not self.occurrences:
            return None
        return self.actual_area / self.occurrences

    @property
    def predicted_saving(self) -> float:
        return self.original_area - self.predicted_area

    @property
    def actual_saving(self) -> Optional[float]:
        if self.actual_area is None:
            return None
        return self.original_area - self.actual_area


def cell_ledger(rewrite_report: Path, cells_dir: Path) -> Tuple[List[CellRow], dict]:
    """Per-cell promise-vs-delivery, plus the design-level totals."""
    report = json.loads(Path(rewrite_report).read_text())
    rows: List[CellRow] = []

    for pattern in report.get("patterns", []):
        cell = pattern["module_name"]
        lib = Path(cells_dir) / cell / f"{cell}.lib"
        each = liberty_area(lib, cell)
        occurrences = int(pattern["occurrences"])
        rows.append(CellRow(
            cell=cell,
            occurrences=occurrences,
            original_area=float(pattern["total_original_area"]),
            predicted_area=float(pattern["total_new_area"]),
            actual_area=None if each is None else each * occurrences,
            published=each is not None,
        ))
    rows.sort(key=lambda r: -r.original_area)

    summary = report.get("summary", {})
    original_total = float(summary.get("original_total_area", 0.0))
    replaced = sum(r.original_area for r in rows)
    predicted = sum(r.predicted_area for r in rows)
    drawn = [r for r in rows if r.actual_area is not None]
    actual = sum(r.actual_area for r in drawn) if drawn else None

    totals = {
        "area_factor": report.get("parameters", {}).get("area_factor"),
        "occurrences": sum(r.occurrences for r in rows),
        "cells": len(rows),
        "cells_published": len(drawn),
        "original_cell_count": summary.get("original_cells"),
        "rewritten_cell_count": summary.get("rewritten_cells"),
        "original_total_area": original_total,
        "replaced_area": replaced,
        "predicted_area": predicted,
        "actual_area": actual,
        "predicted_saving": replaced - predicted,
        "actual_saving": None if actual is None else replaced - actual,
        "predicted_saving_pct": _pct(replaced - predicted, replaced),
        "actual_saving_pct": None if actual is None else _pct(replaced - actual, replaced),
        "predicted_design_saving_pct": _pct(replaced - predicted, original_total),
        "actual_design_saving_pct": (None if actual is None
                                     else _pct(replaced - actual, original_total)),
    }
    return rows, totals


def _pct(part: float, whole: float) -> Optional[float]:
    return None if not whole else 100.0 * part / whole


# =====================================================================
# Comparing
# =====================================================================
def delta(metric: Metric, aion: Optional[float],
          baseline: Optional[float]) -> dict:
    """One row of the comparison, with a verdict attached."""
    row = {
        "key": metric.key,
        "label": metric.label,
        "unit": metric.unit,
        "aion": aion,
        "baseline": baseline,
        "absolute": None,
        "percent": None,
        "verdict": "n/a",
    }
    if aion is None or baseline is None:
        return row

    row["absolute"] = aion - baseline
    row["percent"] = _pct(aion - baseline, abs(baseline)) if baseline else None

    if metric.better == "none":
        row["verdict"] = "info"
    elif metric.better == "zero":
        row["verdict"] = "clean" if aion == 0 else "fail"
    else:
        # Slack is compared in absolute nanoseconds: a percentage of a slack
        # is not a quantity anyone reasons about, and it explodes near zero.
        if metric.better == "higher":
            span = abs(aion - baseline)
            row["verdict"] = ("tie" if span < 1e-9
                              else "win" if aion > baseline else "loss")
        else:
            pct = row["percent"]
            if pct is None:
                row["verdict"] = "tie" if aion == baseline else (
                    "win" if aion < baseline else "loss")
            elif abs(pct) < TIE_BAND_PCT:
                row["verdict"] = "tie"
            else:
                row["verdict"] = "win" if pct < 0 else "loss"
    return row


#: Prefix LibreLane uses for its per-class area breakdown.
CLASS_AREA = "design__instance__area__class:"

#: Classes nothing in this flow substitutes. Their delta is a consequence of
#: the substitution, never a target of it.
COLLATERAL = ("antenna_cell", "timing_repair_buffer", "fill_cell")


def area_attribution(aion: Run, baseline: Run) -> List[dict]:
    """Where the standard cell area delta actually went, class by class.

    `design__instance__area__stdcell` moving by a quarter of a percent is a
    fact, not an explanation. The substitution only ever touches combinational
    cells; everything else in this table is what place-and-route decided to
    spend in response, and that is usually the larger number.
    """
    keys = sorted({k for k in (*aion.metrics, *baseline.metrics)
                   if k.startswith(CLASS_AREA)})
    rows = []
    for key in keys:
        left = float(aion.metrics.get(key, 0.0))
        right = float(baseline.metrics.get(key, 0.0))
        if left == right == 0.0:
            continue
        name = key[len(CLASS_AREA):]
        rows.append({
            "klass": name,
            "aion": left,
            "baseline": right,
            "absolute": left - right,
            "percent": _pct(left - right, right),
            "collateral": name in COLLATERAL,
        })
    rows.sort(key=lambda r: -abs(r["absolute"]))
    return rows


def compare(aion_dir: Path, baseline_dir: Path, rewrite_report: Optional[Path],
            cells_dir: Optional[Path], prefix: str = "AION_") -> dict:
    """Everything the report needs, as plain data."""
    aion = load_run(Path(aion_dir), "aion")
    baseline = load_run(Path(baseline_dir), "baseline")

    sections = []
    for title, metrics in SECTIONS:
        rows = [delta(m, m.value(aion.metrics), m.value(baseline.metrics))
                for m in metrics]
        sections.append({"title": title, "rows": rows})

    corners = []
    for corner in CORNERS:
        corners.append({
            "corner": corner,
            "setup": delta(Metric(f"timing__setup__ws__corner:{corner}",
                                  "Setup", "ns", better="higher", digits=3),
                           aion.metrics.get(f"timing__setup__ws__corner:{corner}"),
                           baseline.metrics.get(f"timing__setup__ws__corner:{corner}")),
            "hold": delta(Metric(f"timing__hold__ws__corner:{corner}",
                                 "Hold", "ns", better="higher", digits=3),
                          aion.metrics.get(f"timing__hold__ws__corner:{corner}"),
                          baseline.metrics.get(f"timing__hold__ws__corner:{corner}")),
        })

    record = {
        "design": aion.config.get("DESIGN_NAME") or baseline.config.get("DESIGN_NAME"),
        "prefix": prefix,
        "runs": {
            "aion": {"dir": str(aion.directory), "die_um": aion.die_um},
            "baseline": {"dir": str(baseline.directory), "die_um": baseline.die_um},
        },
        "fixed_die": aion.config.get("FP_SIZING") == "absolute",
        "clock_period_ns": aion.config.get("CLOCK_PERIOD"),
        "comparable": comparability(aion, baseline),
        "sections": sections,
        "corners": corners,
        "area_attribution": area_attribution(aion, baseline),
        "ledger": None,
        "ledger_totals": None,
    }

    if rewrite_report and Path(rewrite_report).is_file():
        rows, totals = cell_ledger(Path(rewrite_report),
                                   Path(cells_dir or "implementation/cells"))
        record["ledger"] = [vars(r) | {"original_each": r.original_each,
                                       "actual_each": r.actual_each,
                                       "predicted_saving": r.predicted_saving,
                                       "actual_saving": r.actual_saving}
                            for r in rows]
        record["ledger_totals"] = totals

    record["headline"] = headline(record)
    return record


#: The axes a reader actually decides on. Everything else is supporting detail.
HEADLINE_KEYS = ("design__instance__area__stdcell", "timing__setup__ws",
                 "power__total", "route__wirelength")


def headline(record: dict) -> dict:
    """The four numbers that decide whether the AI flow was worth it."""
    rows = {r["key"]: r for section in record["sections"] for r in section["rows"]}
    picked = [rows[k] for k in HEADLINE_KEYS if k in rows]
    wins = sum(1 for r in picked if r["verdict"] == "win")
    losses = sum(1 for r in picked if r["verdict"] == "loss")
    return {
        "rows": picked,
        "wins": wins,
        "losses": losses,
        "ties": len(picked) - wins - losses,
        "verdict": "win" if wins > losses else "loss" if losses > wins else "mixed",
    }


# =====================================================================
# Rendering
# =====================================================================
MARK = {"win": "**win**", "loss": "loss", "tie": "tie", "clean": "clean",
        "fail": "**FAIL**", "info": "—", "n/a": "—"}


def _short(path: str) -> str:
    """A path relative to the working directory, when that is shorter."""
    try:
        relative = os.path.relpath(path)
    except ValueError:                  # different drive on Windows
        return path
    return relative if not relative.startswith("..") else path


def _num(value: Optional[float], digits: int = 3) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _signed(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "—"
    return f"{value:+,.{digits}f}"


def _digits_for(unit: str) -> int:
    return 0 if unit in ("", "um") else 3 if unit in ("ns", "mW", "uW") else 2


def render_markdown(record: dict) -> str:
    """The report, as a document rather than a metric dump."""
    design = record.get("design") or "design"
    out: List[str] = []
    add = out.append

    add(f"# {design} — AION cells vs the PDK baseline")
    add("")
    add(_verdict_paragraph(record))
    add("")

    # ---- what is being compared -------------------------------------
    add("## What is being compared")
    add("")
    add("| | AION run | PDK baseline |")
    add("|---|---|---|")
    add(f"| Directory | `{_short(record['runs']['aion']['dir'])}` "
        f"| `{_short(record['runs']['baseline']['dir'])}` |")
    die = record["runs"]["aion"]["die_um"]
    if die:
        add(f"| Die | {die[0]:g} x {die[1]:g} um | {die[0]:g} x {die[1]:g} um |")
    if record.get("clock_period_ns"):
        add(f"| Clock period | {record['clock_period_ns']} ns "
            f"| {record['clock_period_ns']} ns |")
    add("")
    if record["comparable"]:
        add("> **These two runs are not configured the same way.** Every delta "
            "below mixes the cell substitution with a configuration change, so "
            "none of it is attributable:")
        add("")
        for problem in record["comparable"]:
            add(f"> - {problem}")
        add("")
    else:
        add("Same RTL, same SDC, same die, same PDK, same synthesis strategy. "
            "The cell substitution is the only variable, so the deltas below "
            "are attributable to it.")
        add("")
    if record.get("fixed_die"):
        add("`FP_SIZING` is `absolute`, so **the die is pinned and is not a "
            "result**. An area saving shows up as lower utilization and more "
            "fill, never as a smaller chip.")
        add("")

    # ---- headline ----------------------------------------------------
    add("## Headline")
    add("")
    add("| Metric | AION | Baseline | Delta | |")
    add("|---|---:|---:|---:|---|")
    for row in record["headline"]["rows"]:
        digits = _digits_for(row["unit"])
        unit = f" ({row['unit']})" if row["unit"] else ""
        pct = "" if row["percent"] is None else f" ({row['percent']:+.2f}%)"
        add(f"| {row['label']}{unit} | {_num(row['aion'], digits)} "
            f"| {_num(row['baseline'], digits)} "
            f"| {_signed(row['absolute'], digits)}{pct} | {MARK[row['verdict']]} |")
    add("")

    # ---- the full tables ---------------------------------------------
    add("## The chip, metric by metric")
    add("")
    for section in record["sections"]:
        add(f"### {section['title']}")
        add("")
        add("| Metric | AION | Baseline | Delta | |")
        add("|---|---:|---:|---:|---|")
        for row in section["rows"]:
            digits = _digits_for(row["unit"])
            unit = f" {row['unit']}" if row["unit"] else ""
            pct = "" if row["percent"] is None else f" ({row['percent']:+.2f}%)"
            add(f"| {row['label']}{unit} | {_num(row['aion'], digits)} "
                f"| {_num(row['baseline'], digits)} "
                f"| {_signed(row['absolute'], digits)}{pct} | {MARK[row['verdict']]} |")
        add("")

    # ---- where the area went -------------------------------------------
    if record.get("area_attribution"):
        out.extend(_attribution_markdown(record))

    # ---- corners ------------------------------------------------------
    add("### Slack by corner")
    add("")
    add("Setup is set by the slow corner and hold by the fast one, so a single "
        "worst-slack number hides where it came from.")
    add("")
    add("| Corner | Setup AION | Setup base | Hold AION | Hold base |")
    add("|---|---:|---:|---:|---:|")
    for entry in record["corners"]:
        add(f"| `{entry['corner']}` "
            f"| {_num(entry['setup']['aion'])} | {_num(entry['setup']['baseline'])} "
            f"| {_num(entry['hold']['aion'])} | {_num(entry['hold']['baseline'])} |")
    add("")

    # ---- promise vs delivery ------------------------------------------
    if record.get("ledger_totals"):
        out.extend(_ledger_markdown(record))

    # ---- caveats -------------------------------------------------------
    add("## What this does not say")
    add("")
    add("- Every number above is read from a `metrics.json` LibreLane wrote or "
        "a Liberty the cell exporter wrote. Nothing is re-measured here.")
    add("- Placement and routing are seeded heuristics. Deltas under "
        f"{TIE_BAND_PCT}% are reported as ties because a re-run moves them by "
        "about that much on its own.")
    add("- The AI cells are characterized at one corner (`LAYOUT_CORNERS=typ`) "
        "and LibreLane reads that Liberty into all three STA corners, so slow "
        "and fast slack for paths through an AION cell rest on typ data.")
    add("- Power is a switching-activity estimate, not a measurement.")
    add("")
    return "\n".join(out)


def _attribution_markdown(record: dict) -> List[str]:
    """Which cell classes moved, and which of them anyone aimed at."""
    rows = record["area_attribution"]
    out: List[str] = []
    add = out.append

    add("### Where the standard cell area went")
    add("")
    add("The substitution only ever replaces combinational cells. Every other "
        "row is what place-and-route spent in response to it — and on a fixed "
        "die, fill absorbs whatever is left over, so its delta is the mirror "
        "of everything above it rather than a result.")
    add("")
    add("| Cell class | AION | Baseline | Delta | |")
    add("|---|---:|---:|---:|---|")
    for row in rows:
        pct = "" if row["percent"] is None else f" ({row['percent']:+.2f}%)"
        # A class that did not move is evidence the comparison is controlled,
        # so it stays in the table -- but calling it "substituted" would be a
        # lie about what the flow touched.
        tag = ("unchanged" if row["absolute"] == 0 else
               "collateral" if row["collateral"] else "**substituted**")
        add(f"| `{row['klass']}` | {_num(row['aion'], 1)} "
            f"| {_num(row['baseline'], 1)} "
            f"| {_signed(row['absolute'], 1)}{pct} | {tag} |")
    add("")

    aimed = sum(r["absolute"] for r in rows
                if not r["collateral"] and r["klass"] != "fill_cell")
    spent = sum(r["absolute"] for r in rows
                if r["collateral"] and r["klass"] != "fill_cell")
    if aimed or spent:
        verb = "saved" if aimed < 0 else "cost"
        add(f"The cells the flow set out to replace {verb} "
            f"{abs(aimed):,.1f} um². Place-and-route then "
            f"{'spent' if spent > 0 else 'gave back'} {abs(spent):,.1f} um² on "
            "antenna diodes and repair buffers"
            + (" — more than the substitution saved, which is why the chip "
               "ends up larger."
               if spent > 0 and aimed < 0 and spent > -aimed else ".")
            )
        add("")
    return out


def _ledger_markdown(record: dict) -> List[str]:
    totals = record["ledger_totals"]
    out: List[str] = []
    add = out.append

    add("## The promise and the delivery")
    add("")
    factor = totals.get("area_factor")
    add(f"Step 3 estimated the area of a merged cell as `AREA_FACTOR`"
        f"{f' = {factor}' if factor else ''} times the cells it replaces. That "
        "is an assumption made before anything was drawn. Step 6 drew them, and "
        "their Liberty carries the real number.")
    add("")
    add("| Cell | Sites | x | PDK area replaced | Predicted | Drawn | "
        "Predicted saving | Real saving |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in record["ledger"]:
        sites = ("—" if row["actual_each"] is None
                 else f"{row['actual_each'] / SITE_AREA_UM2:.0f}")
        was = f"{row['original_each'] / SITE_AREA_UM2:.0f}"
        drawn = "not published" if row["actual_area"] is None else _num(row["actual_area"], 2)
        saving = ("—" if row["actual_saving"] is None else
                  f"{_num(row['actual_saving'], 2)} "
                  f"({_pct(row['actual_saving'], row['original_area']):.1f}%)")
        add(f"| `{row['cell']}` | {sites} of {was} | {row['occurrences']} "
            f"| {_num(row['original_area'], 2)} | {_num(row['predicted_area'], 2)} "
            f"| {drawn} "
            f"| {_num(row['predicted_saving'], 2)} "
            f"({_pct(row['predicted_saving'], row['original_area']):.1f}%) "
            f"| {saving} |")
    add("")
    add(f"Across the {totals['occurrences']} substituted sites: predicted "
        f"{_num(totals['predicted_saving'], 1)} um² "
        f"({totals['predicted_saving_pct']:.1f}% of the area replaced), "
        + (f"delivered {_num(totals['actual_saving'], 1)} um² "
           f"({totals['actual_saving_pct']:.1f}%)."
           if totals["actual_saving"] is not None else
           "delivered: no cell has been published yet."))
    add("")
    # Cross-check: the ledger is arithmetic on Liberty areas, the attribution
    # is what OpenROAD reported about the placed chip. They are independent,
    # and agreement is the evidence that the cells reached the die intact.
    combinational = next((r for r in record.get("area_attribution", [])
                          if r["klass"] == "multi_input_combinational_cell"), None)
    if combinational is not None and totals.get("actual_saving") is not None:
        placed = -combinational["absolute"]
        gap = placed - totals["actual_saving"]
        if abs(gap) < 0.5:
            add(f"The hardened chip agrees: combinational cell area fell by "
                f"{placed:,.1f} um², the same number, so the cells reached the "
                "die at the size they were drawn and the resizer left them "
                "alone.")
        else:
            add(f"The hardened chip does not quite agree: combinational cell "
                f"area fell by {placed:,.1f} um² against the ledger's "
                f"{totals['actual_saving']:,.1f} um², a {gap:+,.1f} um² gap. "
                "The resizer swapped combinational cells the substitution "
                "never touched.")
        add("")

    if totals.get("actual_design_saving_pct") is not None:
        add(f"Against the whole synthesized design "
            f"({_num(totals['original_total_area'], 0)} um²) that is "
            f"{totals['predicted_design_saving_pct']:.2f}% predicted against "
            f"{totals['actual_design_saving_pct']:.2f}% delivered — and the "
            "hardened chip's standard cell area in the table above is the "
            "number that actually shipped, because the resizer, CTS and "
            "antenna insertion all ran afterwards.")
        add("")
    return out


def _verdict_paragraph(record: dict) -> str:
    head = record["headline"]
    rows = {r["key"]: r for r in head["rows"]}
    parts = []

    def phrase(key: str, noun: str, down: str, up: str,
               unit: str = "%", digits: int = 2) -> None:
        row = rows.get(key)
        if row is None:
            return
        value = row["absolute"] if unit != "%" else row["percent"]
        if value is None:
            return
        # An exact zero is a real answer, and "0.00% larger" is not how to
        # give it.
        if row["verdict"] == "tie":
            parts.append(f"{noun} is unchanged")
            return
        amount = f"{abs(value):.{digits}f}{'%' if unit == '%' else ' ' + unit}"
        parts.append(f"{noun} is {amount} {down if value < 0 else up}")

    phrase("design__instance__area__stdcell", "standard cell area",
           "smaller", "larger")
    phrase("timing__setup__ws", "setup slack", "less", "more",
           unit="ns", digits=3)
    phrase("power__total", "power", "lower", "higher")
    phrase("route__wirelength", "routed wirelength", "shorter", "longer")

    if not parts:
        return "Not enough metrics in common to compare the two runs."
    verdict = {"win": "comes out ahead", "loss": "comes out behind",
               "mixed": "is a wash"}[head["verdict"]]
    return (f"Against the same design hardened with PDK cells only, the AION "
            f"run {verdict}: " + ", ".join(parts) + ".")


# =====================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a hardened run against the PDK-only baseline.")
    parser.add_argument("aion", help="the AION run directory (flow/7_pnr)")
    parser.add_argument("baseline", help="the baseline run directory (flow/pnr_simple)")
    parser.add_argument("-o", "--outdir", help="write report.md and report.json here")
    parser.add_argument("--rewrite-report",
                        default="flow/3_rewrite/report/rewrite_report.json",
                        help="step 3's report, for the promise-vs-delivery ledger")
    parser.add_argument("--cells-dir", default="implementation/cells",
                        help="published cell views, for their real Liberty area")
    parser.add_argument("--prefix", default="AION_",
                        help="module prefix that marks an AI cell")

    args = parser.parse_args()
    try:
        record = compare(Path(args.aion), Path(args.baseline),
                         Path(args.rewrite_report), Path(args.cells_dir),
                         prefix=args.prefix)
    except Exception as exc:            # noqa: BLE001 - a CLI, not a library
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(record)
    if args.outdir:
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "report.md").write_text(markdown)
        (outdir / "report.json").write_text(json.dumps(record, indent=2) + "\n")
        written = ["report.md", "report.json"]
        try:
            from report_html import render_html
        except ImportError as exc:      # the page is a convenience, not the report
            print(f"Warning: no HTML page — {exc}", file=sys.stderr)
        else:
            (outdir / "report.html").write_text(render_html(record))
            written.append("report.html")
        print("Wrote " + ", ".join(f"{outdir / name}" for name in written))
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
