#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The step 9 comparison, as a page you can look at
#
#  Same record compare_runs.py renders to Markdown, rendered instead to one
#  self-contained HTML file: inline CSS, inline SVG, inline script, no CDN and
#  no network.  It is opened off the filesystem, so anything external -- a web
#  font included -- would simply not load.
#
#  DESIGN -- this page is the sibling of aion_opt's optimization report
#  (flow/3_rewrite/report/rewrite_report.html) and deliberately shares its
#  design system, so the two reports of one flow read as one family:
#
#    tokens      the same --bg/--panel/--accent set aion_opt declares, down to
#                the hex values (aion_opt/report/reporter.py)
#    header      the sky -> indigo -> violet gradient, its masked 32px grid and
#                its two soft highlights, with main pulled up over the seam
#    cards       translucent panel, blurred, 16px radius, a gradient hairline
#                across the top, a stroked icon, an uppercase label and a
#                heavy number
#    marks       pill bars with rounded caps on a recessed track, a donut with
#                a rounded cap and a gradient stroke, glowing legend dots
#    tables      one bordered, rounded box; a --panel-2 header; zebra and hover
#                tints; a mini share bar in the last column
#
#  Colour by job.  aion_opt's palette is built from bright Tailwind 400s, so
#  every mark on it sits above the dark-mode lightness band -- that glow is the
#  look, and matching it is the point.  The checks that decide whether a reader
#  can actually separate two marks were run rather than eyeballed, on aion_opt's
#  own #151b2b panel:
#
#    diverging   saved #38bdf8 / spent #f472b6, neutral remainder #64748b
#                CVD dE 10.4 (target 8), normal vision 27.0 (floor 15), all >=3:1
#    paired      predicted #38bdf8 / delivered #34d399
#                CVD dE 15.7, normal vision 16.8, all >=3:1
#
#  Sky and indigo -- the pair aion_opt uses for Original/Optimized -- were
#  measured and rejected here: dE 13.5 to normal vision is under the floor, so
#  a full-colour reader cannot separate them.  Delivered is emerald instead,
#  which is also what aion_opt paints its own "New" bar.
#
#  Two charts, and each is the form its data asks for:
#
#    area attribution   signed deltas around zero -> diverging pill bars,
#                       warm/cool poles, neutral zero rule.  This is the chart
#                       that says the substitution saved less than PnR spent.
#    promise/delivery   one quantity measured twice per cell -> a donut for the
#                       headline share and paired pill bars per cell, which is
#                       aion_opt's Area Overview panel applied to what was
#                       drawn instead of to the AREA_FACTOR estimate.
#
#  Print.  A thesis prints.  @media print flips to ink on paper, drops the
#  gradient, keeps the data hues, and refuses to break a figure across pages.
# ================================================================

from __future__ import annotations

