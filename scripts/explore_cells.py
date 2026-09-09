#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Explore the step-2/3 mining space and pick the
#                             cell library that buys the most area.
#
#  Steps 2 and 3 are driven by six knobs (MAX_SIZE, MAX_OUTPUTS, MAX_INPUTS,
#  MIN_OCCURRENCES, MIN_SELECTED, ELITE_COUNT) and re-running the flow once
#  per combination is hopeless: one mining pass over tt_um_aion enumerates
#  ~46 million subgraphs and takes three minutes.
#
#  It only has to be paid once.  The caps are *post-filters* inside the
#  miner -- `_mine_roots` enumerates the same subgraphs whatever they are set
#  to and drops the ones that fail -- and MAX_OUTPUTS/MAX_INPUTS/MAX_SIZE are
#  all properties of the canonical key.  So this mines once at the loosest
#  corner of the grid and derives every tighter point from that result in
#  memory, which turns a day of flow runs into a couple of minutes.
#
#  Two selections are scored for every grid point:
#
#    flow    what the flow does today: cover the whole netlist, then keep the
#            ELITE_COUNT best cells.  The cover is computed before the cut, so
#            the sites of the cells that lost the cut just stay PDK cells --
#            step 3 filters the cover, it does not recompute it.
#    budget  choose the ELITE_COUNT cells *first*, greedily by the area each
#            one still buys given what the earlier picks already claimed, and
#            cover with only those.  Same number of hand-drawn layouts in
#            step 6, strictly more area, because no site is spent on a cell
#            that will not be in the library.
#
#  `--score merge` replaces the AREA_FACTOR guess with a measurement.  It runs
#  step 5's own synthesizer on every candidate pattern and prices the result in
#  placement sites, because AREA_FACTOR is the weakest link in the chain: the
#  flow assumed 0.65 and the two cells step 6 has drawn delivered 0.889, and
#  the reason is visible in step 5's own log -- `10 transistors vs 10 original
#  (+0), 0 merged stage(s)`.  Two inverting gates in series make a
#  non-inverting function, which a single complementary CMOS stack cannot
#  realise, so nothing collapses and the whole saving is the one diffusion
#  break between the two cells.  Ranking by area x occurrences cannot see that
#  difference; ranking by transistors removed is the point of this mode.
#
#  `survey` answers the other question -- why the mined cells are all two
#  standard cells wide -- by histogramming the enumeration itself, before any
#  filter, so the cost of each cap is a number rather than a guess.
# ================================================================

from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing as mp
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AION_OPT_DIR = PROJECT_ROOT / "aion_flow" / "tools" / "aion_opt"
AION_MIN_DIR = PROJECT_ROOT / "aion_flow" / "tools" / "aion_minimizer"
sys.path.insert(0, str(AION_OPT_DIR))
sys.path.insert(0, str(AION_MIN_DIR))

# Site geometry, from the same place scripts/compare_runs.py reads it: the
# placement site is the quantum a cell's width is measured in, and it is the
# unit this script prices a merge in.
from gds_to_image import ROW_HEIGHT_UM, SITE_WIDTH_UM   # noqa: E402

SITE_AREA = SITE_WIDTH_UM * ROW_HEIGHT_UM

from aion_minimizer.gate_extractor import extract_gate_functions  # noqa: E402
from aion_minimizer.sizing import SizingRules                 # noqa: E402
from aion_minimizer.spice_parser import parse_spice_file      # noqa: E402
from aion_minimizer.synthesis import synthesize               # noqa: E402
from aion_opt.cellgen.generator import CellGenerator          # noqa: E402
from aion_opt.cli import cmd_cells_to_spice                   # noqa: E402
from aion_opt.graph.builder import build_signal_flow_graph      # noqa: E402
from aion_opt.io.cell_file import CellModule, library_header, write_library  # noqa: E402
from aion_opt.io.cell_lib import (                             # noqa: E402
    NON_LOGIC_KEYWORDS,
    SEQUENTIAL_KEYWORDS,
    CellLib,
)
from aion_opt.io.selection import Selection, compute_fingerprint  # noqa: E402
from aion_opt.io.verilog_to_json import is_verilog, verilog_to_json  # noqa: E402
from aion_opt.io.yosys_json import load_yosys_json              # noqa: E402
from aion_opt.pattern.cover import CoverResult, pattern_area, select_cover  # noqa: E402
from aion_opt.pattern.miner import (                            # noqa: E402
    MiningGraph,
    MiningResult,
    mine_patterns,
    resolve_jobs,
)
from aion_opt.report.reporter import (                          # noqa: E402
    rank_patterns,
    select_elite_keys,
    write_pattern_report,
)

DEFAULT_NETLIST = PROJECT_ROOT / "flow" / "1_synth" / "nl" / "tt_um_aion.nl.v"
DEFAULT_CELL_LIB = PROJECT_ROOT / "aion_flow" / "tech" / "tech_dict" / "sg13g2_stdcell.json"
DEFAULT_GATES = PROJECT_ROOT / "aion_flow" / "tech" / "spice" / "sg13g2_stdcell.spice"
DEFAULT_WORK = PROJECT_ROOT / "flow" / "explore" / "work"

#: Step 5 refuses a cell with more primary inputs than this
#: (`MINIMIZER_MAX_INPUTS` in scripts/flow/config.py, checked in
#: aion_minimizer/cli.py).  Mining wider patterns is legal and they will be
#: rewritten and pass LEC, but the flow then dies at gate minimization, so a
#: row that needs them is not a result -- it is a trap.
MINIMIZER_MAX_INPUTS = 6


# ---------------------------------------------------------------------------
# Design loading
# ---------------------------------------------------------------------------
@dataclass
class Design:
    cell_lib: CellLib
    circuit: object
    sfg: object
    netlist: Path
    cell_lib_path: Path
    top: str | None
    total_area: float


def load_design(netlist: Path, cell_lib_path: Path, top: str | None,
                work_dir: Path, collapse: bool) -> Design:
    """Read the netlist (converting Verilog through yosys) and build the SFG."""
    work_dir.mkdir(parents=True, exist_ok=True)
    if is_verilog(netlist):
        cached = work_dir / f"{netlist.stem}.json"
        if cached.exists() and cached.stat().st_mtime >= netlist.stat().st_mtime:
            print(f"[explore] reusing {cached}")
            json_path = cached
        else:
            print(f"[explore] yosys: {netlist} -> {cached}")
            json_path = verilog_to_json(netlist, top_module=top, output_json=cached)
    else:
        json_path = netlist

    cell_lib = CellLib(cell_lib_path, collapse_strengths=collapse)
    circuit = load_yosys_json(json_path, cell_lib=cell_lib, top_module=top)
    sfg = build_signal_flow_graph(circuit, cell_lib)
    total = sum(cell_lib.area(cell_lib.collapse_name(inst.cell_type))
                for inst in circuit.instances.values())
    print(f"[explore] {circuit.name}: {len(circuit.instances)} instance(s), "
          f"{len(sfg.node_types)} combinational, {total:,.0f} um2 of standard cells")
    return Design(cell_lib, circuit, sfg, netlist, cell_lib_path, top, total)


