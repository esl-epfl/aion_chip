#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The step 9 comparison, as a page you can look at
#
#  Same record compare_runs.py renders to Markdown, rendered instead to one
#  self-contained HTML file: inline CSS, inline SVG, inline script, no CDN and
#  no network.  It is opened off the filesystem, so anything external would
#  simply not load.
#
#  The surface is the flow's own panel colour -- the same #18181c the cell and
#  die renders are drawn on -- so the report and the pictures beside it read
#  as one artifact rather than two.  Everything that carries data on top of it
#  was validated against that exact surface: the diverging blue/red pair
#  clears the CVD gate at dE 19.2 and the normal-vision floor at 29.0, and both
#  poles clear 3:1 contrast.
#
#  Two charts, and each is the form its data asks for:
#
#    area attribution   signed deltas around zero -> diverging bar, warm/cool
#                       poles, neutral zero rule.  This is the chart that says
#                       the substitution saved less than PnR spent back.
#    promise/delivery   one quantity measured twice per cell -> dumbbell, one
#                       hue in two shades.  The connector IS the shortfall.
#
#  The headline is four numbers, so it is four stat tiles and not a grouped
#  bar chart.  Status colour never travels alone: every verdict ships an icon
#  and the word.
# ================================================================

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------
# Tokens. The surfaces and inks are gds_to_image's panel palette; the data
# colours are the validated diverging pair and the reserved status steps.
CSS = """
:root {
  color-scheme: dark;
  --plane:     #0f0f12;
  --surface:   #18181c;
  --raised:    #1e1e23;
  --ink:       #ebebeb;
  --ink-2:     #a8a8b0;
  --muted:     #96969b;
  --grid:      #2c2c31;
  --axis:      #3a3a41;
  --border:    rgba(255,255,255,0.10);

  --saved:     #3987e5;
  --spent:     #e66767;
  --neutral:   #383835;
  --deemph:    #6e6e78;
  --promise:   #b7d3f6;
  --delivered: #2a78d6;

  --good:      #0ca30c;
  --warning:   #fab219;
  --critical:  #d03b3b;

  --sans: system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
}

* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 24px 96px;
  background: var(--plane); color: var(--ink);
  font: 15px/1.65 var(--sans);
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1040px; margin: 0 auto; }

/* ---- header ---- */
header { padding: 56px 0 8px; }
h1 { margin: 0 0 6px; font-size: 30px; font-weight: 620; letter-spacing: -0.015em; }
h1 .sub { color: var(--muted); font-weight: 400; }
.meta { color: var(--muted); font-size: 13px; }
.meta code { font-family: var(--mono); font-size: 12px; color: var(--ink-2); }

.lede {
  margin: 22px 0 0; padding: 18px 20px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  font-size: 17px; line-height: 1.55; color: var(--ink);
}
.lede b { font-weight: 620; }

h2 {
  margin: 52px 0 4px; font-size: 13px; font-weight: 600;
  letter-spacing: 0.09em; text-transform: uppercase; color: var(--muted);
}
h2 + .note { margin-top: 10px; }
h3 { margin: 30px 0 10px; font-size: 16px; font-weight: 600; }
p  { margin: 12px 0; color: var(--ink-2); }
.note { color: var(--muted); font-size: 13.5px; margin: 8px 0 18px; }

/* ---- banners ---- */
.banner {
  display: flex; gap: 12px; align-items: flex-start;
  margin: 18px 0; padding: 14px 16px; border-radius: 10px;
  background: var(--surface); border: 1px solid var(--border);
  border-left: 3px solid var(--axis); font-size: 14px; color: var(--ink-2);
}
.banner.good     { border-left-color: var(--good); }
.banner.critical { border-left-color: var(--critical); }
.banner .icon { flex: none; font-weight: 700; line-height: 1.5; }
.banner.good     .icon { color: var(--good); }
.banner.critical .icon { color: var(--critical); }
.banner ul { margin: 8px 0 0; padding-left: 18px; }
.banner code { font-family: var(--mono); font-size: 12.5px; color: var(--ink); }

/* ---- stat tiles ---- */
.tiles {
  display: grid; gap: 12px; margin: 20px 0 8px;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
}
.tile {
  padding: 16px 18px 15px; background: var(--surface);
  border: 1px solid var(--border); border-radius: 10px;
}
.tile .label { color: var(--muted); font-size: 12.5px; margin-bottom: 10px; }
.tile .value { font-size: 30px; font-weight: 600; letter-spacing: -0.02em; line-height: 1.1; }
.tile .value .unit { font-size: 14px; font-weight: 400; color: var(--muted); margin-left: 4px; }
.tile .delta { margin-top: 8px; font-size: 13.5px; color: var(--ink-2); }
.tile .foot  { margin-top: 3px; font-size: 12.5px; color: var(--muted); }

/* ---- status chip: icon + word, never colour alone ---- */
.chip {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; font-weight: 600; letter-spacing: 0.02em;
}
.chip .dot { font-size: 11px; }
.chip.win, .chip.clean { color: var(--good); }
.chip.loss, .chip.fail { color: var(--critical); }
.chip.tie, .chip.info, .chip.na { color: var(--muted); font-weight: 500; }

/* ---- figures ---- */
figure {
  margin: 18px 0 8px; padding: 20px 20px 14px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
}
figure svg { display: block; width: 100%; height: auto; overflow: visible; }
figcaption { margin-top: 14px; color: var(--muted); font-size: 13px; }
.legend { display: flex; flex-wrap: wrap; gap: 18px; margin-bottom: 16px; }
.legend span { display: inline-flex; align-items: center; gap: 7px;
               font-size: 13px; color: var(--ink-2); }
.legend i { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }

/* ---- shots ---- */
.shots { display: grid; gap: 14px; margin: 18px 0; }
.shots img { width: 100%; display: block; border-radius: 8px; border: 1px solid var(--border); }
.shots .cap { color: var(--muted); font-size: 12.5px; margin-top: -4px; }

/* ---- tables ---- */
.scroll { overflow-x: auto; margin: 14px 0 6px; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
caption { text-align: left; color: var(--muted); font-size: 13px; padding-bottom: 10px; }
th, td { padding: 9px 12px; text-align: right; white-space: nowrap; }
th:first-child, td:first-child { text-align: left; white-space: normal; }
thead th {
  color: var(--muted); font-weight: 500; font-size: 12px;
  letter-spacing: 0.05em; text-transform: uppercase;
  border-bottom: 1px solid var(--axis);
}
tbody tr { border-bottom: 1px solid var(--grid); }
tbody tr:last-child { border-bottom: 0; }
tbody td { font-variant-numeric: tabular-nums; color: var(--ink-2); }
tbody td:first-child { color: var(--ink); font-variant-numeric: normal; }
tbody tr.total { border-top: 1px solid var(--axis); }
tbody tr.total td { color: var(--ink); font-weight: 600; }
td code, th code { font-family: var(--mono); font-size: 12.5px; }
.swatch { display: inline-block; width: 9px; height: 9px; border-radius: 2px;
          margin-right: 8px; vertical-align: 1px; }

/* ---- footer ---- */
footer { margin-top: 56px; padding-top: 22px; border-top: 1px solid var(--grid);
         color: var(--muted); font-size: 13px; }
footer ul { padding-left: 18px; margin: 10px 0 0; }
footer li { margin: 7px 0; }

/* ---- tooltip ---- */
#tip {
  position: fixed; z-index: 20; pointer-events: none; opacity: 0;
  transition: opacity .09s; padding: 8px 11px; border-radius: 7px;
  background: var(--raised); border: 1px solid var(--border);
  box-shadow: 0 6px 22px rgba(0,0,0,.55);
  font: 12.5px/1.5 var(--sans); color: var(--ink); white-space: nowrap;
}
#tip b { font-weight: 600; }
#tip .k { color: var(--muted); }
[data-tip] { cursor: default; }

@media (max-width: 640px) {
  h1 { font-size: 24px; }
  .lede { font-size: 15.5px; }
}
"""