import html
import json
import math
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------
# Tokens. The first block is aion_opt's, value for value; the second is the
# data palette, which the UI is never allowed to borrow from.
CSS = """
:root {
  color-scheme: dark;

  --bg:      #0b0f19;
  --panel:   #151b2b;
  --panel-2: #1f293b;
  --text:    #f1f5f9;
  --muted:   #94a3b8;
  --accent:  #38bdf8;
  --accent-2:#818cf8;
  --success: #34d399;
  --danger:  #f472b6;
  --warning: #fbbf24;
  --border:  #28334d;
  --radius:  16px;
  --shadow:  0 20px 40px rgba(0,0,0,0.45);

  /* data — measured on --panel, not eyeballed; see the header comment */
  --saved:     #38bdf8;
  --spent:     #f472b6;
  --remainder: #64748b;
  --predicted: #38bdf8;
  --delivered: #34d399;

  --sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
          "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: var(--sans);
  background:
    radial-gradient(ellipse at 0% 0%, rgba(56,189,248,0.08) 0%, transparent 40%),
    radial-gradient(ellipse at 100% 0%, rgba(129,140,248,0.08) 0%, transparent 40%),
    var(--bg);
  color: var(--text);
  line-height: 1.55;
}

/* ---- header: aion_opt's gradient, grid and highlights ---- */
header {
  background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 55%, #8b5cf6 100%);
  padding: 4rem 1.5rem 4.5rem;
  text-align: center;
  position: relative;
  overflow: hidden;
}
header::before {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(rgba(255,255,255,0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.06) 1px, transparent 1px);
  background-size: 32px 32px;
  mask-image: radial-gradient(circle at 50% 50%, black 0%, transparent 70%);
  -webkit-mask-image: radial-gradient(circle at 50% 50%, black 0%, transparent 70%);
}
header::after {
  content: "";
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at 20% 30%, rgba(255,255,255,0.14) 0%, transparent 45%),
              radial-gradient(circle at 80% 70%, rgba(255,255,255,0.10) 0%, transparent 45%);
}
header h1 {
  margin: 0; font-size: 2.6rem; font-weight: 800; letter-spacing: -0.04em;
  position: relative; z-index: 1;
}
header p { margin: 0.6rem 0 0; opacity: 0.92; font-size: 1.15rem;
           position: relative; z-index: 1; }
.subtitle-note {
  margin: 0.55rem auto 0; max-width: 64ch; opacity: 0.82; font-size: 0.95rem;
  font-style: italic; position: relative; z-index: 1;
}
.subtitle-note b { font-style: normal; font-weight: 700; opacity: 1; }

.tally {
  position: relative; z-index: 1;
  display: flex; justify-content: center; flex-wrap: wrap; gap: 0.6rem;
  margin-top: 1.4rem;
}
.tally span {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.3rem 0.85rem; border-radius: 999px;
  background: rgba(255,255,255,0.14);
  border: 1px solid rgba(255,255,255,0.22);
  font-size: 0.82rem; font-weight: 600; letter-spacing: 0.02em;
}
.tally b { font-size: 1rem; font-weight: 800; }
.tally .scope { font-family: var(--mono); font-size: 0.72rem;
                background: rgba(255,255,255,0.08); font-weight: 500; }

main {
  max-width: 1200px; margin: -2.5rem auto 0; padding: 0 1.5rem;
  position: relative; z-index: 2;
}

/* ---- cards ---- */
.cards {
  display: grid; gap: 1.25rem; margin-bottom: 1.5rem;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
}
.card {
  background: rgba(21,27,43,0.85);
  backdrop-filter: blur(10px);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.5rem;
  box-shadow: var(--shadow);
  position: relative; overflow: hidden;
}
.card::before {
  content: "";
  position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  opacity: 0.8;
}
.card-icon { color: var(--accent); margin-bottom: 0.6rem; }
.card-icon svg { width: 1.6rem; height: 1.6rem; }
.card-label { color: var(--muted); font-size: 0.8rem; text-transform: uppercase;
              letter-spacing: 0.09em; margin-bottom: 0.35rem; }
.card-value { font-size: 2.1rem; font-weight: 800; letter-spacing: -0.03em;
              font-variant-numeric: tabular-nums; }
.card-value .unit { font-size: 0.9rem; font-weight: 600; color: var(--muted);
                    margin-left: 0.35rem; letter-spacing: 0;
                    font-family: var(--mono); }
.card-delta { font-size: 0.9rem; margin-top: 0.4rem; font-weight: 700;
              font-variant-numeric: tabular-nums; color: var(--success);
              display: flex; align-items: center; gap: 0.5rem;
              flex-wrap: wrap; }
.card-delta.negative { color: var(--danger); }
.card-delta.flat     { color: var(--muted); }
.card-foot { margin-top: 0.35rem; font-size: 0.78rem; color: var(--muted);
             font-family: var(--mono); }

/* ---- panels ---- */
.grid-2 {
  display: grid; gap: 1.5rem; margin-bottom: 1.5rem; align-items: start;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
}
.panel {
  background: rgba(21,27,43,0.85);
  backdrop-filter: blur(10px);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.75rem;
  box-shadow: var(--shadow);
  margin-bottom: 1.5rem;
}
.grid-2 .panel { margin-bottom: 0; }
.panel h2 {
  margin: 0 0 0.4rem; font-size: 1.25rem; font-weight: 700; color: var(--text);
  display: flex; align-items: center; gap: 0.5rem;
}
.panel h2 svg { width: 1.25rem; height: 1.25rem; color: var(--accent);
                flex: none; }
.panel h3 {
  margin: 1.6rem 0 0.3rem; font-size: 0.75rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted);
  font-family: var(--mono);
}
/* a cell name is an identifier; uppercasing it renames the cell */
.panel h3.cell { text-transform: none; font-size: 0.8rem; color: var(--text);
                 letter-spacing: 0.02em; }
.panel h3.cell em { font-style: normal; color: var(--muted); font-weight: 600; }
.muted-note { color: var(--muted); font-size: 0.88rem; margin: 0 0 1.25rem;
              max-width: 80ch; }
.panel > p { color: var(--muted); font-size: 0.9rem; max-width: 80ch; }

/* findings sit on the panel, not in another box */
.finding { display: flex; gap: 0.7rem; align-items: baseline;
           margin: 0.6rem 0; font-size: 0.9rem; color: var(--muted); }
.finding .ic { flex: none; font-weight: 800; color: var(--muted); }
.finding.good .ic { color: var(--success); }
.finding.bad  .ic { color: var(--danger); }
.finding b { color: var(--text); font-weight: 700; }
.finding ul { margin: 0.5rem 0 0; padding-left: 1.1rem; }
.finding code { font-family: var(--mono); color: var(--text); font-size: 0.85em; }

/* ---- pill bars ---- */
.bar-row {
  display: grid; grid-template-columns: 92px 1fr 86px;
  align-items: center; gap: 0.9rem; margin: 0.6rem 0; font-size: 0.95rem;
}
.bar-label { color: var(--muted); text-align: right; font-weight: 500;
             font-family: var(--mono); font-size: 0.78rem; }
.bar-track { background: var(--panel-2); border-radius: 999px; height: 16px;
             overflow: hidden; box-shadow: inset 0 2px 4px rgba(0,0,0,0.2); }
.bar-fill  { height: 100%; border-radius: 999px; }
.bar-value { font-weight: 700; text-align: right;
             font-variant-numeric: tabular-nums; font-size: 0.88rem;
             font-family: var(--mono); }

/* ---- donut ---- */
.donut { width: 190px; height: 190px; margin: 0.75rem auto 1.25rem;
         position: relative; }
.donut svg { transform: rotate(-90deg); width: 100%; height: 100%;
             filter: drop-shadow(0 0 8px rgba(52,211,153,0.25)); }
.donut-bg { fill: none; stroke: var(--panel-2); stroke-width: 10; }
.donut-fg { fill: none; stroke: url(#gradDonut); stroke-width: 10;
            stroke-linecap: round; }
.donut-text { position: absolute; inset: 0; display: flex; flex-direction: column;
              align-items: center; justify-content: center; }
.donut-percent { font-size: 1.9rem; font-weight: 800; letter-spacing: -0.03em;
                 font-variant-numeric: tabular-nums; }
.donut-label { font-size: 0.85rem; color: var(--muted); font-weight: 500; }

/* ---- legend ---- */
.legend { display: flex; justify-content: center; flex-wrap: wrap;
          gap: 0.9rem 1.6rem; margin-top: 1.25rem; font-size: 0.83rem;
          color: var(--muted); }
.legend span { display: inline-flex; align-items: center; gap: 0.45rem; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block;
       box-shadow: 0 0 8px currentColor; }

/* ---- diverging chart ---- */
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
figure { margin: 0; }
figcaption { margin-top: 1.1rem; padding-top: 0.9rem;
             border-top: 1px solid var(--border);
             color: var(--muted); font-size: 0.85rem; }

/* ---- tables ---- */
.table-wrap { border: 1px solid var(--border); border-radius: var(--radius);
              overflow: hidden; margin-top: 0.75rem; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th {
  padding: 0.85rem; text-align: right; background: var(--panel-2);
  color: var(--muted); font-weight: 700; text-transform: uppercase;
  font-size: 0.7rem; letter-spacing: 0.08em;
  border-bottom: 1px solid var(--border); white-space: nowrap;
}
th:first-child, td:first-child { text-align: left; }
th .u { text-transform: none; letter-spacing: 0; }
td { padding: 0.78rem 0.85rem; text-align: right; white-space: nowrap;
     border-bottom: 1px solid var(--border);
     font-family: var(--mono); font-size: 0.84rem;
     font-variant-numeric: tabular-nums; }
td:first-child { font-family: var(--sans); font-size: 0.9rem;
                 white-space: normal; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:nth-child(even) { background: rgba(255,255,255,0.015); }
tbody tr:hover td { background: rgba(255,255,255,0.045); }
td .unit { color: var(--muted); }
td code { font-family: var(--mono); color: var(--text); }
.swatch { display: inline-block; width: 9px; height: 9px; border-radius: 2px;
          margin-right: 0.55rem; vertical-align: 1px; }

/* a losing row is marked on the row, not only by the chip at the end */
tbody tr.v-loss td:first-child,
tbody tr.v-fail td:first-child { box-shadow: inset 3px 0 0 var(--danger); }

.mini-bar { background: var(--panel-2); border-radius: 999px; height: 7px;
            width: 80px; overflow: hidden; display: inline-block;
            vertical-align: middle; margin-right: 0.5rem;
            box-shadow: inset 0 1px 2px rgba(0,0,0,0.2); }
.mini-bar-fill { display: block; height: 100%; border-radius: 999px;
                 background: linear-gradient(90deg, var(--accent), var(--accent-2)); }
.share { color: var(--muted); font-size: 0.78rem; font-weight: 700;
         min-width: 34px; display: inline-block; }

/* ---- verdict chip: icon + word, never colour alone ---- */
.chip { display: inline-flex; align-items: center; gap: 0.28rem;
        font-size: 0.68rem; font-weight: 800; letter-spacing: 0.08em;
        text-transform: uppercase; font-family: var(--sans); }
.chip.win, .chip.clean { color: var(--success); }
.chip.loss, .chip.fail { color: var(--danger); }
.chip.tie, .chip.info, .chip.na { color: var(--muted); font-weight: 700; }

/* ---- die plates ---- */
.plates { display: grid; gap: 1.5rem; }
.plate img { width: 100%; max-width: 100%; display: block;
             border-radius: 10px; border: 1px solid var(--border); }
.plate figcaption { border-top: 0; padding-top: 0.75rem; margin-top: 0.5rem; }
.plate-tag { display: inline-block; margin-right: 0.6rem; padding: 0.12rem 0.5rem;
             border-radius: 999px; background: rgba(56,189,248,0.16);
             color: var(--accent); font-size: 0.65rem; font-weight: 800;
             letter-spacing: 0.08em; text-transform: uppercase;
             vertical-align: 1px; }

/* ---- footer ---- */
footer { text-align: center; color: var(--muted); padding: 2.5rem 1.5rem;
         font-size: 0.85rem; }
footer b { color: var(--text); }
footer time { color: var(--text); font-weight: 600; }
footer ul { list-style: none; padding: 0; margin: 1.1rem auto 1.6rem;
            max-width: 80ch; text-align: left; }
footer li { margin: 0.5rem 0; padding-left: 1rem; position: relative; }
footer li::before { content: "·"; position: absolute; left: 0;
                    color: var(--accent); font-weight: 800; }
footer code { font-family: var(--mono); color: var(--text); }

/* ---- tooltip ---- */
#tip {
  position: fixed; z-index: 30; pointer-events: none; opacity: 0;
  transition: opacity .09s; padding: 0.6rem 0.8rem; border-radius: 10px;
  background: var(--panel-2); border: 1px solid var(--border);
  box-shadow: var(--shadow);
  font: 0.78rem/1.55 var(--mono); color: var(--text); white-space: nowrap;
}
#tip b { font-family: var(--sans); font-size: 0.85rem; font-weight: 700; }
#tip .k { color: var(--muted); }
[data-tip] { cursor: default; }

:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}

@media (max-width: 640px) {
  header h1 { font-size: 1.9rem; }
  header { padding: 3rem 1.25rem 3.5rem; }
  .cards { grid-template-columns: 1fr; }
  .card-value { font-size: 1.8rem; }
  .bar-row { grid-template-columns: 74px 1fr 64px; font-size: 0.85rem; }
  .panel { padding: 1.25rem; }
}

@media print {
  :root {
    color-scheme: light;
    --bg: #fff; --panel: #fff; --panel-2: #f1f3f7;
    --text: #0b0f19; --muted: #55607a; --border: #cfd6e4;
    --shadow: none;
  }
  body { background: #fff; }
  header { background: #fff; color: var(--text); padding: 1.5rem 0 0.5rem;
           text-align: left; }
  header::before, header::after { display: none; }
  .tally { justify-content: flex-start; }
  .tally span { background: #f1f3f7; border-color: var(--border);
                color: var(--text); }
  main { margin-top: 1.5rem; max-width: none; padding: 0; }
  .panel, .card { backdrop-filter: none; break-inside: avoid; }
  .table-wrap, figure, .plate, .donut { break-inside: avoid; }
  #tip { display: none; }
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

# The same stroked 24px set aion_opt's cards and panel headings use.
ICON_GRID = ('<svg xmlns="http://www.w3.org/2000/svg" fill="none" '
             'viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" '
             'aria-hidden="true"><path stroke-linecap="round" '
             'stroke-linejoin="round" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 '
             '01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 '
             '01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 '
             '01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 '
             '01-2 2h-2a2 2 0 01-2-2v-2z" /></svg>')
ICON_BOLT = ('<svg xmlns="http://www.w3.org/2000/svg" fill="none" '
             'viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" '
             'aria-hidden="true"><path stroke-linecap="round" '
             'stroke-linejoin="round" d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" /></svg>')
ICON_CUBE = ('<svg xmlns="http://www.w3.org/2000/svg" fill="none" '
             'viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" '
             'aria-hidden="true"><path stroke-linecap="round" '
             'stroke-linejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0'
             '-10L4 7m8 4v10M4 7v10l8 4" /></svg>')
ICON_MINIMIZE = ('<svg xmlns="http://www.w3.org/2000/svg" fill="none" '
                 'viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" '
                 'aria-hidden="true"><path stroke-linecap="round" '
                 'stroke-linejoin="round" d="M3 7V5a2 2 0 012-2h2M17 3h2a2 2 0 '
                 '012 2v2M21 17v2a2 2 0 01-2 2h-2M7 21H5a2 2 0 01-2-2v-2" />'
                 '</svg>')
ICON_CHIP = ('<svg xmlns="http://www.w3.org/2000/svg" fill="none" '
             'viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" '
             'aria-hidden="true"><path stroke-linecap="round" '
             'stroke-linejoin="round" d="M8 8h8v8H8zM4 9h4M4 15h4M16 9h4M16 15h4'
             'M9 4v4M15 4v4M9 16v4M15 16v4" /></svg>')
ICON_CLOCK = ('<svg xmlns="http://www.w3.org/2000/svg" fill="none" '
              'viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" '
              'aria-hidden="true"><path stroke-linecap="round" '
              'stroke-linejoin="round" d="M12 7v5l3 2m6-2a9 9 0 11-18 0 9 9 0 '
              '0118 0z" /></svg>')
ICON_IMAGE = ('<svg xmlns="http://www.w3.org/2000/svg" fill="none" '
              'viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" '
              'aria-hidden="true"><path stroke-linecap="round" '
              'stroke-linejoin="round" d="M3 5h18v14H3zM3 16l5-5 4 4 3-3 6 6" '
              '/></svg>')
ICON_SCALE = ('<svg xmlns="http://www.w3.org/2000/svg" fill="none" '
              'viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" '
              'aria-hidden="true"><path stroke-linecap="round" '
              'stroke-linejoin="round" d="M12 4v16M5 8h14M5 8l-2 6h4l-2-6zm14 0'
              'l-2 6h4l-2-6z" /></svg>')

#: One icon per headline metric, keyed the way compare_runs names them.
CARD_ICONS = {
    "design__instance__area__stdcell": ICON_MINIMIZE,
    "timing__setup__ws": ICON_CLOCK,
    "power__total": ICON_BOLT,
    "route__wirelength": ICON_GRID,
}

#: icon + word, so a verdict never rests on hue alone
CHIP = {"win": ("✓", "win"), "loss": ("✕", "loss"), "tie": ("—", "tie"),
        "clean": ("✓", "clean"), "fail": ("✕", "fail"),
        "info": ("", "—"), "n/a": ("", "—")}

#: LibreLane writes ASCII units. The page is read by people, so it shows the
#: ones the process actually uses.
UNIT = {"um²": "µm²", "um": "µm", "uW": "µW"}


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def unit_of(unit: str) -> str:
    return UNIT.get(unit, unit)


def _fmt(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _signed(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value:+,.{digits}f}"


def _digits(unit: str) -> int:
    """Decimals a unit deserves.

    Area and wirelength here run to six figures; two decimals on those is
    noise that makes the cards harder to read, not more precise.
    """
    if unit in ("", "um", "um²"):
        return 0
    if unit in ("ns", "mW", "uW"):
        return 3
    return 2


def chip(verdict: str) -> str:
    icon, word = CHIP.get(verdict, ("", verdict))
    mark = f"<span>{icon}</span>" if icon else ""
    return f'<span class="chip {esc(verdict)}">{mark}{esc(word)}</span>'


# =====================================================================
# Marks
# =====================================================================
def card(icon: str, label: str, value: str, unit: str, delta: str,
         tone: str, foot: str) -> str:
    return (f'<div class="card"><div class="card-icon">{icon}</div>'
            f'<div class="card-label">{esc(label)}</div>'
            f'<div class="card-value">{value}'
            f'<span class="unit">{esc(unit)}</span></div>'
            f'<div class="card-delta {tone}">{delta}</div>'
            f'<div class="card-foot">{foot}</div></div>')


def pct_bar(label: str, value: Optional[float], total: float, colour: str,
            display: str, tip: str = "") -> str:
    """aion_opt's bar row: a recessed pill track and a rounded, glowing fill."""
    share = (0.0 if not total or value is None
             else max(0.0, min(100.0, 100.0 * value / total)))
    attr = f' data-tip="{esc(tip)}"' if tip else ""
    return (f'<div class="bar-row"{attr}>'
            f'<div class="bar-label">{esc(label)}</div>'
            f'<div class="bar-track"><div class="bar-fill" '
            f'style="width:{share:.2f}%;background:{colour};'
            f'box-shadow:0 0 12px {colour}"></div></div>'
            f'<div class="bar-value">{display}</div></div>')