# ---------------------------------------------------------------------------
# survey: histogram the raw enumeration, before any filter
# ---------------------------------------------------------------------------
_SURVEY_GRAPH: MiningGraph | None = None
_SURVEY_MAX_SIZE = 5


def _survey_roots(roots: Sequence[int]) -> tuple[Counter, int]:
    """Count every connected subgraph by (size, boundary ins, boundary outs).

    Deliberately does not canonicalise: the question is how many subgraphs
    each cap admits, and skipping canonicalisation is what makes counting the
    unfiltered enumeration affordable in the first place.
    """
    graph = _SURVEY_GRAPH
    assert graph is not None
    adj, node_edges = graph.adj, graph.node_edges
    edge_src, edge_dst = graph.edge_src, graph.edge_dst
    src_pin, dst_pin = graph.edge_src_pin, graph.edge_dst_pin
    max_size = _SURVEY_MAX_SIZE

    hist: Counter = Counter()
    total = 0

    def record(sub: list[int]) -> None:
        nonlocal total
        total += 1
        node_set = set(sub)
        edge_ids: set[int] = set()
        for node in sub:
            edge_ids.update(node_edges[node])
        b_in: set[tuple[int, str]] = set()
        b_out: set[tuple[int, str]] = set()
        for eid in edge_ids:
            s, d = edge_src[eid], edge_dst[eid]
            s_in, d_in = s in node_set, d in node_set
            if s_in and d_in:
                continue
            if s_in:
                b_out.add((s, src_pin[eid]))
            elif d_in:
                b_in.add((d, dst_pin[eid]))
        hist[(len(sub), len(b_in), len(b_out))] += 1

    def extend(sub: list[int], ext: list[int], closed: set[int], root: int) -> None:
        if len(sub) >= 2:
            record(sub)
        if len(sub) >= max_size:
            return
        remaining = list(ext)
        while remaining:
            w = remaining.pop()
            exclusive = [u for u in adj[w] if u > root and u not in closed]
            sub.append(w)
            extend(sub, remaining + exclusive, closed | set(adj[w]) | {w}, root)
            sub.pop()

    for root in roots:
        neighbours = adj[root]
        extend([root], [u for u in neighbours if u > root],
               set(neighbours) | {root}, root)
    return hist, total


def survey(design: Design, max_size: int, jobs: int | None,
           cache: Path | None) -> dict:
    """Histogram every subgraph of the design by size and boundary width."""
    if cache is not None and cache.exists():
        data = json.loads(cache.read_text())
        if data.get("max_size") == max_size:
            print(f"[survey] reusing {cache}")
            return data

    global _SURVEY_GRAPH, _SURVEY_MAX_SIZE
    _SURVEY_GRAPH = MiningGraph.from_sfg(design.sfg)
    _SURVEY_MAX_SIZE = max_size
    n_jobs = resolve_jobs(jobs)
    order = _SURVEY_GRAPH.order
    print(f"[survey] enumerating subgraphs of up to {max_size} cell(s) "
          f"on {n_jobs} worker(s)")

    t0 = time.time()
    tty = sys.stderr.isatty()
    hist: Counter = Counter()
    total = 0
    if n_jobs <= 1 or order < 2 * n_jobs:
        hist, total = _survey_roots(range(order))
    else:
        n_chunks = min(order, n_jobs * 8)
        chunks = [tuple(range(start, order, n_chunks)) for start in range(n_chunks)]
        with mp.get_context("fork").Pool(n_jobs) as pool:
            for done, (partial, count) in enumerate(
                    pool.imap_unordered(_survey_roots, [c for c in chunks if c]), 1):
                hist.update(partial)
                total += count
                if tty:
                    print(f"\r[survey] {done}/{len(chunks)} chunks, "
                          f"{total:,} subgraph(s)",
                          end="", file=sys.stderr, flush=True)
        if tty:
            print("", file=sys.stderr)
    _SURVEY_GRAPH = None

    data = {
        "max_size": max_size,
        "subgraphs": total,
        "seconds": round(time.time() - t0, 1),
        "histogram": {f"{s},{i},{o}": n for (s, i, o), n in sorted(hist.items())},
    }
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, indent=1))
        print(f"[survey] wrote {cache}")
    return data


def print_survey(data: dict) -> None:
    hist = {tuple(int(x) for x in k.split(",")): v
            for k, v in data["histogram"].items()}
    sizes = sorted({s for s, _, _ in hist})
    outs = sorted({o for _, _, o in hist})
    total = data["subgraphs"]

    print(f"\n{total:,} connected subgraph(s) of 2..{data['max_size']} cells "
          f"({data['seconds']}s)\n")

    print("subgraphs by pattern size x boundary outputs")
    print(f"{'size':>6}" + "".join(f"{'out=' + str(o):>14}" for o in outs))
    for s in sizes:
        print(f"{s:>6}" + "".join(f"{sum(v for (a, _, o), v in hist.items() if a == s and o == b):>14,}"
                                 for b in outs))

    print("\nsingle-output subgraphs by pattern size x boundary inputs "
          f"(step 5 refuses more than {MINIMIZER_MAX_INPUTS})")
    caps = [4, 5, 6, 8, 12, 15, None]
    print(f"{'size':>6}" + "".join(f"{('in<=' + str(c)) if c else 'any':>10}" for c in caps))
    for s in sizes:
        row = [sum(v for (a, i, o), v in hist.items()
                   if a == s and o == 1 and (c is None or i <= c)) for c in caps]
        print(f"{s:>6}" + "".join(f"{n:>10,}" for n in row))

    print("\nsubgraphs surviving each (MAX_OUTPUTS, MAX_INPUTS) pair")
    print(f"{'MAX_OUTPUTS':>12}{'MAX_INPUTS':>12}{'kept':>16}{'of total':>11}")
    for mo in (1, 2, 3, None):
        for mi in (6, 8, 15, None):
            kept = sum(v for (_, i, o), v in hist.items()
                       if (mo is None or o <= mo) and (mi is None or i <= mi))
            print(f"{str(mo):>12}{str(mi):>12}{kept:>16,}{100 * kept / total:>10.3f}%")