SCRIPT = """
(function () {
  var tip = document.getElementById('tip');
  function show(e) {
    var t = e.currentTarget.getAttribute('data-tip');
    if (!t) return;
    tip.innerHTML = t;
    tip.style.opacity = '1';
    move(e);
  }
  function move(e) {
    var pad = 14, r = tip.getBoundingClientRect();
    var x = e.clientX + pad, y = e.clientY + pad;
    if (x + r.width  > window.innerWidth)  x = e.clientX - r.width  - pad;
    if (y + r.height > window.innerHeight) y = e.clientY - r.height - pad;
    tip.style.left = x + 'px';
    tip.style.top  = y + 'px';
  }
  function hide() { tip.style.opacity = '0'; }
  document.querySelectorAll('[data-tip]').forEach(function (el) {
    el.addEventListener('mouseenter', show);
    el.addEventListener('mousemove', move);
    el.addEventListener('mouseleave', hide);
  });
})();
"""

#: icon + word, so a verdict never rests on hue alone
CHIP = {"win": ("✓", "win"), "loss": ("✕", "loss"), "tie": ("—", "tie"),
        "clean": ("✓", "clean"), "fail": ("✕", "fail"),
        "info": ("", "—"), "n/a": ("", "—")}


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def _fmt(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _signed(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value:+,.{digits}f}"


def _digits(unit: str) -> int:
    return 0 if unit in ("", "um") else 3 if unit in ("ns", "mW", "uW") else 2


def chip(verdict: str) -> str:
    icon, word = CHIP.get(verdict, ("", verdict))
    mark = f'<span class="dot">{icon}</span>' if icon else ""
    return f'<span class="chip {esc(verdict)}">{mark}{esc(word)}</span>'


# =====================================================================
# Charts
# =====================================================================
def _bar(x: float, y: float, width: float, height: float,
         radius: float, grows: str) -> str:
    """A bar with a rounded data-end and a square baseline end."""
    radius = max(0.0, min(radius, abs(width)))
    if width <= 0.2:                    # too short to round; draw a sliver
        return (f'M{x:.2f},{y:.2f} h{width if grows == "right" else -width:.2f} '
                f'v{height:.2f} h{-width if grows == "right" else width:.2f} Z')
    if grows == "right":
        return (f"M{x:.2f},{y:.2f} H{x + width - radius:.2f} "
                f"a{radius:.2f},{radius:.2f} 0 0 1 {radius:.2f},{radius:.2f} "
                f"V{y + height - radius:.2f} "
                f"a{radius:.2f},{radius:.2f} 0 0 1 {-radius:.2f},{radius:.2f} "
                f"H{x:.2f} Z")
    return (f"M{x:.2f},{y:.2f} H{x - width + radius:.2f} "
            f"a{radius:.2f},{radius:.2f} 0 0 0 {-radius:.2f},{radius:.2f} "
            f"V{y + height - radius:.2f} "
            f"a{radius:.2f},{radius:.2f} 0 0 0 {radius:.2f},{radius:.2f} "
            f"H{x:.2f} Z")


def _nice(value: float) -> float:
    """A round axis bound at or above `value`."""
    if value <= 0:
        return 1.0
    import math
    power = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * power:
            return step * power
    return 10 * power


def attribution_chart(rows: List[dict]) -> str:
    """Diverging bar: what the substitution saved, what PnR spent back."""
    rows = [r for r in rows if r["absolute"] != 0]
    if not rows:
        return ""

    # The name gutter, the plot, and a lane on each side wide enough for a tip
    # label -- so a full-length bar's value never lands on the class name or
    # runs off the card.
    gutter, lane, right_pad = 240, 62, 70
    width, row_h, bar_h = 900, 34, 18
    top = 26
    height = top + row_h * len(rows) + 34
    plot_w = width - gutter - lane - right_pad
    bound = _nice(max(abs(r["absolute"]) for r in rows))
    zero = gutter + lane + plot_w / 2
    scale = (plot_w / 2) / bound

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="Standard cell area delta by cell class">']

    # hairline grid, solid, recessive
    for frac in (-1.0, -0.5, 0.0, 0.5, 1.0):
        x = zero + frac * bound * scale
        if frac:
            parts.append(f'<line x1="{x:.1f}" y1="{top - 12}" x2="{x:.1f}" '
                         f'y2="{top + row_h * len(rows) + 2}" '
                         f'stroke="var(--grid)" stroke-width="1"/>')
        tick = "0" if not frac else f"{frac * bound:+,.0f}"
        parts.append(f'<text x="{x:.1f}" y="{height - 12}" fill="var(--muted)" '
                     f'font-size="11" text-anchor="middle" '
                     f'style="font-variant-numeric:tabular-nums">{tick}</text>')

    for index, row in enumerate(rows):
        y = top + index * row_h
        span = abs(row["absolute"]) * scale
        spent = row["absolute"] > 0
        # Fill is whatever is left over on a pinned die. Painting it the same
        # blue as a real saving would claim a result nobody achieved.
        colour = ("var(--deemph)" if row["klass"] == "fill_cell"
                  else "var(--spent)" if spent else "var(--saved)")
        bar_y = y + (row_h - bar_h) / 2 - 4
        path = _bar(zero, bar_y, span, bar_h, 4, "right" if spent else "left")

        tag = ("collateral" if row["collateral"]
               else "substituted")
        tip = (f'<b>{esc(row["klass"])}</b><br>'
               f'<span class="k">AION</span> {_fmt(row["aion"], 1)} um²<br>'
               f'<span class="k">baseline</span> {_fmt(row["baseline"], 1)} um²<br>'
               f'<span class="k">delta</span> {_signed(row["absolute"], 1)} um² '
               f'({tag})')

        parts.append(f'<g data-tip="{esc(tip)}">')
        parts.append(f'<rect x="0" y="{y - 4}" width="{width}" height="{row_h}" '
                     f'fill="transparent"/>')
        parts.append(f'<text x="{gutter - 16}" y="{bar_y + bar_h - 4:.1f}" '
                     f'fill="var(--ink-2)" font-size="13" text-anchor="end">'
                     f'{esc(row["klass"])}</text>')
        parts.append(f'<path d="{path}" fill="{colour}"/>')
        label_x = zero + (span + 10) * (1 if spent else -1)
        parts.append(f'<text x="{label_x:.1f}" y="{bar_y + bar_h - 4:.1f}" '
                     f'fill="var(--ink-2)" font-size="12.5" '
                     f'text-anchor="{"start" if spent else "end"}" '
                     f'style="font-variant-numeric:tabular-nums">'
                     f'{_signed(row["absolute"], 1)}</text>')
        parts.append("</g>")

    # the zero rule sits above the bars: it is the thing they are read against
    parts.append(f'<line x1="{zero:.1f}" y1="{top - 12}" x2="{zero:.1f}" '
                 f'y2="{top + row_h * len(rows) + 2}" stroke="var(--axis)" '
                 f'stroke-width="1"/>')
    parts.append("</svg>")
    return "".join(parts)


def dumbbell_chart(rows: List[dict], totals: dict) -> str:
    """Predicted saving against delivered saving, per cell. The gap is the point."""
    entries = [(r["cell"], r["predicted_saving"], r["actual_saving"])
               for r in rows if r["actual_saving"] is not None]
    if not entries:
        return ""
    if totals.get("actual_saving") is not None and len(entries) > 1:
        entries.append(("all substituted sites", totals["predicted_saving"],
                        totals["actual_saving"]))

    gutter, right_pad = 250, 140
    width, row_h = 900, 44
    top = 24
    height = top + row_h * len(entries) + 30
    plot_w = width - gutter - right_pad
    bound = _nice(max(max(p, a) for _, p, a in entries))
    scale = plot_w / bound

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="Predicted against delivered area saving per cell">']
    for step in range(5):
        value = bound * step / 4
        x = gutter + value * scale
        parts.append(f'<line x1="{x:.1f}" y1="{top - 10}" x2="{x:.1f}" '
                     f'y2="{top + row_h * len(entries) - 8}" '
                     f'stroke="var(--grid)" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{height - 10}" fill="var(--muted)" '
                     f'font-size="11" text-anchor="middle" '
                     f'style="font-variant-numeric:tabular-nums">'
                     f'{value:,.0f}</text>')

    for index, (name, predicted, delivered) in enumerate(entries):
        y = top + index * row_h + 8
        x1 = gutter + delivered * scale
        x2 = gutter + predicted * scale
        share = 100.0 * delivered / predicted if predicted else 0.0
        tip = (f'<b>{esc(name)}</b><br>'
               f'<span class="k">predicted</span> {_fmt(predicted, 1)} um²<br>'
               f'<span class="k">delivered</span> {_fmt(delivered, 1)} um²<br>'
               f'<span class="k">that is</span> {share:.0f}% of the promise')

        parts.append(f'<g data-tip="{esc(tip)}">')
        parts.append(f'<rect x="0" y="{y - 20}" width="{width}" height="{row_h}" '
                     f'fill="transparent"/>')
        parts.append(f'<text x="{gutter - 16}" y="{y + 4}" fill="var(--ink-2)" '
                     f'font-size="13" text-anchor="end">{esc(name)}</text>')
        parts.append(f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" '
                     f'stroke="var(--axis)" stroke-width="2" stroke-linecap="round"/>')
        # Hollow for the promise, solid for what shipped: shape carries the
        # before/after too, so the pair does not rest on two shades of one hue.
        # The surface ring keeps both legible where they nearly touch.
        parts.append(f'<circle cx="{x2:.1f}" cy="{y:.1f}" r="5" '
                     f'fill="var(--surface)" stroke="var(--promise)" '
                     f'stroke-width="2.5"/>')
        parts.append(f'<circle cx="{x1:.1f}" cy="{y:.1f}" r="5.5" '
                     f'fill="var(--delivered)" stroke="var(--surface)" '
                     f'stroke-width="2"/>')
        parts.append(f'<text x="{x2 + 16:.1f}" y="{y + 4:.1f}" fill="var(--ink-2)" '
                     f'font-size="12.5">'
                     f'<tspan style="font-variant-numeric:tabular-nums">'
                     f'{share:.0f}%</tspan> delivered</text>')
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


def legend(items: List[tuple], hollow_first: bool = False) -> str:
    """Swatches beside the labels. Identity never rides on the text colour."""
    swatches = []
    for index, (label, colour) in enumerate(items):
        if hollow_first and index == 0:
            style = (f"background:var(--surface);box-shadow:inset 0 0 0 2.5px "
                     f"{colour};border-radius:50%")
        else:
            style = f"background:{colour}" + (";border-radius:50%"
                                              if hollow_first else "")
        swatches.append(f'<span><i style="{style}"></i>{esc(label)}</span>')
    return f'<div class="legend">{"".join(swatches)}</div>'


# =====================================================================
# Tables
# =====================================================================
def metric_table(rows: List[dict], caption: str = "") -> str:
    body = []
    for row in rows:
        digits = _digits(row["unit"])
        unit = f' <span style="color:var(--muted)">{esc(row["unit"])}</span>' \
            if row["unit"] else ""
        pct = "" if row["percent"] is None else f' ({row["percent"]:+.2f}%)'
        body.append(
            f'<tr><td>{esc(row["label"])}{unit}</td>'
            f'<td>{_fmt(row["aion"], digits)}</td>'
            f'<td>{_fmt(row["baseline"], digits)}</td>'
            f'<td>{_signed(row["absolute"], digits)}{pct}</td>'
            f'<td>{chip(row["verdict"])}</td></tr>')
    head = f"<caption>{esc(caption)}</caption>" if caption else ""
    return (f'<div class="scroll"><table>{head}<thead><tr>'
            f"<th>Metric</th><th>AION</th><th>Baseline</th><th>Delta</th><th></th>"
            f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


def tiles(rows: List[dict]) -> str:
    out = []
    for row in rows:
        digits = _digits(row["unit"])
        pct = "" if row["percent"] is None else f" ({row['percent']:+.2f}%)"
        out.append(
            f'<div class="tile">'
            f'<div class="label">{esc(row["label"])}</div>'
            f'<div class="value">{_fmt(row["aion"], digits)}'
            f'<span class="unit">{esc(row["unit"])}</span></div>'
            f'<div class="delta">{_signed(row["absolute"], digits)}{pct} '
            f'&nbsp;{chip(row["verdict"])}</div>'
            f'<div class="foot">baseline {_fmt(row["baseline"], digits)}</div>'
            f"</div>")
    return f'<div class="tiles">{"".join(out)}</div>'


def corner_table(corners: List[dict]) -> str:
    body = []
    for entry in corners:
        body.append(
            f'<tr><td><code>{esc(entry["corner"])}</code></td>'
            f'<td>{_fmt(entry["setup"]["aion"], 3)}</td>'
            f'<td>{_fmt(entry["setup"]["baseline"], 3)}</td>'
            f'<td>{_fmt(entry["hold"]["aion"], 3)}</td>'
            f'<td>{_fmt(entry["hold"]["baseline"], 3)}</td></tr>')
    return (f'<div class="scroll"><table><thead><tr><th>Corner</th>'
            f"<th>Setup AION</th><th>Setup base</th>"
            f"<th>Hold AION</th><th>Hold base</th></tr></thead>"
            f'<tbody>{"".join(body)}</tbody></table></div>')


def ledger_table(rows: List[dict], site_area: float) -> str:
    body = []
    for row in rows:
        sites = ("—" if row["actual_each"] is None
                 else f'{row["actual_each"] / site_area:.0f}')
        was = f'{row["original_each"] / site_area:.0f}'
        drawn = ("not published" if row["actual_area"] is None
                 else _fmt(row["actual_area"], 1))
        if row["actual_saving"] is None:
            real = "—"
        else:
            share = 100.0 * row["actual_saving"] / row["original_area"]
            real = f'{_fmt(row["actual_saving"], 1)} ({share:.1f}%)'
        predicted_share = 100.0 * row["predicted_saving"] / row["original_area"]
        body.append(
            f'<tr><td><code>{esc(row["cell"])}</code></td>'
            f'<td>{sites} of {was}</td>'
            f'<td>{row["occurrences"]}</td>'
            f'<td>{_fmt(row["original_area"], 1)}</td>'
            f'<td>{_fmt(row["predicted_area"], 1)}</td>'
            f"<td>{drawn}</td>"
            f'<td>{_fmt(row["predicted_saving"], 1)} ({predicted_share:.1f}%)</td>'
            f"<td>{real}</td></tr>")
    return (f'<div class="scroll"><table><thead><tr><th>Cell</th><th>Sites</th>'
            f"<th>Placed</th><th>PDK area</th><th>Predicted</th><th>Drawn</th>"
            f"<th>Predicted saving</th><th>Real saving</th>"
            f'</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def attribution_table(rows: List[dict]) -> str:
    body = []
    for row in rows:
        if row["absolute"] == 0:
            colour, tag = "var(--deemph)", "unchanged"
        elif row["klass"] == "fill_cell":
            colour, tag = "var(--deemph)", "remainder"
        elif row["collateral"]:
            colour, tag = ("var(--spent)" if row["absolute"] > 0
                           else "var(--saved)"), "collateral"
        else:
            colour, tag = ("var(--spent)" if row["absolute"] > 0
                           else "var(--saved)"), "substituted"
        pct = "" if row["percent"] is None else f' ({row["percent"]:+.2f}%)'
        body.append(
            f'<tr><td><span class="swatch" style="background:{colour}"></span>'
            f'<code>{esc(row["klass"])}</code></td>'
            f'<td>{_fmt(row["aion"], 1)}</td>'
            f'<td>{_fmt(row["baseline"], 1)}</td>'
            f'<td>{_signed(row["absolute"], 1)}{pct}</td>'
            f'<td><span class="chip info">{esc(tag)}</span></td></tr>')
    return (f'<div class="scroll"><table><thead><tr><th>Cell class</th>'
            f"<th>AION</th><th>Baseline</th><th>Delta</th><th></th>"
            f'</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


# =====================================================================
def render_html(record: dict, images: Optional[List[tuple]] = None,
                site_area: float = 0.48 * 3.78) -> str:
    """The whole report as one self-contained page."""
    design = record.get("design") or "design"
    runs = record["runs"]
    head = record["headline"]
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

    body: List[str] = ['<div class="wrap">']
    add = body.append

    # ---- header -------------------------------------------------------
    add("<header>")
    add(f'<h1>{esc(design)} <span class="sub">— AION cells vs the PDK '
        f"baseline</span></h1>")
    add(f'<div class="meta"><code>{esc(_short(runs["aion"]["dir"]))}</code> '
        f'against <code>{esc(_short(runs["baseline"]["dir"]))}</code>'
        f" &nbsp;·&nbsp; generated {esc(stamp)}</div>")
    add("</header>")
    add(f'<div class="lede">{_lede(record)}</div>')

    # ---- comparability -------------------------------------------------
    if record["comparable"]:
        items = "".join(f"<li><code>{esc(p)}</code></li>"
                        for p in record["comparable"])
        add('<div class="banner critical"><span class="icon">✕</span><div>'
            "<b>These two runs are not configured the same way.</b> Every delta "
            "below mixes the cell substitution with a configuration change, so "
            f"none of it is attributable.<ul>{items}</ul></div></div>")
    else:
        add('<div class="banner good"><span class="icon">✓</span><div>'
            "Same RTL, same SDC, same die, same PDK, same synthesis strategy. "
            "The cell substitution is the only variable, so every delta below "
            "is attributable to it.</div></div>")
    if record.get("fixed_die"):
        add('<div class="banner"><span class="icon">·</span><div>'
            "<code>FP_SIZING</code> is <code>absolute</code>, so the die is "
            "pinned and <b>is not a result</b>. An area saving shows up as "
            "lower utilization and more fill, never as a smaller chip.</div></div>")

    # ---- headline ------------------------------------------------------
    add("<h2>Headline</h2>")
    add(tiles(head["rows"]))
    add(f'<p class="note">{head["wins"]} win · {head["losses"]} loss · '
        f'{head["ties"]} tie. Values are the AION run; the delta is against the '
        f"baseline. A delta under 0.5% is called a tie, because placement is a "
        f"seeded heuristic and a re-run moves it by about that much.</p>")

    # ---- attribution ----------------------------------------------------
    attribution = record.get("area_attribution") or []
    if attribution:
        add("<h2>Where the standard cell area went</h2>")
        add("<p>The substitution only ever replaces combinational cells. "
            "Everything else is what place-and-route spent in response — and on "
            "a fixed die, fill absorbs the remainder, so its bar is the mirror "
            "of the others rather than a result.</p>")
        add("<figure>")
        add(legend([("saved (smaller than baseline)", "var(--saved)"),
                    ("spent (larger than baseline)", "var(--spent)"),
                    ("remainder — not a result", "var(--deemph)")]))
        add(attribution_chart(attribution))
        add(f"<figcaption>{_attribution_caption(attribution)}</figcaption>")
        add("</figure>")
        add(attribution_table(attribution))

    # ---- the chip -------------------------------------------------------
    add("<h2>The chip, metric by metric</h2>")
    for section in record["sections"]:
        add(f'<h3>{esc(section["title"])}</h3>')
        add(metric_table(section["rows"]))
    add("<h3>Slack by corner</h3>")
    add('<p class="note">Setup is set by the slow corner and hold by the fast '
        "one, so a single worst-slack number hides where it came from.</p>")
    add(corner_table(record["corners"]))

    # ---- promise vs delivery --------------------------------------------
    totals = record.get("ledger_totals")
    if totals and record.get("ledger"):
        add("<h2>The promise and the delivery</h2>")
        factor = totals.get("area_factor")
        add("<p>Step 3 estimated the area of a merged cell as "
            f"<code>AREA_FACTOR{f' = {factor}' if factor else ''}</code> times "
            "the cells it replaces — an assumption made before anything was "
            "drawn. Step 6 drew them, and their Liberty carries the real "
            "number.</p>")
        chart = dumbbell_chart(record["ledger"], totals)
        if chart:
            add("<figure>")
            add(legend([("predicted by AREA_FACTOR", "var(--promise)"),
                        ("delivered by the drawn cells", "var(--delivered)")],
                       hollow_first=True))
            add(chart)
            add(f"<figcaption>{_ledger_caption(totals)}</figcaption>")
            add("</figure>")
        add(ledger_table(record["ledger"], site_area))
        add(f"<p>{_ledger_prose(record, totals)}</p>")

    # ---- pictures --------------------------------------------------------
    if images:
        add("<h2>The die</h2>")
        add('<div class="shots">')
        for caption, source in images:
            add(f'<img src="{esc(source)}" alt="{esc(caption)}">')
            add(f'<div class="cap">{esc(caption)}</div>')
        add("</div>")

    # ---- caveats ----------------------------------------------------------
    add("<footer><b>What this does not say</b><ul>")
    add("<li>Every number here is read from a <code>metrics.json</code> "
        "LibreLane wrote or a Liberty the cell exporter wrote. Nothing is "
        "re-measured.</li>")
    add("<li>Placement and routing are seeded heuristics. Deltas under 0.5% are "
        "reported as ties because a re-run moves them by about that much.</li>")
    add("<li>The AI cells are characterized at one corner "
        "(<code>LAYOUT_CORNERS=typ</code>) and LibreLane reads that Liberty into "
        "all three STA corners, so slow and fast slack for paths through an "
        "AION cell rest on typ data.</li>")
    add("<li>Power is a switching-activity estimate, not a measurement.</li>")
    add("</ul></footer>")
    add("</div>")
    add('<div id="tip"></div>')

    return (f"<!doctype html>\n<html lang=\"en\">\n<head>\n"
            f'<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f"<title>{esc(design)} — AION vs PDK baseline</title>\n"
            f"<style>{CSS}</style>\n</head>\n<body>\n"
            + "\n".join(body)
            + f"\n<script>{SCRIPT}</script>\n</body>\n</html>\n")


def _short(path: str) -> str:
    import os
    try:
        relative = os.path.relpath(path)
    except ValueError:
        return path
    return relative if not relative.startswith("..") else path


def _lede(record: dict) -> str:
    head = record["headline"]
    verdict = {"win": "comes out ahead", "loss": "comes out behind",
               "mixed": "is a wash"}[head["verdict"]]
    rows = {r["key"]: r for r in head["rows"]}

    bits = []
    for key, noun, down, up, unit in (
            ("design__instance__area__stdcell", "standard cell area",
             "smaller", "larger", "%"),
            ("timing__setup__ws", "setup slack", "less", "more", "ns"),
            ("power__total", "power", "lower", "higher", "%"),
            ("route__wirelength", "routed wirelength", "shorter", "longer", "%")):
        row = rows.get(key)
        if row is None:
            continue
        value = row["percent"] if unit == "%" else row["absolute"]
        if value is None:
            continue
        if row["verdict"] == "tie":
            bits.append(f"{noun} is unchanged")
            continue
        amount = (f"{abs(value):.2f}%" if unit == "%"
                  else f"{abs(value):.3f} ns")
        bits.append(f"{noun} is <b>{amount} {down if value < 0 else up}</b>")
    if not bits:
        return "Not enough metrics in common to compare the two runs."
    return (f"Against the same design hardened with PDK cells only, the AION run "
            f"{verdict}: " + ", ".join(bits) + ".")


def _attribution_caption(rows: List[dict]) -> str:
    aimed = sum(r["absolute"] for r in rows
                if not r["collateral"] and r["klass"] != "fill_cell")
    spent = sum(r["absolute"] for r in rows
                if r["collateral"] and r["klass"] != "fill_cell")
    verb = "saved" if aimed < 0 else "cost"
    tail = (" — more than the substitution saved, which is why the chip ends up "
            "larger." if spent > 0 and aimed < 0 and spent > -aimed else ".")
    return (f"The cells the flow set out to replace {verb} {abs(aimed):,.1f} um². "
            f"Place-and-route then {'spent' if spent > 0 else 'gave back'} "
            f"{abs(spent):,.1f} um² on antenna diodes and repair buffers{tail}")


def _ledger_caption(totals: dict) -> str:
    if totals.get("actual_saving") is None:
        return "No cell has been published yet, so there is nothing to compare."
    share = (100.0 * totals["actual_saving"] / totals["predicted_saving"]
             if totals["predicted_saving"] else 0.0)
    return (f"Across {totals['occurrences']} substituted sites the drawn cells "
            f"delivered {share:.0f}% of what AREA_FACTOR promised: "
            f"{totals['actual_saving']:,.1f} um² against "
            f"{totals['predicted_saving']:,.1f} um².")


def _ledger_prose(record: dict, totals: dict) -> str:
    if totals.get("actual_saving") is None:
        return "No AION cell has been published, so the prediction stands untested."
    parts = []
    combinational = next((r for r in record.get("area_attribution", [])
                          if r["klass"] == "multi_input_combinational_cell"), None)
    if combinational is not None:
        placed = -combinational["absolute"]
        if abs(placed - totals["actual_saving"]) < 0.5:
            parts.append(
                f"The hardened chip agrees: combinational cell area fell by "
                f"{placed:,.1f} um², the same number, so the cells reached the "
                f"die at the size they were drawn and the resizer left them alone.")
        else:
            parts.append(
                f"The hardened chip does not quite agree: combinational cell area "
                f"fell by {placed:,.1f} um² against the ledger's "
                f"{totals['actual_saving']:,.1f} um². The resizer swapped "
                f"combinational cells the substitution never touched.")
    if totals.get("actual_design_saving_pct") is not None:
        parts.append(
            f"Against the whole synthesized design "
            f"({totals['original_total_area']:,.0f} um²) that is "
            f"{totals['predicted_design_saving_pct']:.2f}% predicted against "
            f"{totals['actual_design_saving_pct']:.2f}% delivered.")
    return " ".join(parts)


# =====================================================================
def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Render a step 9 report.json to a self-contained HTML page.")
    parser.add_argument("record", help="flow/9_report/report.json")
    parser.add_argument("-o", "--output", help="where to write the page")
    args = parser.parse_args()

    record = json.loads(Path(args.record).read_text())
    page = render_html(record)
    if args.output:
        Path(args.output).write_text(page)
        print(f"Wrote {args.output}")
    else:
        print(page)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