def donut(percent: float, label: str, tip: str = "") -> str:
    """A single share, as aion_opt draws it: 10px ring, rounded cap, gradient."""
    radius = 70
    circumference = 2 * math.pi * radius
    offset = circumference * (1 - max(0.0, min(1.0, percent / 100.0)))
    attr = f' data-tip="{esc(tip)}"' if tip else ""
    return (f'<div class="donut"{attr}>'
            f'<svg viewBox="0 0 160 160" role="img" '
            f'aria-label="{esc(label)}: {percent:.0f} percent">'
            f'<circle class="donut-bg" cx="80" cy="80" r="{radius}"/>'
            f'<circle class="donut-fg" cx="80" cy="80" r="{radius}" '
            f'stroke-dasharray="{circumference:.2f}" '
            f'stroke-dashoffset="{offset:.2f}"/></svg>'
            f'<div class="donut-text"><div class="donut-percent">'
            f'{percent:.0f}%</div>'
            f'<div class="donut-label">{esc(label)}</div></div></div>')


#: A finer ladder than 1/2/5. On a coarse one a 1,257 um² bar is drawn against
#: a 2,000 bound and spends a third of the plot on nothing.
_LADDER = (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10)


def _nice(value: float) -> float:
    """The smallest round axis bound at or above `value`."""
    if value <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(value))
    for step in _LADDER:
        if value <= step * power * 1.0000001:
            return step * power
    return 10 * power