# ---------------------------------------------------------------------------
# What a merged cell is actually worth
# ---------------------------------------------------------------------------
class _Envelope:
    """Piecewise-linear lower envelope through (x, narrowest width) points.

    Only x values several cells actually demonstrate are kept: one cell is an
    anecdote, and the two singletons in this PDK (``sg13g2_a221oi_1`` at six
    pins, ``sg13g2_mux4_1`` at seven) are both wide for reasons of their own.
    Beyond the supported range the last segment's slope continues, which
    over-estimates width slightly -- and over-estimating width under-estimates
    the saving, which is the direction to be wrong in.
    """

    def __init__(self, samples: dict[int, list[float]], min_support: int = 2):
        self.points = sorted(
            (x, min(widths)) for x, widths in samples.items()
            if len(widths) >= min_support
        )
        if len(self.points) < 2:
            raise RuntimeError("not enough datapoints for a width envelope")

    def __call__(self, x: float) -> float:
        pts = self.points
        if x <= pts[0][0]:
            return pts[0][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x <= x1:
                return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        return y1 + (y1 - y0) * (x - x1) / (x1 - x0)

    def describe(self, unit: str) -> str:
        return " ".join(f"{x}{unit}->{y:.0f}s" for x, y in self.points)


class SiteModel:
    """How wide a cell of ``n`` transistors and ``p`` pins has to be.

    `AREA_FACTOR` is a guess made before anything is drawn; this is a
    measurement.  Every combinational cell in the PDK is one datapoint of "n
    transistors and p pins, drawn as tightly as this library draws them", and
    the lower envelope of that cloud is what a good layout of the same costs.
    A cell has to hold its devices *and* its pins, so both envelopes are lower
    bounds and the model takes the larger.

    Step 6 landed exactly on it the one time it was asked:
    ``AION_nand2_o21ai_0`` is 10 transistors and 5 pins in 8 sites, and the
    device bound for 10 transistors is 8 sites (``sg13g2_a221oi_1``).  That is
    the whole justification for using the envelope to price cells nobody has
    drawn yet -- and it is a best case, so a cell whose pins route badly will
    come out wider than this says.
    """

    def __init__(self, gates_spice: Path, cell_lib: CellLib) -> None:
        by_devices: dict[int, list[float]] = defaultdict(list)
        by_pins: dict[int, list[float]] = defaultdict(list)
        for name, subckt in parse_spice_file(str(gates_spice)).items():
            entry = cell_lib.raw.get(name)
            devices = len(subckt.mosfets)
            if not entry or not devices or not _is_logic(name):
                continue
            signal_pins = sum(1 for pin in subckt.pins
                              if pin.upper() not in ("VDD", "VSS"))
            sites = float(entry["area"]) / SITE_AREA
            by_devices[devices].append(sites)
            by_pins[signal_pins].append(sites)
        self.by_devices = _Envelope(by_devices)
        self.by_pins = _Envelope(by_pins)

    def sites(self, devices: int, pins: int) -> float:
        """Narrowest plausible width, in placement sites."""
        return max(self.by_devices(devices), self.by_pins(pins))

    def describe(self) -> str:
        return (f"devices {self.by_devices.describe('T')} | "
                f"pins {self.by_pins.describe('P')}")


def _is_logic(name: str) -> bool:
    """A combinational cell that carries a logic function.

    Sequential cells are excluded because a flip-flop's width is set by its
    clock and reset structure, and they are the only cells above 12 devices --
    leaving them in would bend the curve exactly where merged cells live.
    Fills, taps and decaps carry no function and no useful width.
    """
    lowered = name.lower()
    return not any(word in lowered
                   for word in (*SEQUENTIAL_KEYWORDS, *NON_LOGIC_KEYWORDS))


@dataclass
class MergeScore:
    """What step 5 does to one pattern, and what that is worth in area."""

    module: str = ""
    devices_before: int = 0
    devices_after: int = 0
    merged_stages: int = 0
    pins: int = 0
    pdk_sites: float = 0.0
    drawn_sites: float = 0.0
    error: str = ""

    @property
    def devices_saved(self) -> int:
        return self.devices_before - self.devices_after

    @property
    def saved_sites(self) -> float:
        return self.pdk_sites - self.drawn_sites

    @property
    def saved_area(self) -> float:
        """Area a substitution buys, floored at zero.

        A cell the synthesizer cannot shrink below the abutted PDK cells is
        not worth a layout, and a zero here is what keeps it out of the
        budgeted cover.
        """
        return max(0.0, self.saved_sites) * SITE_AREA


def validate_site_model(model: SiteModel) -> None:
    """Check the width model against every cell step 6 has actually drawn.

    The model is the whole basis for pricing a cell nobody has drawn, so it
    should be made to answer for itself on the ones that exist.  Silent when
    the flow has not run that far.
    """
    drawn = sorted((PROJECT_ROOT / "implementation" / "cells").glob("*/*.lef"))
    minimized = PROJECT_ROOT / "flow" / "5_gate_minimization" / "minimized_spice"
    rows = []
    for lef in drawn:
        spice = minimized / f"{lef.stem}_minimized.spice"
        match = re.search(r"^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;",
                          lef.read_text(encoding="utf-8"), re.MULTILINE)
        if match is None or not spice.exists():
            continue
        subckt = next(iter(parse_spice_file(str(spice)).values()))
        pins = sum(1 for pin in subckt.pins if pin.upper() not in ("VDD", "VSS"))
        rows.append((lef.stem, len(subckt.mosfets), pins,
                     float(match.group(1)) / SITE_WIDTH_UM,
                     model.sites(len(subckt.mosfets), pins)))
    if not rows:
        return
    print(f"\n[score] the model against the {len(rows)} cell(s) step 6 has drawn")
    print(f"  {'cell':<28}{'dev':>5}{'pins':>6}{'drawn':>8}{'predicted':>11}{'error':>8}")
    for name, devices, pins, actual, predicted in rows:
        print(f"  {name:<28}{devices:>5}{pins:>6}{actual:>8.0f}{predicted:>11.1f}"
              f"{predicted - actual:>+8.1f}")
    print()


#: Set in the parent before the scoring pool forks; inherited read-only.
_SCORE_GATES: dict = {}
_SCORE_FUNCTIONS: dict = {}
_SCORE_SKIPPED: dict = {}
_SCORE_RULES: "SizingRules | None" = None
_SCORE_MODE = "transistor"
_SCORE_MAX_STACK = 4


def _score_one(path: Path) -> tuple[str, int, int, int, str]:
    """Synthesize one gate-level cell exactly as step 5 would."""
    subckts = parse_spice_file(str(path))
    name, top = next(iter(subckts.items()))
    try:
        result = synthesize(
            top, _SCORE_FUNCTIONS, _SCORE_GATES,
            skipped=_SCORE_SKIPPED,
            mode=_SCORE_MODE,
            rules=_SCORE_RULES,
            max_stack_depth=_SCORE_MAX_STACK,
        )
    except Exception as exc:                      # step 5 would fail here too
        return name, 0, 0, 0, f"{type(exc).__name__}: {exc}"[:120]
    return (name, result.original_transistors, result.transistors,
            result.merged_stages, "")


def score_merges(keys: Sequence[str], mining: MiningResult, design: Design,
                 site_model: SiteModel, args: argparse.Namespace,
                 work_dir: Path) -> dict[str, MergeScore]:
    """Price every pattern in ``keys`` by what step 5 actually does to it.

    Generates the cell library, converts it to the gate-level SPICE step 5
    consumes (``aion_opt cells-to-spice``, the same function the flow calls)
    and runs ``aion_minimizer``'s synthesizer on each.  All of that is pure
    Python: no container, no ngspice.  Only the exhaustive verification step 5
    also runs needs those, and a wrong transistor count here would show up as
    a wrong prediction, not as a wrong chip.
    """
    global _SCORE_GATES, _SCORE_FUNCTIONS, _SCORE_SKIPPED
    global _SCORE_RULES, _SCORE_MODE, _SCORE_MAX_STACK

    generator = CellGenerator(prefix=args.cell_prefix)
    modules: list[CellModule] = []
    by_module: dict[str, str] = {}
    for index, key in enumerate(keys):
        pattern = mining.representative(key)
        name = generator.module_name(pattern, index)
        text = generator.generate_cell(pattern, index, design.cell_lib)
        lines = text.splitlines(keepends=True)
        while lines and lines[0].lstrip().startswith("// AION "):
            lines.pop(0)
        modules.append(CellModule(name=name, text="".join(lines),
                                  canonical_key=key))
        by_module[name] = key

    work_dir.mkdir(parents=True, exist_ok=True)
    cells = work_dir / "score_cells.v"
    spice_dir = work_dir / "score_spice"
    shutil.rmtree(spice_dir, ignore_errors=True)
    write_library(cells, modules, library_header("AION Optimizer - scoring"))
    cmd_cells_to_spice(argparse.Namespace(
        cells=cells, gates=args.gates, output_dir=spice_dir,
        cell_prefix=args.cell_prefix, vdd="VDD", vss="VSS", quiet=True))

    _SCORE_GATES = parse_spice_file(str(args.gates))
    _SCORE_SKIPPED = {}
    _SCORE_FUNCTIONS = extract_gate_functions(_SCORE_GATES, _SCORE_SKIPPED)
    _SCORE_RULES = SizingRules(wn=args.wn, wp=args.wp, l=args.l)
    _SCORE_MODE = args.minimizer_mode
    _SCORE_MAX_STACK = args.max_stack_depth

    files = sorted(spice_dir.glob("*.spice"))
    print(f"[score] running step 5's synthesizer on {len(files)} pattern(s)")
    t0 = time.time()
    n_jobs = resolve_jobs(args.jobs)
    if n_jobs <= 1 or len(files) < 2 * n_jobs:
        raw = [_score_one(f) for f in files]
    else:
        with mp.get_context("fork").Pool(n_jobs) as pool:
            raw = list(pool.imap_unordered(_score_one, files, chunksize=8))

    scores: dict[str, MergeScore] = {}
    for name, before, after, merged, error in raw:
        key = by_module.get(name)
        if key is None:
            continue
        pattern = mining.representative(key)
        pdk = pattern_area(pattern.node_types, design.cell_lib) / SITE_AREA
        pins = len(pattern.boundary_inputs) + len(pattern.boundary_outputs)
        scores[key] = MergeScore(
            module=name,
            devices_before=before,
            devices_after=after,
            merged_stages=merged,
            pins=pins,
            # A cell the synthesizer refused is priced at no saving at all,
            # not at a small one: step 5 would stop the flow on it.
            pdk_sites=pdk,
            drawn_sites=pdk if error else site_model.sites(after, pins),
            error=error,
        )

    shrinking = sum(1 for s in scores.values() if s.devices_saved > 0)
    failed = sum(1 for s in scores.values() if s.error)
    print(f"[score] {shrinking} of {len(scores)} pattern(s) shed transistors, "
          f"{failed} the synthesizer refused, in {time.time() - t0:.0f}s")
    return scores


# ---------------------------------------------------------------------------
# sweep: one mining pass, every grid point derived from it
# ---------------------------------------------------------------------------
@dataclass
class KeyInfo:
    """Everything about a pattern that a grid point needs to filter on."""

    size: int
    inputs: int
    outputs: int


@dataclass
class Row:
    """One grid point, scored under both selection strategies."""

    min_size: int
    max_size: int
    max_outputs: int
    max_inputs: int
    min_occurrences: int
    min_selected: int
    budget: int
    metric: str
    candidates: int = 0
    cover_types: int = 0
    cover_sites: int = 0
    flow_saved: float = 0.0
    flow_sizes: Counter = field(default_factory=Counter)
    flow_max_inputs: int = 0
    budget_saved: float = 0.0
    budget_sites: int = 0
    budget_cells: int = 0
    budget_sizes: Counter = field(default_factory=Counter)
    budget_max_inputs: int = 0
    budget_keys: list[str] = field(default_factory=list)
    budget_cover: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    #: --score merge only: transistors the budgeted cover removes from the
    #: netlist, and how many of its cells shed any at all.
    budget_devices: int = 0
    budget_merging: int = 0
    seconds: float = 0.0

    def sizes_text(self, counter: Counter) -> str:
        return "+".join(f"{n}x{s}" for s, n in sorted(counter.items())) or "-"


def key_infos(mining: MiningResult) -> dict[str, KeyInfo]:
    """Size / boundary width / area per canonical key.

    All of these are encoded in the key itself, so one representative answers
    for every occurrence -- which is what makes a tighter grid point a pure
    dictionary filter instead of another mining pass.
    """
    infos: dict[str, KeyInfo] = {}
    for key in mining.occurrences:
        pattern = mining.representative(key)
        infos[key] = KeyInfo(
            size=pattern.size(),
            inputs=len(pattern.boundary_inputs),
            outputs=len(pattern.boundary_outputs),
        )
    return infos


def restrict(mining: MiningResult, design: Design, keys: Iterable[str],
             min_occurrences: int) -> MiningResult:
    """The mining result a tighter grid point would have produced."""
    keep = set(keys)
    sub = MiningResult(
        occurrences={k: v for k, v in mining.occurrences.items()
                     if k in keep and len(v) >= min_occurrences},
        subgraphs_enumerated=mining.subgraphs_enumerated,
        # Share the representative cache: rebuilding a Pattern means walking
        # the netlist again, and every grid point wants the same ones.
        _representatives=mining._representatives,
    )
    sub.bind(design.circuit, design.sfg, design.cell_lib.collapse_name)
    return sub


def budget_cover(
    occurrences: dict[str, list[tuple[str, ...]]],
    saved_per_occ: dict[str, float],
    budget: int,
    min_selected: int,
) -> tuple[list[str], list[tuple[str, tuple[str, ...]]]]:
    """Pick ``budget`` patterns to maximise the area a disjoint cover buys.

    Maximum coverage: NP-hard, and greedy is the standard 1-1/e answer.  Each
    round scores every remaining pattern by what it *still* buys once the
    already-chosen cells have claimed their instances, which is the whole
    difference from the flow's "cover everything, then keep the best N" --
    that ranks patterns against a cover they will not get.
    """
    sets = {k: [frozenset(o) for o in occs] for k, occs in occurrences.items()}
    chosen: list[str] = []
    selected: list[tuple[str, tuple[str, ...]]] = []
    used: set[str] = set()
    remaining = set(sets)

    while len(chosen) < budget and remaining:
        # Nothing can beat "every occurrence still free", so patterns are tried
        # in that order and the loop stops as soon as the bound falls below the
        # best real gain found so far.
        order = sorted(remaining,
                       key=lambda k: (-saved_per_occ[k] * len(sets[k]), k))
        best_gain = 0.0
        best: tuple[str, list[frozenset[str]]] | None = None
        for key in order:
            if saved_per_occ[key] * len(sets[key]) <= best_gain:
                break
            packed: list[frozenset[str]] = []
            claimed: set[str] = set()
            for occ in sets[key]:
                if used.isdisjoint(occ) and claimed.isdisjoint(occ):
                    packed.append(occ)
                    claimed |= occ
            if len(packed) < min_selected:
                continue
            gain = saved_per_occ[key] * len(packed)
            if gain > best_gain:
                best_gain, best = gain, (key, packed)
        if best is None:
            break
        key, packed = best
        chosen.append(key)
        remaining.discard(key)
        for occ in packed:
            selected.append((key, tuple(sorted(occ))))
            used |= occ

    return chosen, selected


def evaluate(row: Row, mining: MiningResult, design: Design,
             infos: dict[str, KeyInfo], area_factor: float,
             minimizer_max_inputs: int,
             scores: dict[str, MergeScore] | None = None) -> Row:
    """Score one grid point under both selection strategies.

    ``scores`` switches the currency.  Without it a substitution is worth
    ``area x (1 - AREA_FACTOR)``, which is what the flow assumes.  With it, a
    substitution is worth the placement sites step 5's synthesizer actually
    frees -- so a pattern the synthesizer cannot collapse is priced at the one
    diffusion break it saves, and one it can is priced at what it really buys.
    """
    t0 = time.time()
    keys = [k for k, i in infos.items()
            if row.min_size <= i.size <= row.max_size
            and i.inputs <= row.max_inputs
            and i.outputs <= row.max_outputs
            # A cell step 5 cannot minimize is not a candidate at all: the
            # flow would mine it, rewrite it, pass LEC and then stop.
            and i.inputs <= minimizer_max_inputs]
    sub = restrict(mining, design, keys, row.min_occurrences)
    row.candidates = len(sub.occurrences)
    if not sub.occurrences:
        row.seconds = time.time() - t0
        return row

    cover = select_cover(sub, design.cell_lib, area_factor=area_factor,
                         min_selected_occurrences=row.min_selected)
    row.cover_types = len(cover.counts)
    row.cover_sites = len(cover.selected)

    # The currency both strategies are settled in. `select_cover` always
    # ranks by area -- that is what the flow does, and the flow strategy has
    # to be scored on the cover it would really get -- but what a selected
    # occurrence is *worth* is a separate question, and --score merge answers
    # it with a measurement.
    worth = (cover.saved_area_per_occurrence if scores is None
             else {k: scores[k].saved_area for k in sub.occurrences
                   if k in scores and scores[k].saved_area > 0.0})

    # --- what the flow does today, priced in the same currency
    elite = select_elite_keys(cover, row.budget, row.metric)
    row.flow_saved = sum(worth.get(k, 0.0) * cover.counts[k] for k in elite)
    row.flow_sizes = Counter(infos[k].size for k in elite)
    row.flow_max_inputs = max((infos[k].inputs for k in elite), default=0)

    # --- budget-first
    candidates = {k: v for k, v in sub.occurrences.items() if k in worth}
    chosen, selected = budget_cover(candidates, worth,
                                    row.budget, row.min_selected)
    counts = Counter(k for k, _ in selected)
    row.budget_saved = sum(worth[k] * n for k, n in counts.items())
    if scores is not None:
        row.budget_devices = sum(scores[k].devices_saved * n
                                 for k, n in counts.items())
        row.budget_merging = sum(1 for k in chosen if scores[k].devices_saved > 0)
    row.budget_sites = len(selected)
    row.budget_cells = len(chosen)
    row.budget_sizes = Counter(infos[k].size for k in chosen)
    row.budget_max_inputs = max((infos[k].inputs for k in chosen), default=0)
    row.budget_keys = chosen
    row.budget_cover = selected
    row.seconds = time.time() - t0
    return row


def sweep(design: Design, args: argparse.Namespace) -> tuple[
        list[Row], MiningResult, "dict[str, MergeScore] | None"]:
    """Mine once at the loosest corner, then score every grid point."""
    max_size = max(args.max_size)
    max_outputs = max(args.max_outputs)
    max_inputs = max(args.max_inputs)
    min_occurrences = min(args.min_occurrences)

    print(f"[sweep] one mining pass at the loosest corner: max_size={max_size}, "
          f"max_outputs={max_outputs}, max_inputs={max_inputs}, "
          f"min_occurrences={min_occurrences}")
    t0 = time.time()
    mining = mine_patterns(
        design.circuit, design.sfg, design.cell_lib,
        max_size=max_size,
        min_occurrences=min_occurrences,
        max_outputs=max_outputs,
        max_inputs=max_inputs,
        jobs=args.jobs,
        progress=True,
    )
    print(f"[sweep] {mining.subgraphs_enumerated:,} subgraph(s) -> "
          f"{len(mining.occurrences)} pattern type(s), "
          f"{mining.total_occurrences():,} occurrence(s) in {time.time() - t0:.0f}s")

    infos = key_infos(mining)

    scores: dict[str, MergeScore] | None = None
    if args.score == "merge":
        site_model = SiteModel(args.gates, design.cell_lib)
        print(f"[score] PDK width envelope: {site_model.describe()}")
        validate_site_model(site_model)
        # Only patterns that could enter a budget are worth synthesizing, and
        # `pdk sites x occurrences` is an upper bound on what any of them can
        # save -- a merge can never free more than the cells it replaces --
        # so cutting the list on it cannot discard a pattern that would have
        # beaten the ones kept.
        shortlist = sorted(
            (k for k, i in infos.items() if i.inputs <= args.minimizer_max_inputs),
            key=lambda k: (-pattern_area(mining.representative(k).node_types,
                                         design.cell_lib)
                           * len(mining.occurrences[k]), k),
        )[:args.score_limit]
        scores = score_merges(shortlist, mining, design, site_model, args,
                              args.work_dir)

    grid = [g for g in itertools.product(
                args.min_size, args.max_size, args.max_outputs, args.max_inputs,
                args.min_occurrences, args.min_selected, args.budget, args.metric)
            if g[0] <= g[1]]
    print(f"[sweep] scoring {len(grid)} grid point(s)")
    rows: list[Row] = []
    for n, (low, size, outs, ins, occ, sel, budget, metric) in enumerate(grid, 1):
        if sel is None:
            sel = occ
        row = evaluate(Row(low, size, outs, ins, occ, sel, budget, metric),
                       mining, design, infos, args.area_factor,
                       args.minimizer_max_inputs, scores)
        rows.append(row)
        if sys.stderr.isatty():
            print(f"\r[sweep] {n}/{len(grid)}", end="", file=sys.stderr, flush=True)
    if sys.stderr.isatty():
        print("", file=sys.stderr)
    return rows, mining, scores


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_rows(rows: list[Row], design: Design, args: argparse.Namespace,
               top: int) -> None:
    merge = args.score == "merge"
    ranked = sorted(rows, key=lambda r: -r.budget_saved)[:top]
    print(f"\nbest {len(ranked)} of {len(rows)} grid point(s), by the area the "
          f"budgeted selection buys")
    if merge:
        print("(--score merge: every saving below is placement sites step 5's "
              "synthesizer actually frees,\n larger of the two columns priced "
              "the same way. AREA_FACTOR is not used.)\n")
    else:
        print(f"(AREA_FACTOR={args.area_factor}: an AION cell is assumed to cost "
              f"{args.area_factor:.0%} of the cells it replaces, so each "
              f"substitution saves {1 - args.area_factor:.0%})\n")
    extra = f"{'merge':>7}{'devices':>9}" if merge else f"{'cell sizes':>16}"
    head = (f"{'SIZE':>7}{'OUT':>4}{'IN':>4}{'OCC':>4}{'SEL':>4}{'N':>4}"
            f"{'budget um2':>12}{'%die':>7}{'sites':>7}{extra}"
            f"{'flow um2':>11}{'gain':>8}")
    print(head)
    print("-" * len(head))
    for r in ranked:
        gain = (r.budget_saved / r.flow_saved - 1) if r.flow_saved else 0.0
        span = f"{r.min_size}-{r.max_size}" if r.min_size > 2 else str(r.max_size)
        cells = (f"{f'{r.budget_merging}/{r.budget_cells}':>7}"
                 f"{-r.budget_devices:>9}") if merge else \
                f"{r.sizes_text(r.budget_sizes):>16}"
        print(f"{span:>7}{r.max_outputs:>4}{r.max_inputs:>4}"
              f"{r.min_occurrences:>4}{r.min_selected:>4}{r.budget_cells:>4}"
              f"{r.budget_saved:>12.1f}{100 * r.budget_saved / design.total_area:>6.2f}%"
              f"{r.budget_sites:>7}{cells}"
              f"{r.flow_saved:>11.1f}{gain:>7.0%}")


def rows_to_json(rows: list[Row], design: Design, args: argparse.Namespace,
                 scores: "dict[str, MergeScore] | None") -> dict:
    return {
        "design": {
            "netlist": str(design.netlist),
            "instances": len(design.circuit.instances),
            "standard_cell_area": design.total_area,
        },
        "score": args.score,
        "site_area_um2": SITE_AREA,
        "area_factor": args.area_factor,
        "merge_scores": None if scores is None else {
            s.module: {
                "pattern_key": key,
                "devices_before": s.devices_before,
                "devices_after": s.devices_after,
                "merged_stages": s.merged_stages,
                "pins": s.pins,
                "pdk_sites": round(s.pdk_sites, 2),
                "drawn_sites": round(s.drawn_sites, 2),
                "saved_area_per_occurrence": round(s.saved_area, 4),
                **({"error": s.error} if s.error else {}),
            }
            for key, s in sorted(scores.items(), key=lambda kv: -kv[1].saved_area)
        },
        "rows": [
            {
                "min_size": r.min_size,
                "max_size": r.max_size,
                "max_outputs": r.max_outputs,
                "max_inputs": r.max_inputs,
                "min_occurrences": r.min_occurrences,
                "min_selected": r.min_selected,
                "elite_count": r.budget,
                "elite_metric": r.metric,
                "candidate_patterns": r.candidates,
                "cover_types": r.cover_types,
                "cover_sites": r.cover_sites,
                "flow_saved_area": r.flow_saved,
                "flow_cell_sizes": dict(sorted(r.flow_sizes.items())),
                "flow_max_inputs": r.flow_max_inputs,
                "budget_saved_area": r.budget_saved,
                "budget_cells": r.budget_cells,
                "budget_sites": r.budget_sites,
                "budget_cell_sizes": dict(sorted(r.budget_sizes.items())),
                "budget_max_inputs": r.budget_max_inputs,
                "budget_saved_fraction": r.budget_saved / design.total_area,
                "budget_devices_removed": r.budget_devices,
                "budget_cells_that_merge": r.budget_merging,
                "seconds": round(r.seconds, 2),
            }
            for r in sorted(rows, key=lambda r: -r.budget_saved)
        ],
    }


def recommend(row: Row) -> str:
    return (f"./flow.py 2 --set MAX_SIZE={row.max_size} "
            f"--set MAX_OUTPUTS={row.max_outputs} "
            f"--set MAX_INPUTS={row.max_inputs} "
            f"--set MIN_OCCURRENCES={row.min_occurrences} "
            f"--set MIN_SELECTED={row.min_selected} "
            f"--set ELITE_COUNT={row.budget} "
            f"--set ELITE_METRIC={row.metric}")


# ---------------------------------------------------------------------------
# emit: write the winning row as a drop-in step-2 output directory
# ---------------------------------------------------------------------------
def emit(row: Row, mining: MiningResult, design: Design, outdir: Path,
         args: argparse.Namespace,
         scores: "dict[str, MergeScore] | None" = None) -> None:
    """Write cells + selection for ``row``'s budgeted cover.

    The layout matches flow/2_pattern_extraction so step 3 can be pointed at
    it unchanged.  The selection's fingerprint is computed from the mining
    parameters step 3 will be invoked with, which is what makes it a cache hit
    rather than a silent re-mine of the flow's own (worse) cover.
    """
    keys = list(row.budget_keys)
    sub = restrict(mining, design, keys, 1)
    counts = Counter(k for k, _ in row.budget_cover)
    # The report and the module numbering rank on whatever currency the row
    # was chosen with, so `..._0` is the most valuable cell by the same
    # measure that picked the library.
    cover = CoverResult(
        selected=list(row.budget_cover),
        counts=counts,
        saved_area_per_occurrence={
            k: (scores[k].saved_area if scores is not None and k in scores
                else pattern_area(sub.representative(k).node_types, design.cell_lib)
                * (1.0 - args.area_factor))
            for k in counts
        },
        iterations=1,
    )

    generator = CellGenerator(prefix=args.cell_prefix)
    ranked = rank_patterns(cover)
    module_names = {k: generator.module_name(sub.representative(k), i)
                    for i, k in enumerate(ranked)}
    modules = []
    for index, key in enumerate(ranked):
        text = generator.generate_cell(sub.representative(key), index, design.cell_lib)
        lines = text.splitlines(keepends=True)
        while lines and lines[0].lstrip().startswith("// AION "):
            lines.pop(0)
        modules.append(CellModule(name=module_names[key], text="".join(lines),
                                  canonical_key=key))

    notes = [
        f"design: {design.circuit.name}",
        f"chosen by: scripts/explore_cells.py --score {args.score} "
        f"(budget {row.budget})",
        f"cells: {len(modules)}",
        f"occurrences replaced: {len(cover.selected)}",
        f"estimated area saved: {cover.total_saved_area_all():.4f}",
    ]
    if scores is not None:
        notes += [
            f"transistors removed: {row.budget_devices}",
            f"cells that shed transistors: {row.budget_merging} of {len(modules)}",
            "priced from aion_minimizer, not from AREA_FACTOR",
        ]
    header = library_header("AION Optimizer - budgeted selection", notes)
    cells = outdir / "aion_cells.v"
    write_library(cells, modules, header)
    write_library(outdir / "aion_cells_elite.v", modules, header)
    write_pattern_report(outdir / "report" / "pattern_report.json", sub, cover,
                         module_names, design.cell_lib,
                         area_factor=args.area_factor, elite_keys=ranked,
                         parameters=_flow_parameters(row, args))

    selection = outdir / "work" / "selection.json"
    Selection(
        fingerprint=compute_fingerprint(design.netlist, design.cell_lib_path,
                                        design.top, _flow_parameters(row, args)),
        parameters=_flow_parameters(row, args),
        module_names=module_names,
        occurrences=[(k, list(insts)) for k, insts in cover.selected],
        saved_area_per_occurrence=dict(cover.saved_area_per_occurrence),
    ).write(selection)

    print(f"\n[emit] {len(modules)} cell(s), {len(cover.selected)} site(s), "
          f"{cover.total_saved_area_all():,.1f} um2 -> {outdir}")
    if scores is not None:
        print(f"[emit] {row.budget_merging} of {len(modules)} cell(s) shed "
              f"transistors; {row.budget_devices} device(s) leave the netlist")
        print(f"\n{'cell':<30}{'uses':>6}{'dev':>10}{'sites':>13}{'um2':>10}")
        for key in ranked:
            sc = scores.get(key)
            if sc is None:
                continue
            print(f"{module_names[key]:<30}{counts[key]:>6}"
                  f"{f'{sc.devices_before}->{sc.devices_after}':>10}"
                  f"{f'{sc.pdk_sites:.0f}->{sc.drawn_sites:.1f}':>13}"
                  f"{sc.saved_area * counts[key]:>10.1f}")
        print()
    print("[emit] to rewrite with exactly this selection:")
    print(f"  cp {selection} flow/2_pattern_extraction/work/selection.json")
    print(f"  ./flow.py 3 --set REWRITE_CELLS={cells} \\")
    print(f"      --set MAX_SIZE={row.max_size} --set MAX_OUTPUTS={row.max_outputs} "
          f"--set MAX_INPUTS={row.max_inputs} \\")
    print(f"      --set MIN_OCCURRENCES={row.min_occurrences} "
          f"--set MIN_SELECTED={row.min_selected} "
          f"--set AREA_FACTOR={args.area_factor}")


def _flow_parameters(row: Row, args: argparse.Namespace) -> dict:
    """The parameter dict aion_opt fingerprints a selection with.

    Must match `_mining_parameters` in aion_opt/cli.py key for key, or step 3
    calls the selection stale and re-mines.
    """
    return {
        "max_size": row.max_size,
        "min_occurrences": row.min_occurrences,
        "min_selected": row.min_selected,
        "area_factor": args.area_factor,
        "max_outputs": row.max_outputs,
        "max_inputs": row.max_inputs,
        "cell_prefix": args.cell_prefix,
        "collapse_strengths": args.collapse_strengths,
        "allow_overlapping": False,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _int_list(text: str) -> list[int]:
    return [int(x) for x in text.replace(",", " ").split()]


def _opt_int_list(text: str) -> list[int | None]:
    return [None if x.lower() in ("none", "auto") else int(x)
            for x in text.replace(",", " ").split()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore the AION pattern-mining space and pick the cell "
                    "library that buys the most area.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  scripts/explore_cells.py survey\n"
               "  scripts/explore_cells.py sweep\n"
               "  scripts/explore_cells.py sweep --budget 25 --emit flow/explore/best\n"
               "  scripts/explore_cells.py sweep --score merge --emit flow/explore/best\n")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("survey", "sweep"):
        p = sub.add_parser(name)
        p.add_argument("--netlist", type=Path, default=DEFAULT_NETLIST)
        p.add_argument("--cell-lib", type=Path, default=DEFAULT_CELL_LIB)
        p.add_argument("--top", default="tt_um_aion")
        p.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
        p.add_argument("--jobs", type=int, default=None,
                       help="mining workers (0 or omitted = every core)")
        p.add_argument("--no-collapse-strengths", dest="collapse_strengths",
                       action="store_false", default=True)

    s = sub.choices["survey"]
    s.add_argument("--max-size", type=int, default=5)
    s.add_argument("--cache", type=Path,
                   default=PROJECT_ROOT / "flow" / "explore" / "survey.json")
    s.add_argument("--no-cache", dest="cache", action="store_const", const=None)

    w = sub.choices["sweep"]
    w.add_argument("--max-size", type=_int_list, default=[3, 4, 5],
                   metavar="LIST", help="MAX_SIZE values to try")
    w.add_argument("--min-size", type=_int_list, default=[2], metavar="LIST",
                   help="floor on the pattern size, to ask what a library of "
                        "*only* wide cells would buy. Not a flow knob: the "
                        "flow always mines from 2 up, so a row with min-size "
                        "above 2 is only reachable through --emit.")
    w.add_argument("--max-outputs", type=_int_list, default=[1, 2],
                   metavar="LIST", help="MAX_OUTPUTS values to try")
    w.add_argument("--max-inputs", type=_int_list, default=[4, 5, 6],
                   metavar="LIST", help="MAX_INPUTS values to try")
    w.add_argument("--min-occurrences", type=_int_list, default=[2, 3],
                   metavar="LIST")
    w.add_argument("--min-selected", type=_opt_int_list, default=[1, 2],
                   metavar="LIST", help="'none' means the MIN_OCCURRENCES value")
    w.add_argument("--budget", type=_int_list, default=[25], metavar="LIST",
                   help="ELITE_COUNT values to try -- one hand-drawn layout each")
    w.add_argument("--metric", type=lambda s: s.split(","),
                   default=["saved-area"], metavar="LIST")
    w.add_argument("--area-factor", type=float, default=0.85,
                   help="assumed AION cell area relative to the cells it "
                        "replaces (0.85 = a 15%% saving per substitution). "
                        "A uniform scale on every saving, so it moves the "
                        "numbers, never the ranking.")
    w.add_argument("--minimizer-max-inputs", type=int, default=MINIMIZER_MAX_INPUTS,
                   help="drop patterns step 5 could not minimize "
                        "(MINIMIZER_MAX_INPUTS; 0 disables the check)")
    w.add_argument("--score", choices=("area", "merge"), default="area",
                   help="how a substitution is priced. 'area' uses "
                        "AREA_FACTOR, which is what the flow assumes. 'merge' "
                        "runs step 5's synthesizer on every candidate and "
                        "prices it in the placement sites it actually frees, "
                        "so a pattern that cannot collapse is worth its one "
                        "diffusion break and no more.")
    w.add_argument("--score-limit", type=int, default=2000, metavar="N",
                   help="how many patterns --score merge synthesizes, taken "
                        "in decreasing order of the most they could possibly "
                        "save (default 2000, ~40 ms each, run in parallel)")
    w.add_argument("--gates", type=Path, default=DEFAULT_GATES,
                   help="PDK gate-level SPICE the synthesizer reads")
    w.add_argument("--minimizer-mode", default="transistor",
                   choices=("transistor", "area", "balance"),
                   help="MINIMIZER_MODE")
    w.add_argument("--wn", default="0.74u", help="MINIMIZER_WN")
    w.add_argument("--wp", default="1.48u", help="MINIMIZER_WP")
    w.add_argument("--l", default="0.13u", help="MINIMIZER_L")
    w.add_argument("--max-stack-depth", type=int, default=4,
                   help="deepest series stack the synthesizer may build")
    w.add_argument("--cell-prefix", default="AION_")
    w.add_argument("--top-rows", type=int, default=20)
    w.add_argument("--json", type=Path,
                   default=PROJECT_ROOT / "flow" / "explore" / "sweep.json")
    w.add_argument("--emit", type=Path, default=None,
                   help="write the winning row's cells + selection here")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sweep" and args.minimizer_max_inputs <= 0:
        args.minimizer_max_inputs = 1 << 30

    design = load_design(args.netlist, args.cell_lib, args.top,
                         args.work_dir, args.collapse_strengths)

    if args.command == "survey":
        print_survey(survey(design, args.max_size, args.jobs, args.cache))
        return 0

    rows, mining, scores = sweep(design, args)
    print_rows(rows, design, args, args.top_rows)

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows_to_json(rows, design, args, scores),
                                        indent=2))
        print(f"\n[sweep] wrote {args.json}")

    best = max(rows, key=lambda r: r.budget_saved)
    if best.budget_saved <= 0:
        print("\nno grid point produced a usable selection")
        return 1

    print(f"\nbest row: {best.budget_saved:,.1f} um2 over {best.budget_cells} cell(s) "
          f"({100 * best.budget_saved / design.total_area:.2f}% of the standard-cell "
          f"area), sizes {best.sizes_text(best.budget_sizes)}")
    print(f"the same knobs through the flow's own selection: "
          f"{best.flow_saved:,.1f} um2, sizes {best.sizes_text(best.flow_sizes)}")
    if scores is not None:
        print(f"{best.budget_merging} of {best.budget_cells} cell(s) shed "
              f"transistors; {best.budget_devices} device(s) leave the netlist "
              f"across {best.budget_sites} site(s)")
    print(f"\n  {recommend(best)}")
    if best.min_size > 2:
        print(f"  ...but this row keeps only patterns of {best.min_size} cell(s) "
              f"or more, which no flow knob expresses. Use --emit and point "
              f"step 3 at the library it writes.")

    if args.emit is not None:
        args.emit.mkdir(parents=True, exist_ok=True)
        emit(best, mining, design, args.emit, args, scores)
    return 0


if __name__ == "__main__":
    sys.exit(main())