def attribution_chart(rows: List[dict]) -> str:
    """Diverging pill bars: what the substitution saved, what PnR spent back."""
    rows = [r for r in rows if r["absolute"] != 0]
    if not rows:
        return ""

    # A name gutter, the plot, and a lane on each side wide enough for a value
    # label -- so a full-length bar's number never lands on the class name or
    # runs off the panel.
    gutter, lane, right_pad = 270, 64, 76
    width, row_h, bar_h = 960, 38, 16
    top = 16
    height = top + row_h * len(rows) + 42
    plot_w = width - gutter - lane - right_pad
    bound = _nice(max(abs(r["absolute"]) for r in rows))
    zero = gutter + lane + plot_w / 2
    scale = (plot_w / 2) / bound
    radius = bar_h / 2
    last = top + row_h * len(rows)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="Standard cell area delta by cell class">']

    # the recessed track first, so every bar is read against the same rail
    for index in range(len(rows)):
        bar_y = top + index * row_h + (row_h - bar_h) / 2
        parts.append(f'<rect x="{gutter + lane:.1f}" y="{bar_y:.1f}" '
                     f'width="{plot_w:.1f}" height="{bar_h}" rx="{radius}" '
                     f'fill="var(--panel-2)"/>')

    # hairline grid over the tracks, recessive
    for frac in (-1.0, -0.5, 0.5, 1.0):
        x = zero + frac * bound * scale
        parts.append(f'<line x1="{x:.1f}" y1="{top - 4}" x2="{x:.1f}" '
                     f'y2="{last + 4}" stroke="var(--border)" stroke-width="1"/>')
    for frac in (-1.0, -0.5, 0.0, 0.5, 1.0):
        x = zero + frac * bound * scale
        tick = "0" if not frac else f"{frac * bound:+,.0f}"
        parts.append(f'<text x="{x:.1f}" y="{height - 17}" fill="var(--muted)" '
                     f'font-size="11.5" text-anchor="middle">{tick}</text>')
    parts.append(f'<text x="{zero:.1f}" y="{height - 2}" fill="var(--muted)" '
                 f'font-size="10.5" text-anchor="middle" '
                 f'letter-spacing="0.09em">delta   µm²</text>')

    for index, row in enumerate(rows):
        y = top + index * row_h
        span = abs(row["absolute"]) * scale
        spent = row["absolute"] > 0
        # Fill is whatever is left over on a pinned die. Painting it the same
        # sky as a real saving would claim a result nobody achieved.
        if row["klass"] == "fill_cell":
            colour, glow = "var(--remainder)", "rgba(100,116,139,0.40)"
        elif spent:
            colour, glow = "var(--spent)", "rgba(244,114,182,0.45)"
        else:
            colour, glow = "var(--saved)", "rgba(56,189,248,0.45)"
        bar_y = y + (row_h - bar_h) / 2
        bar_x = zero if spent else zero - span
        base = bar_y + bar_h - 4

        tag = "collateral" if row["collateral"] else "substituted"
        tip = (f'<b>{esc(row["klass"])}</b><br>'
               f'<span class="k">AION</span> {_fmt(row["aion"], 1)} µm²<br>'
               f'<span class="k">baseline</span> {_fmt(row["baseline"], 1)} µm²<br>'
               f'<span class="k">delta</span> {_signed(row["absolute"], 1)} µm² '
               f'({tag})')

        parts.append(f'<g data-tip="{esc(tip)}">')
        parts.append(f'<rect x="0" y="{y:.1f}" width="{width}" height="{row_h}" '
                     f'fill="transparent"/>')
        parts.append(f'<text x="{gutter - 20}" y="{base:.1f}" '
                     f'fill="var(--muted)" font-size="12" text-anchor="end" '
                     f'class="m">{esc(row["klass"])}</text>')
        parts.append(f'<rect x="{bar_x:.2f}" y="{bar_y:.1f}" '
                     f'width="{max(span, 4):.2f}" height="{bar_h}" '
                     f'rx="{radius}" fill="{colour}" '
                     f'style="filter:drop-shadow(0 0 6px {glow})"/>')
        label_x = zero + (span + 13) * (1 if spent else -1)
        parts.append(f'<text x="{label_x:.1f}" y="{base:.1f}" '
                     f'fill="var(--text)" font-size="12.5" font-weight="700" '
                     f'text-anchor="{"start" if spent else "end"}">'
                     f'{_signed(row["absolute"], 1)}</text>')
        parts.append("</g>")

    # the zero rule sits above the bars: it is the thing they are read against
    parts.append(f'<line x1="{zero:.1f}" y1="{top - 4}" x2="{zero:.1f}" '
                 f'y2="{last + 4}" stroke="var(--muted)" stroke-width="1" '
                 f'stroke-opacity="0.6"/>')
    parts.append("</svg>")
    return "".join(parts)


def legend(items: List[tuple]) -> str:
    """Glowing dots beside the labels. Identity never rides on the text colour."""
    out = []
    for label, colour in items:
        out.append(f'<span><i class="dot" style="background:{colour};'
                   f'color:{colour}"></i>{esc(label)}</span>')
    return f'<div class="legend">{"".join(out)}</div>'


# =====================================================================
# Tables
# =====================================================================
def _row_class(verdict: str) -> str:
    """Only a row that is worse than the baseline gets an edge.

    Marking the wins too would put a stripe on nearly every row, which is a
    decoration rather than a signal. The chip in the last column still carries
    every verdict, including the good ones.
    """
    return f' class="v-{verdict}"' if verdict in ("loss", "fail") else ""


def metric_table(rows: List[dict]) -> str:
    body = []
    for row in rows:
        digits = _digits(row["unit"])
        unit = (f' <span class="unit">{esc(unit_of(row["unit"]))}</span>'
                if row["unit"] else "")
        pct = "" if row["percent"] is None else f' ({row["percent"]:+.2f}%)'
        body.append(
            f'<tr{_row_class(row["verdict"])}><td>{esc(row["label"])}{unit}</td>'
            f'<td>{_fmt(row["aion"], digits)}</td>'
            f'<td>{_fmt(row["baseline"], digits)}</td>'
            f'<td>{_signed(row["absolute"], digits)}{pct}</td>'
            f'<td>{chip(row["verdict"])}</td></tr>')
    return (f'<div class="table-wrap"><div class="scroll"><table><thead><tr>'
            f"<th>Metric</th><th>AION</th><th>Baseline</th><th>Delta</th><th></th>"
            f"</tr></thead><tbody>{''.join(body)}</tbody></table></div></div>")


def cards(rows: List[dict]) -> str:
    out = []
    for row in rows:
        digits = _digits(row["unit"])
        pct = "" if row["percent"] is None else f" ({row['percent']:+.2f}%)"
        tone = {"win": "", "loss": "negative"}.get(row["verdict"], "flat")
        mark = "" if not row["absolute"] else ("▼ " if row["absolute"] < 0
                                               else "▲ ")
        delta = (f'<span>{mark}{_signed(row["absolute"], digits)}{pct}</span>'
                 f'{chip(row["verdict"])}')
        out.append(card(CARD_ICONS.get(row["key"], ICON_CUBE), row["label"],
                        _fmt(row["aion"], digits), unit_of(row["unit"]),
                        delta, tone,
                        f'baseline {_fmt(row["baseline"], digits)}'))
    return f'<section class="cards">{"".join(out)}</section>'


def corner_table(corners: List[dict]) -> str:
    body = []
    for entry in corners:
        body.append(
            f'<tr><td><code>{esc(entry["corner"])}</code></td>'
            f'<td>{_fmt(entry["setup"]["aion"], 3)}</td>'
            f'<td>{_fmt(entry["setup"]["baseline"], 3)}</td>'
            f'<td>{_fmt(entry["hold"]["aion"], 3)}</td>'
            f'<td>{_fmt(entry["hold"]["baseline"], 3)}</td></tr>')
    return (f'<div class="table-wrap"><div class="scroll"><table><thead><tr>'
            f"<th>Corner</th><th>Setup AION</th><th>Setup base</th>"
            f"<th>Hold AION</th><th>Hold base</th></tr></thead>"
            f'<tbody>{"".join(body)}</tbody></table></div></div>')


def ledger_table(rows: List[dict], site_area: float) -> str:
    """The drawn cells, ranked by what they actually delivered.

    The share column is aion_opt's mini bar: each cell's slice of the total
    saving, so the reader sees which one carried the result.
    """
    delivered_total = sum(r["actual_saving"] or 0.0 for r in rows) or 1.0
    body = []
    for row in sorted(rows, key=lambda r: -(r["actual_saving"] or 0.0)):
        sites = ("—" if row["actual_each"] is None
                 else f'{row["actual_each"] / site_area:.0f}')
        was = f'{row["original_each"] / site_area:.0f}'
        drawn = ("not published" if row["actual_area"] is None
                 else _fmt(row["actual_area"], 1))
        if row["actual_saving"] is None:
            real, share = "—", 0.0
        else:
            pct = 100.0 * row["actual_saving"] / row["original_area"]
            real = f'{_fmt(row["actual_saving"], 1)} ({pct:.1f}%)'
            share = 100.0 * row["actual_saving"] / delivered_total
        body.append(
            f'<tr><td><code>{esc(row["cell"])}</code></td>'
            f'<td>{sites} of {was}</td>'
            f'<td>{row["occurrences"]}</td>'
            f'<td>{_fmt(row["original_area"], 1)}</td>'
            f'<td>{_fmt(row["predicted_area"], 1)}</td>'
            f"<td>{drawn}</td>"
            f'<td>{_fmt(row["predicted_saving"], 1)}</td>'
            f"<td>{real}</td>"
            f'<td><span class="mini-bar"><span class="mini-bar-fill" '
            f'style="width:{share:.1f}%"></span></span>'
            f'<span class="share">{share:.0f}%</span></td></tr>')
    unit = '<span class="u">µm²</span>'
    return (f'<div class="table-wrap"><div class="scroll"><table><thead><tr>'
            f"<th>Cell</th><th>Sites</th><th>Placed</th>"
            f"<th>PDK area {unit}</th><th>Predicted {unit}</th>"
            f"<th>Drawn {unit}</th>"
            f"<th>Predicted saving</th><th>Real saving</th>"
            f"<th>Share of saving</th>"
            f'</tr></thead><tbody>{"".join(body)}</tbody></table></div></div>')


def attribution_table(rows: List[dict]) -> str:
    body = []
    for row in rows:
        if row["absolute"] == 0:
            colour, tag = "var(--remainder)", "unchanged"
        elif row["klass"] == "fill_cell":
            colour, tag = "var(--remainder)", "remainder"
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
    unit = '<span class="u">µm²</span>'
    return (f'<div class="table-wrap"><div class="scroll"><table><thead><tr>'
            f"<th>Cell class</th><th>AION {unit}</th><th>Baseline {unit}</th>"
            f"<th>Delta</th><th></th>"
            f'</tr></thead><tbody>{"".join(body)}</tbody></table></div></div>')


# =====================================================================
def render_html(record: dict, images: Optional[List[tuple]] = None,
                site_area: float = 0.48 * 3.78) -> str:
    """The whole report as one self-contained page."""
    design = record.get("design") or "design"
    head = record["headline"]
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

    body: List[str] = []
    add = body.append

    # ---- header: aion_opt's gradient, and the answer inside it ----------
    add("<header>")
    add("<h1>AION Flow - Comparison Report</h1>")
    add(f"<p>{esc(design)} — the mined cell library against the PDK-only "
        f"baseline</p>")
    add(f'<p class="subtitle-note">{_lede(record)}</p>')
    add(f'<div class="tally">'
        f'<span><b>{head["wins"]}</b> win</span>'
        f'<span><b>{head["losses"]}</b> loss</span>'
        f'<span><b>{head["ties"]}</b> tie</span>'
        f'<span class="scope">{esc(_scope(record))}</span></div>')
    add("</header>")

    add("<main>")

    # ---- headline cards --------------------------------------------------
    add(cards(head["rows"]))

    # ---- what the comparison is worth ------------------------------------
    add('<div class="panel">')
    add(f"<h2>{ICON_SCALE} How to read this</h2>")
    if record["comparable"]:
        items = "".join(f"<li><code>{esc(p)}</code></li>"
                        for p in record["comparable"])
        add('<div class="finding bad"><span class="ic">✕</span><div>'
            "<b>These two runs are not configured the same way.</b> Every delta "
            "here mixes the cell substitution with a configuration change, so "
            f"none of it is attributable.<ul>{items}</ul></div></div>")
    else:
        add('<div class="finding good"><span class="ic">✓</span><div>'
            "Same RTL, same SDC, same die, same PDK, same synthesis strategy — "
            "<b>the cell substitution is the only variable</b>, so every delta "
            "here is attributable to it.</div></div>")
    if record.get("fixed_die"):
        add('<div class="finding"><span class="ic">·</span><div>'
            "<code>FP_SIZING</code> is <code>absolute</code>, so the die is "
            "pinned and <b>is not a result</b>. An area saving shows up as "
            "lower utilization and more fill, never as a smaller chip.</div></div>")
    add('<div class="finding"><span class="ic">·</span><div>'
        "Values are the AION run; the delta is measured against the baseline. "
        "Anything under 0.5% is called a <b>tie</b>, because placement is a "
        "seeded heuristic and a re-run moves it by about that much.</div></div>")
    add("</div>")

    # ---- attribution ----------------------------------------------------
    attribution = record.get("area_attribution") or []
    if attribution:
        add('<div class="panel">')
        add(f"<h2>{ICON_MINIMIZE} Where the standard cell area went</h2>")
        add('<p class="muted-note">The substitution only ever replaces '
            "combinational cells. Everything else is what place-and-route spent "
            "in response — and on a fixed die, fill absorbs the remainder, so "
            "its bar is the mirror of the others rather than a result.</p>")
        add('<figure><div class="chart">')
        add(attribution_chart(attribution))
        add("</div>")
        add(legend([("saved — smaller than baseline", "var(--saved)"),
                    ("spent — larger than baseline", "var(--spent)"),
                    ("remainder — not a result", "var(--remainder)")]))
        add(f"<figcaption>{_attribution_caption(attribution)}</figcaption>")
        add("</figure>")
        add(attribution_table(attribution))
        add("</div>")

    # ---- promise vs delivery --------------------------------------------
    totals = record.get("ledger_totals")
    ledger = record.get("ledger") or []
    if totals and ledger:
        factor = totals.get("area_factor")
        promised = totals.get("predicted_saving") or 0.0
        got = totals.get("actual_saving")
        delivered_pct = 100.0 * got / promised if got is not None and promised else 0.0

        add('<div class="grid-2">')

        add('<div class="panel">')
        add(f"<h2>{ICON_CUBE} The promise and the delivery</h2>")
        add('<p class="muted-note">Step 3 estimated a merged cell at '
            f"<code>AREA_FACTOR{f' = {factor}' if factor else ''}</code> times "
            "the cells it replaces — before anything was drawn. Step 6 drew "
            "them, and their Liberty carries the real number.</p>")
        add(donut(delivered_pct, "of the promise",
                  "<b>all substituted sites</b><br>"
                  f'<span class="k">predicted</span> {_fmt(promised, 1)} µm²<br>'
                  f'<span class="k">delivered</span> {_fmt(got, 1)} µm²'))
        add(pct_bar("predicted", promised, promised, "var(--predicted)",
                    _fmt(promised, 0)))
        add(pct_bar("delivered", got, promised, "var(--delivered)",
                    _fmt(got, 0)))
        add(legend([("predicted by AREA_FACTOR", "var(--predicted)"),
                    ("delivered by the drawn cells", "var(--delivered)")]))
        add(f"<figcaption>{_ledger_caption(totals)}</figcaption>")
        add("</div>")

        add('<div class="panel">')
        add(f"<h2>{ICON_GRID} Cell by cell</h2>")
        add('<p class="muted-note">Area saved at the substituted sites, as '
            "AREA_FACTOR promised it and as the drawn layouts delivered it. "
            "Both bars are on one scale, so the gap is the shortfall.</p>")
        bound = max(max(r["predicted_saving"], r["actual_saving"] or 0.0)
                    for r in ledger)
        for row in sorted(ledger, key=lambda r: -(r["actual_saving"] or 0.0)):
            share = (100.0 * (row["actual_saving"] or 0.0) / row["predicted_saving"]
                     if row["predicted_saving"] else 0.0)
            tip = (f'<b>{esc(row["cell"])}</b><br>'
                   f'<span class="k">predicted</span> '
                   f'{_fmt(row["predicted_saving"], 1)} µm²<br>'
                   f'<span class="k">delivered</span> '
                   f'{_fmt(row["actual_saving"], 1)} µm²<br>'
                   f'<span class="k">that is</span> {share:.0f}% of the promise')
            add(f'<h3 class="cell">{esc(row["cell"])} '
                f"<em>· {share:.0f}% delivered</em></h3>")
            add(pct_bar("predicted", row["predicted_saving"], bound,
                        "var(--predicted)", _fmt(row["predicted_saving"], 0), tip))
            add(pct_bar("delivered", row["actual_saving"], bound,
                        "var(--delivered)", _fmt(row["actual_saving"], 0), tip))
        add("</div>")
        add("</div>")

        add('<div class="panel">')
        add(f"<h2>{ICON_CUBE} Applied cells</h2>")
        add('<p class="muted-note">Every mined cell that reached the die, '
            "ranked by the area it actually saved.</p>")
        add(ledger_table(ledger, site_area))
        add(f"<p>{_ledger_prose(record, totals)}</p>")
        add("</div>")

    # ---- the chip -------------------------------------------------------
    add('<div class="panel">')
    add(f"<h2>{ICON_CHIP} The chip, metric by metric</h2>")
    add('<p class="muted-note">Everything LibreLane measured on both runs, side '
        "by side. A pink edge marks a row the AION run lost.</p>")
    for section in record["sections"]:
        add(f'<h3>{esc(section["title"])}</h3>')
        add(metric_table(section["rows"]))
    add("</div>")

    add('<div class="panel">')
    add(f"<h2>{ICON_CLOCK} Slack by corner</h2>")
    add('<p class="muted-note">Setup is set by the slow corner and hold by the '
        "fast one, so a single worst-slack number hides where it came from.</p>")
    add(corner_table(record["corners"]))
    add("</div>")

    # ---- pictures --------------------------------------------------------
    if images:
        add('<div class="panel">')
        add(f"<h2>{ICON_IMAGE} The die</h2>")
        add('<p class="muted-note">Drawn straight from the GDS this run '
            "produced.</p>")
        add('<div class="plates">')
        for index, (caption, source) in enumerate(images, start=1):
            add('<figure class="plate">')
            add(f'<img src="{esc(source)}" alt="{esc(caption)}">')
            add(f'<figcaption><span class="plate-tag">plate {index}</span>'
                f"{esc(caption)}</figcaption>")
            add("</figure>")
        add("</div>")
        add("</div>")

    add("</main>")

    # ---- caveats ----------------------------------------------------------
    add("<footer>")
    add("<b>What this does not say</b><ul>")
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
    add("</ul>")
    add(f"<p>Generated by the AION flow · step 9 · <time>{esc(stamp)}</time></p>")
    add("</footer>")
    add('<div id="tip"></div>')

    gradient = ('<svg width="0" height="0" style="position:absolute">'
                '<defs><linearGradient id="gradDonut" x1="0" y1="0" x2="1" y2="1">'
                '<stop offset="0%" stop-color="#34d399"/>'
                '<stop offset="100%" stop-color="#38bdf8"/>'
                "</linearGradient></defs></svg>")

    return ('<!doctype html>\n<html lang="en">\n<head>\n'
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            "<title>AION Flow - Comparison Report</title>\n"
            f"<style>{CSS}</style>\n</head>\n<body>\n{gradient}\n"
            + "\n".join(body)
            + f"\n<script>{SCRIPT}</script>\n</body>\n</html>\n")


def _short(path: str) -> str:
    import os
    try:
        relative = os.path.relpath(path)
    except ValueError:
        return path
    return relative if not relative.startswith("..") else path


def _scope(record: dict) -> str:
    """The run's own coordinates, short enough to sit in the header."""
    runs = record["runs"]
    bits = [f'{_short(runs["aion"]["dir"])} vs {_short(runs["baseline"]["dir"])}']
    die = runs["aion"].get("die_um")
    if die and len(die) == 2:
        bits.append(f"{die[0]:,.2f} × {die[1]:,.2f} µm die")
    period = record.get("clock_period_ns")
    if period:
        bits.append(f"{period:g} ns clock")
    return " · ".join(bits)


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
    return (f"The cells the flow set out to replace {verb} {abs(aimed):,.1f} µm². "
            f"Place-and-route then {'spent' if spent > 0 else 'gave back'} "
            f"{abs(spent):,.1f} µm² on antenna diodes and repair buffers{tail}")


def _ledger_caption(totals: dict) -> str:
    if totals.get("actual_saving") is None:
        return "No cell has been published yet, so there is nothing to compare."
    share = (100.0 * totals["actual_saving"] / totals["predicted_saving"]
             if totals["predicted_saving"] else 0.0)
    return (f"Across {totals['occurrences']} substituted sites the drawn cells "
            f"delivered {share:.0f}% of what AREA_FACTOR promised: "
            f"{totals['actual_saving']:,.1f} µm² against "
            f"{totals['predicted_saving']:,.1f} µm².")


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
                f"{placed:,.1f} µm², the same number, so the cells reached the "
                f"die at the size they were drawn and the resizer left them alone.")
        else:
            parts.append(
                f"The hardened chip does not quite agree: combinational cell area "
                f"fell by {placed:,.1f} µm² against the ledger's "
                f"{totals['actual_saving']:,.1f} µm². The resizer swapped "
                f"combinational cells the substitution never touched.")
    if totals.get("actual_design_saving_pct") is not None:
        parts.append(
            f"Against the whole synthesized design "
            f"({totals['original_total_area']:,.0f} µm²) that is "
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
