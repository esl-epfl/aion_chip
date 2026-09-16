#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Rank the Boolean functions a netlist builds
#                             out of several cells -- the candidates for a
#                             hand-designed PDK-extension cell.
#
#  The step-2 miner is structural.  A pattern is a set of cell types wired
#  pin to pin, and the cell it generates gets one port per pin.  That is the
#  right model for merging cells that already sit next to each other, and it
#  is blind to a function the netlist spreads over shared nets or builds in
#  several structural variants.
#
#  Majority is the worked example.  In the PDK-only tt_um_aion netlist:
#
#    * 640 gates compute a majority of three signals.
#    * 616 of them share the half-sum XOR with the sum, so the miner has to
#      cut there and mines the carry as a 4-input function.
#    * The full-adder shapes it could take at MAX_OUTPUTS=2 are spread over
#      82 canonical keys.
#    * a and b reach two pins each, so a mined cell is a 6-input function
#      and never maj(a, b, c).
#
#  Yet ABC mapped AION_maj3i_1 745 times the moment the cell existed.
#
#  This does what ABC does.  Every gate's k-input cuts are enumerated and
#  the function over each cut's leaves is computed.  Functions are grouped by
#  NPN class: the same function up to input order, input polarity and output
#  polarity, which is what one cell plus the inverters already in the
#  netlist can implement.  Each occurrence is priced by the area a single
#  cell of that function would free, which is the maximum fanout-free cone
#  of the root inside the cut, and the classes are ranked by the total.
#
#  How to read it:
#
#    sites   occurrences where one cell of the function frees more than the
#            root gate it replaces.  Only there can a single-output cell win.
#    shared  occurrences whose cone spans several cells, but everything below
#            the root is also read elsewhere, so replacing the root alone frees
#            the root alone.
#    freed   area removed at `sites`, before paying for the new cell.  A cell
#            pays where it is narrower than `med freed`.
#    cone    all the logic between the leaves and the root.
#    lib     cells in the dictionary already in the class: PDK cells the
#            mapper passed over (usually for polarity or delay), or extension
#            cells already added.  --new-only hides them.
#
#  A `shared` column much larger than `sites` means the function lives inside
#  something bigger.  The second table prices that: two classes on the same
#  leaves, replaced by two cells at once.  A full adder is XOR3 + MAJ3, whose
#  sum and carry share the half-sum XOR; neither frees much alone, and
#  together they free the whole adder.  --explain N shows, for the top rows,
#  the exact variants, the cone shapes, who else reads the cone and which
#  classes sit on the same leaves.
#
#  Rows are not additive: one gate has cuts in several classes.
#
#  Trying a candidate afterwards is implementation/pdk_extension/README.md:
#  a provisional Liberty, then `make synth MERGE_LIB_ARGS=--provisional`.
# ================================================================

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "aion_flow" / "tools" / "aion_opt"))

from gds_to_image import ROW_HEIGHT_UM, SITE_WIDTH_UM          # noqa: E402
from aion_opt.io.cell_lib import CellLib, collapse_cell_name   # noqa: E402
from aion_opt.io.verilog_to_json import is_verilog, verilog_to_json  # noqa: E402
from aion_opt.io.yosys_json import load_yosys_json             # noqa: E402

SITE_AREA = SITE_WIDTH_UM * ROW_HEIGHT_UM

DEFAULT_NETLIST = PROJECT_ROOT / "flow" / "1_synth" / "nl" / "tt_um_aion.nl.v"
DEFAULT_CELL_LIB = PROJECT_ROOT / "aion_flow" / "tech" / "tech_dict" / "sg13g2_stdcell.json"
#: Written by `make pdk_ext_lib`; a superset of the PDK dictionary, and the
#: only one that knows the cells a netlist mapped with PDK_EXT=1 instantiates.
EXT_CELL_LIB = PROJECT_ROOT / "flow" / "pdk_extension" / "tech_dict" / "sg13g2_stdcell_aion.json"
DEFAULT_WORK = PROJECT_ROOT / "flow" / "explore" / "work"

#: NPN canonicalization tries every input permutation and polarity, 2 * n! * 2^n
#: transforms: 768 at four inputs, 7680 at five.  Four keeps a full run to
#: seconds, and a PDK-extension cell wider than four inputs is not a cell
#: anyone draws by hand.
MAX_K = 4

#: NPN classes worth a name that no library cell gives them.
_NAMED = {
    "XOR2": (2, lambda v: v[0] ^ v[1]),
    "XOR3": (3, lambda v: v[0] ^ v[1] ^ v[2]),
    "XOR4": (4, lambda v: v[0] ^ v[1] ^ v[2] ^ v[3]),
    "MAJ3": (3, lambda v: (v[0] & v[1]) | (v[0] & v[2]) | (v[1] & v[2])),
}


# ---------------------------------------------------------------------------
# Truth tables: an n-input function is an int of 2^n bits, minterm j at bit j
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _vars(n: int) -> tuple[int, ...]:
    return tuple(sum(1 << j for j in range(1 << n) if (j >> i) & 1) for i in range(n))


def _mask(n: int) -> int:
    return (1 << (1 << n)) - 1


def depends_on(table: int, n: int, var: int) -> bool:
    """True when the function's two cofactors on ``var`` differ."""
    low = _mask(n) & ~_vars(n)[var]
    return ((table >> (1 << var)) ^ table) & low != 0


@lru_cache(maxsize=None)
def _transforms(n: int, negations: bool) -> tuple[tuple[int, ...], ...]:
    """For every input permutation (and polarity): new minterm -> old minterm."""
    maps = []
    for perm in itertools.permutations(range(n)):
        for neg in range(1 << n) if negations else (0,):
            idx = []
            for j in range(1 << n):
                i = 0
                for t in range(n):
                    i |= (((j >> t) ^ (neg >> t)) & 1) << perm[t]
                idx.append(i)
            maps.append(tuple(idx))
    return tuple(maps)


def _apply(table: int, idx: Sequence[int]) -> int:
    out = 0
    for j, i in enumerate(idx):
        if (table >> i) & 1:
            out |= 1 << j
    return out


@lru_cache(maxsize=None)
def npn_class(table: int, n: int) -> int:
    """Smallest table reachable by permuting and negating inputs and output."""
    full = _mask(n)
    best = full
    for idx in _transforms(n, True):
        t = _apply(table, idx)
        best = min(best, t, t ^ full)
    return best


@lru_cache(maxsize=None)
def p_class(table: int, n: int) -> int:
    """Smallest table reachable by permuting inputs only: one exact cell."""
    return min(_apply(table, idx) for idx in _transforms(n, False))


def sop(table: int, n: int, names: str = "abcd") -> str:
    """A short two-level expression, for printing a class nobody has named."""
    full = _mask(n)
    if table in (0, full):
        return str(int(table == full))

    def cover(onset: int) -> list[tuple[int, ...]]:
        cubes = []
        for cube in itertools.product((0, 1, 2), repeat=n):
            bits = sum(1 << j for j in range(1 << n)
                       if all(c == 2 or ((j >> i) & 1) == c for i, c in enumerate(cube)))
            if bits & ~onset & full == 0:
                cubes.append((cube, bits))
        primes = [c for c in cubes
                  if not any(o[1] != c[1] and o[1] & c[1] == c[1] for o in cubes)]
        left, chosen = onset, []
        while left:
            cube, bits = max(primes, key=lambda cb: (bin(cb[1] & left).count("1"),
                                                     -sum(x != 2 for x in cb[0])))
            chosen.append(cube)
            left &= ~bits
        return chosen

    def render(cubes: list[tuple[int, ...]]) -> str:
        return " + ".join("".join(("" if c else "!") + names[i]
                                  for i, c in enumerate(cube) if c != 2) or "1"
                          for cube in cubes)

    pos, neg = cover(table), cover(full & ~table)
    literals = lambda cubes: sum(x != 2 for cube in cubes for x in cube)
    return render(pos) if literals(pos) <= literals(neg) else f"!({render(neg)})"


# ---------------------------------------------------------------------------
# Liberty function strings -> bit-parallel Python callables
# ---------------------------------------------------------------------------
_TOKEN = re.compile(r"\s*(?:([A-Za-z_][A-Za-z0-9_]*)|([01])|([!'^*&+|()]))")


def compile_function(expr: str, pins: Sequence[str]) -> Callable | None:
    """Compile a Liberty ``function`` over ``pins``, or None if it is not one.

    Liberty precedence, highest first: ``!`` and postfix ``'``, then ``^``,
    then ``*``/``&``/juxtaposition, then ``+``/``|``.  Python's own
    precedence differs (``&`` binds tighter than ``^``), so the expression is
    re-emitted fully parenthesized rather than transliterated.  A function
    naming anything that is not an input pin -- ``IQ`` on a flip-flop,
    ``int_GATE`` on a clock gate, an enable it ignores -- is refused, and the
    caller treats that cell as a black box.
    """
    tokens, pos = [], 0
    while pos < len(expr):
        match = _TOKEN.match(expr, pos)
        if match is None or match.end() == pos:
            if expr[pos:].strip():
                return None
            break
        tokens.append(match.group(1) or match.group(2) or match.group(3))
        pos = match.end()
    index = {pin: i for i, pin in enumerate(pins)}
    used: set[str] = set()
    at = 0

    def peek():
        return tokens[at] if at < len(tokens) else None

    def take():
        nonlocal at
        at += 1
        return tokens[at - 1]

    def primary():
        tok = take() if peek() is not None else None
        if tok == "(":
            inner = orexpr()
            if take() != ")":
                raise ValueError
            out = inner
        elif tok in ("0", "1"):
            out = "M" if tok == "1" else "0"
        elif tok is not None and (tok[0].isalpha() or tok[0] == "_"):
            if tok not in index:
                raise KeyError(tok)
            used.add(tok)
            out = f"v[{index[tok]}]"
        else:
            raise ValueError
        while peek() == "'":
            take()
            out = f"(M^{out})"
        return out

    def unary():
        if peek() == "!":
            take()
            return f"(M^{unary()})"
        return primary()

    def xorexpr():
        out = unary()
        while peek() == "^":
            take()
            out = f"({out}^{unary()})"
        return out

    def andexpr():
        out = xorexpr()
        while True:
            if peek() in ("*", "&"):
                take()
            elif not (peek() is not None and (peek() in ("!", "(", "0", "1")
                                              or peek()[0].isalpha() or peek()[0] == "_")):
                return out
            out = f"({out}&{xorexpr()})"

    def orexpr():
        out = andexpr()
        while peek() in ("+", "|"):
            take()
            out = f"({out}|{andexpr()})"
        return out

    try:
        body = orexpr()
        if at != len(tokens):
            return None
    except (KeyError, ValueError, IndexError):
        return None
    if used != set(pins):
        return None
    return eval(f"lambda v, M: {body}")                  # noqa: S307 -- our own grammar


# ---------------------------------------------------------------------------
# The netlist as a gate DAG
# ---------------------------------------------------------------------------
@dataclass
class Gate:
    name: str
    cell: str
    inputs: tuple        # bits, in the compiled function's argument order
    output: int
    fn: Callable
    area: float


@dataclass
class Graph:
    gates: dict[str, Gate]
    order: list[str]                       # topological
    driver: dict[int, str]                 # bit -> gate driving it
    loads: dict[int, set]                  # bit -> gates reading it
    external: set                          # bits read by a non-gate: flop, black box, port
    constants: dict                        # bit -> 0 | 1
    black_boxes: Counter

    @property
    def rank(self) -> dict[str, int]:
        return {name: i for i, name in enumerate(self.order)}


def build_graph(circuit, cell_lib: CellLib) -> Graph:
    """Every combinational cell with a usable function becomes a gate.

    Everything else -- flip-flops, clock gates, tristates, cells the
    dictionary does not describe -- is a black box: its outputs are leaves no
    cut can see through, and its inputs count as loads outside any cone.
    """
    gates: dict[str, Gate] = {}
    constants: dict = {"0": 0, "1": 1}
    external: set = set()
    black_boxes: Counter = Counter()

    for inst in circuit.instances.values():
        pins = cell_lib.pins(inst.cell_type)
        outs = [p for p, d in pins.items() if d == "output"]
        ins = sorted(p for p, d in pins.items() if d == "input")
        text = cell_lib.function(inst.cell_type)
        fn = None
        if cell_lib.is_combinational(inst.cell_type) and len(outs) == 1 and text:
            fn = compile_function(text, ins)
        out_bits = inst.connections.get(outs[0], []) if len(outs) == 1 else []
        in_bits = [inst.connections.get(p, []) for p in ins]
        if fn is not None and len(out_bits) == 1 and all(len(b) == 1 for b in in_bits):
            if not ins:                       # tie cell
                constants[out_bits[0]] = fn((), 1) & 1
                continue
            gates[inst.name] = Gate(inst.name, inst.cell_type,
                                    tuple(b[0] for b in in_bits), out_bits[0], fn,
                                    cell_lib.area(inst.cell_type))
            continue
        black_boxes[collapse_cell_name(inst.cell_type)] += 1
        for pin, bits in inst.connections.items():
            if pins.get(pin) != "output":
                external.update(bits)

    for port in circuit.ports:
        if port.direction != "input":
            external.update(port.bits)

    driver = {g.output: g.name for g in gates.values()}
    loads: dict[int, set] = defaultdict(set)
    for g in gates.values():
        for bit in g.inputs:
            loads[bit].add(g.name)

    # Kahn: a gate is ready once every gate driving one of its inputs is placed.
    pending = {n: len({driver[b] for b in g.inputs if b in driver}) for n, g in gates.items()}
    ready = sorted(n for n, k in pending.items() if k == 0)
    order: list[str] = []
    while ready:
        name = ready.pop()
        order.append(name)
        for reader in sorted(loads.get(gates[name].output, ())):
            pending[reader] -= 1
            if pending[reader] == 0:
                ready.append(reader)
    if len(order) != len(gates):
        raise SystemExit(f"error: combinational loop through "
                         f"{len(gates) - len(order)} gate(s)")
    return Graph(gates, order, driver, loads, external, constants, black_boxes)


# ---------------------------------------------------------------------------
# Cuts
# ---------------------------------------------------------------------------
def _bit_key(bit) -> tuple:
    """Yosys bits are ints, except the constants and x/z, which are strings."""
    return (isinstance(bit, str), bit if isinstance(bit, int) else 0, str(bit))


def _prune(cuts: set, limit: int) -> list[frozenset]:
    """Drop dominated cuts (a superset of another), keep the ``limit`` smallest."""
    ordered = sorted(cuts, key=lambda c: (len(c), sorted(map(_bit_key, c))))
    kept: list[frozenset] = []
    for cut in ordered:
        if not any(other < cut for other in kept):
            kept.append(cut)
            if len(kept) >= limit:
                break
    return kept


def enumerate_cuts(graph: Graph, k: int, limit: int) -> dict[str, list[frozenset]]:
    """The non-trivial k-feasible cuts of every gate, leaves as bits."""
    cuts: dict[str, list[frozenset]] = {}
    for name in graph.order:
        partial: set = {frozenset()}
        for bit in set(graph.gates[name].inputs):
            if bit in graph.constants:
                continue
            options = [frozenset((bit,))] + cuts.get(graph.driver.get(bit), [])
            partial = set(_prune({a | b for a in partial for b in options
                                  if len(a | b) <= k}, limit * 4))
        cuts[name] = _prune(partial - {frozenset()}, limit)
    return cuts


@dataclass
class Site:
    """One gate computing one function over one cut."""

    root: str
    leaves: tuple
    table: int
    cone: frozenset
    freed: frozenset     # the root's maximum fanout-free cone inside the cut

    @property
    def n(self) -> int:
        return len(self.leaves)


def evaluate(graph: Graph, root: str, leaves: tuple, rank: dict) -> "tuple[int, frozenset] | None":
    """Truth table of ``root`` over ``leaves`` and the cone that computes it."""
    leaf_set = set(leaves)
    cone, stack = set(), [root]
    while stack:
        name = stack.pop()
        if name in cone:
            continue
        cone.add(name)
        for bit in graph.gates[name].inputs:
            if bit in leaf_set or bit in graph.constants:
                continue
            if bit not in graph.driver:
                return None                      # not actually a cut
            stack.append(graph.driver[bit])
    n = len(leaves)
    full = _mask(n)
    value = {bit: var for bit, var in zip(leaves, _vars(n))}
    for bit, const in graph.constants.items():
        value[bit] = full if const else 0
    for name in sorted(cone, key=rank.__getitem__):
        gate = graph.gates[name]
        value[gate.output] = gate.fn([value[b] for b in gate.inputs], full) & full
    return value[graph.gates[root].output], frozenset(cone)


def fanout_free(graph: Graph, roots, cone: frozenset, rank: dict) -> frozenset:
    """The part of ``cone`` that removing ``roots`` removes."""
    roots = {roots} if isinstance(roots, str) else set(roots)
    freed = set(roots)
    for name in sorted(cone - roots, key=rank.__getitem__, reverse=True):
        out = graph.gates[name].output
        if out not in graph.external and graph.loads.get(out, set()) <= freed:
            freed.add(name)
    return frozenset(freed)


def find_sites(graph: Graph, k: int, limit: int, min_cells: int) -> list[Site]:
    rank = graph.rank
    sites: list[Site] = []
    for root, cuts in enumerate_cuts(graph, k, limit).items():
        for cut in cuts:
            leaves = tuple(sorted(cut, key=_bit_key))
            if len(leaves) < 2:
                continue
            result = evaluate(graph, root, leaves, rank)
            if result is None:
                continue
            table, cone = result
            if len(cone) < min_cells:
                continue
            if not all(depends_on(table, len(leaves), i) for i in range(len(leaves))):
                continue                         # a smaller cut says the same
            sites.append(Site(root, leaves, table, cone,
                              fanout_free(graph, root, cone, rank)))
    return sites


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------
@dataclass(eq=False)
class FunctionClass:
    """One NPN class and every site computing it.

    ``sites`` are those where one cell of the function frees more than the
    root it replaces -- the only ones a single-output cell can win on.
    ``shared`` are the rest: the cone spans several cells, but everything
    below the root is also read outside it, so only a multi-output
    replacement (see :func:`pair_stats`) frees it.
    """

    n: int
    npn: int
    sites: list
    shared: list
    lib_cells: tuple
    name: str
    freed_area: float = 0.0
    cone_area: float = 0.0

    @property
    def count(self) -> int:
        return len(self.sites)

    @property
    def everywhere(self) -> list:
        return self.sites + self.shared


@dataclass(eq=False)
class PairClass:
    """Two classes computed on the same leaves, replaced together."""

    first: FunctionClass
    second: FunctionClass
    joint_freed: list        # per site, um2
    cone: list               # per site, um2

    @property
    def count(self) -> int:
        return len(self.joint_freed)


def library_classes(cell_lib: CellLib, k: int) -> dict[tuple[int, int], list[str]]:
    """``(inputs, NPN class) -> library cells`` for every combinational cell."""
    found: dict[tuple[int, int], set] = defaultdict(set)
    for name, info in cell_lib.raw.items():
        ins = sorted(p for p, d in info.get("pins", {}).items() if d == "input")
        if not (2 <= len(ins) <= k) or not cell_lib.is_combinational(name):
            continue
        fn = compile_function(info.get("function") or "", ins)
        if fn is None:
            continue
        n = len(ins)
        table = fn(_vars(n), _mask(n)) & _mask(n)
        found[(n, npn_class(table, n))].add(collapse_cell_name(name).replace("sg13g2_", ""))
    return {key: sorted(cells) for key, cells in found.items()}


def classify(graph: Graph, sites: list[Site], lib: dict,
             min_cells: int) -> list[FunctionClass]:
    named = {}
    for label, (n, fn) in _NAMED.items():
        named[(n, npn_class(fn(_vars(n)) & _mask(n), n))] = label

    # One site per root and one per leaf set, within a class.  A gate can
    # compute the same class over two cuts (c, or the inverter driving c), and
    # a gate and the inverter on top of it compute it over the same nets.
    # Either way it is one adder, not two.  The site that frees more is the
    # one a cell would replace.
    def freed_area(site: Site) -> float:
        return sum(graph.gates[g].area for g in site.freed)

    def keep_best(candidates, key):
        best: dict = {}
        for site in candidates:
            k = key(site)
            if k not in best or freed_area(site) > freed_area(best[k]):
                best[k] = site
        return best.values()

    npn_of = {id(s): (s.n, npn_class(s.table, s.n)) for s in sites}
    per_root = keep_best(sites, lambda s: (npn_of[id(s)], s.root))
    per_leaves = keep_best(per_root, lambda s: (npn_of[id(s)], s.leaves))

    grouped: dict[tuple, list] = defaultdict(list)
    for site in per_leaves:
        grouped[npn_of[id(site)]].append(site)

    classes = []
    for (n, npn), members in grouped.items():
        cells = tuple(lib.get((n, npn), ()))
        exact = Counter(p_class(s.table, n) for s in members).most_common(1)[0][0]
        name = "/".join(cells[:3]) + ("/..." if len(cells) > 3 else "") if cells \
            else named.get((n, npn), sop(exact, n))
        merging = [s for s in members if len(s.freed) >= min_cells]
        shared = [s for s in members if len(s.freed) < min_cells]
        fc = FunctionClass(n, npn, merging, shared, cells, name)
        fc.freed_area = sum(graph.gates[g].area for s in merging for g in s.freed)
        fc.cone_area = sum(graph.gates[g].area for s in members for g in s.cone)
        classes.append(fc)
    return classes


def pair_stats(graph: Graph, classes: list[FunctionClass]) -> list[PairClass]:
    """Classes that sit on the same leaves, priced as one joint replacement.

    A full adder is the case this exists for.  Its sum and carry share the
    half-sum XOR, so neither XOR3 nor MAJ3 frees much alone.  Replace both
    and the whole adder goes.  A pair counts where the two roots together free
    at least one cell besides themselves, and once per pair of roots however
    many leaf sets they share.
    """
    rank = graph.rank
    by_leaves: dict[tuple, list] = defaultdict(list)
    for fc in classes:
        for site in fc.everywhere:
            by_leaves[site.leaves].append((fc, site))

    pairs: dict[tuple, PairClass] = {}
    seen: set = set()
    for hosted in by_leaves.values():
        for (fa, sa), (fb, sb) in itertools.combinations(hosted, 2):
            roots = frozenset((sa.root, sb.root))
            if fa is fb or len(roots) < 2:
                continue
            if (fb.name, fb.npn) < (fa.name, fa.npn):
                fa, fb = fb, fa
            if (id(fa), id(fb), roots) in seen:
                continue
            seen.add((id(fa), id(fb), roots))
            cone = sa.cone | sb.cone
            joint = fanout_free(graph, roots, cone, rank)
            if len(joint) <= 2:
                continue
            pair = pairs.setdefault((id(fa), id(fb)), PairClass(fa, fb, [], []))
            pair.joint_freed.append(sum(graph.gates[g].area for g in joint))
            pair.cone.append(sum(graph.gates[g].area for g in cone))
    return sorted(pairs.values(), key=lambda p: -sum(p.joint_freed))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _cells(graph: Graph, names) -> str:
    return "+".join(sorted(collapse_cell_name(graph.gates[g].cell).replace("sg13g2_", "")
                           for g in names))


def _sites(graph: Graph, names) -> float:
    return sum(graph.gates[g].area for g in names) / SITE_AREA


def _median(values) -> str:
    values = list(values)
    return f"{median(values):.1f}" if values else "-"


def print_classes(classes: list[FunctionClass], graph: Graph, total_area: float,
                  rows: int) -> None:
    head = (f"{'#':>3}  {'function':<28}{'in':>3}{'sites':>7}{'shared':>7}"
            f"{'freed um2':>11}{'%comb':>7}{'med freed':>10}{'med cone':>9}  lib")
    print(head)
    print("-" * len(head))
    for i, fc in enumerate(classes[:rows], 1):
        print(f"{i:>3}  {fc.name[:27]:<28}{fc.n:>3}{fc.count:>7}{len(fc.shared):>7}"
              f"{fc.freed_area:>11,.0f}{100 * fc.freed_area / total_area:>6.1f}%"
              f"{_median(_sites(graph, s.freed) for s in fc.sites):>10}"
              f"{_median(_sites(graph, s.cone) for s in fc.sites):>9}  "
              f"{'yes' if fc.lib_cells else '-'}")


def print_pairs(pairs: list[PairClass], total_area: float, rows: int) -> None:
    head = (f"{'#':>3}  {'functions on the same leaves':<42}{'sites':>7}"
            f"{'freed um2':>11}{'%comb':>7}{'med freed':>10}{'med cone':>9}")
    print(head)
    print("-" * len(head))
    for i, pair in enumerate(pairs[:rows], 1):
        label = f"{pair.first.name} + {pair.second.name}"
        freed = sum(pair.joint_freed)
        print(f"{i:>3}  {label[:41]:<42}{pair.count:>7}{freed:>11,.0f}"
              f"{100 * freed / total_area:>6.1f}%"
              f"{_median(a / SITE_AREA for a in pair.joint_freed):>10}"
              f"{_median(a / SITE_AREA for a in pair.cone):>9}")


def explain(fc: FunctionClass, graph: Graph, by_leaves: dict, rank_of: dict,
            top: int = 5) -> None:
    every = fc.everywhere
    print(f"\n{fc.name}  ({fc.n} inputs: {fc.count} merging site(s), "
          f"{len(fc.shared)} shared)")
    if fc.lib_cells:
        print(f"  library cells in this class: {', '.join(fc.lib_cells)}")
    variants = Counter(p_class(s.table, fc.n) for s in every)
    print("  exact functions (inputs renamed a, b, c, d):")
    for table, count in variants.most_common(3):
        print(f"    {count:6}  {sop(table, fc.n)}")
    print("  cone shapes (freed / whole cone):")
    shapes = Counter((_cells(graph, s.freed), _cells(graph, s.cone)) for s in every)
    for (freed, cone), count in shapes.most_common(top):
        print(f"    {count:6}  {freed}" + ("" if freed == cone else f"   /   {cone}"))
    escapes: Counter = Counter()
    for s in every:
        readers: set = set()
        for name in s.cone - s.freed:
            out = graph.gates[name].output
            readers |= {collapse_cell_name(graph.gates[r].cell).replace("sg13g2_", "")
                        for r in graph.loads.get(out, ()) if r not in s.cone}
            if out in graph.external:
                readers.add("<flop/port/black box>")
        escapes.update(readers)
    if escapes:
        print("  a net inside the cone is also read by (sites):")
        for cell, count in escapes.most_common(top):
            print(f"    {count:6}  {cell}")
    shared: Counter = Counter()
    for s in every:
        shared.update(other for other in by_leaves[s.leaves] if other is not fc)
    if shared:
        print("  the same leaves also compute (sites):")
        for other, count in shared.most_common(3):
            print(f"    {count:6}  {other.name}   (row {rank_of.get(id(other), '-')})")


def to_json(classes: list[FunctionClass], pairs: list[PairClass], graph: Graph,
            args) -> dict:
    def expression(fc: FunctionClass) -> str:
        return sop(Counter(p_class(s.table, fc.n)
                           for s in fc.everywhere).most_common(1)[0][0], fc.n)

    return {
        "netlist": str(args.netlist),
        "k": args.k,
        "site_area_um2": SITE_AREA,
        "classes": [
            {
                "name": fc.name,
                "inputs": fc.n,
                "npn_table": f"0x{fc.npn:0{max(1, (1 << fc.n) // 4)}x}",
                "expression": expression(fc),
                "library_cells": list(fc.lib_cells),
                "sites": fc.count,
                "shared_sites": len(fc.shared),
                "freed_area_um2": round(fc.freed_area, 4),
                "cone_area_um2": round(fc.cone_area, 4),
                "freed_sites_median": (median(_sites(graph, s.freed) for s in fc.sites)
                                       if fc.sites else None),
            }
            for fc in classes
        ],
        "pairs": [
            {
                "functions": [pair.first.name, pair.second.name],
                "expressions": [expression(pair.first), expression(pair.second)],
                "sites": pair.count,
                "joint_freed_area_um2": round(sum(pair.joint_freed), 4),
                "joint_freed_sites_median": median(a / SITE_AREA for a in pair.joint_freed),
                "cone_sites_median": median(a / SITE_AREA for a in pair.cone),
            }
            for pair in pairs
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load(args) -> "tuple[Graph, CellLib]":
    cell_lib_path = args.cell_lib
    if cell_lib_path is None:
        cell_lib_path = EXT_CELL_LIB if EXT_CELL_LIB.exists() else DEFAULT_CELL_LIB
    cell_lib = CellLib(cell_lib_path)
    netlist = args.netlist
    if is_verilog(netlist):
        cached = args.work_dir / f"{netlist.stem}.json"
        if not (cached.exists() and cached.stat().st_mtime >= netlist.stat().st_mtime):
            print(f"[rank] yosys: {netlist} -> {cached}")
            verilog_to_json(netlist, top_module=args.top, output_json=cached)
        netlist = cached
    circuit = load_yosys_json(netlist, cell_lib=cell_lib, top_module=args.top)
    graph = build_graph(circuit, cell_lib)
    print(f"[rank] {circuit.name}: {len(graph.gates)} gate(s), "
          f"{sum(graph.black_boxes.values())} black box(es), "
          f"dictionary {_rel(cell_lib_path)}")
    return graph, cell_lib


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Rank the Boolean functions a netlist builds out of several "
                    "cells, by the area one cell of each would free.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  scripts/rank_functions.py\n"
               "  scripts/rank_functions.py --new-only --explain 3\n"
               "  scripts/rank_functions.py -k 4 --rows 40 --json flow/explore/functions.json\n")
    p.add_argument("--netlist", type=Path, default=DEFAULT_NETLIST)
    p.add_argument("--cell-lib", type=Path, default=None,
                   help="technology dictionary (default: the PDK-extended one "
                        "if `make pdk_ext_lib` wrote it, else the PDK's)")
    p.add_argument("--top", default="tt_um_aion")
    p.add_argument("--work-dir", type=Path, default=DEFAULT_WORK,
                   help="where the yosys JSON of the netlist is cached")
    p.add_argument("-k", type=int, default=3, choices=range(2, MAX_K + 1),
                   help="largest function to look for, in inputs (default 3)")
    p.add_argument("--max-cuts", type=int, default=20,
                   help="cuts kept per gate, smallest first (default 20)")
    p.add_argument("--min-cells", type=int, default=2,
                   help="a site must span, and a merging site free, at least this "
                        "many cells (default 2: one cell computing its own "
                        "function is no candidate)")
    p.add_argument("--min-sites", type=int, default=10,
                   help="hide classes and pairs with fewer sites (default 10)")
    p.add_argument("--sort", choices=("freed", "cone", "sites"), default="freed")
    p.add_argument("--new-only", action="store_true",
                   help="hide classes a library cell already implements, and "
                        "pairs where both do")
    p.add_argument("--rows", type=int, default=25)
    p.add_argument("--pairs", type=int, default=8, metavar="N",
                   help="rows of the same-leaves pair table (default 8, 0 hides it)")
    p.add_argument("--explain", type=int, default=0, metavar="N",
                   help="detail the top N rows: exact variants, cone shapes, "
                        "who else reads the cone, classes on the same leaves")
    p.add_argument("--json", type=Path, default=None)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    t0 = time.time()
    graph, cell_lib = load(args)
    total_area = sum(g.area for g in graph.gates.values())

    sites = find_sites(graph, args.k, args.max_cuts, args.min_cells)
    classes = classify(graph, sites, library_classes(cell_lib, args.k), args.min_cells)
    pairs = pair_stats(graph, classes)
    by_leaves: dict[tuple, list] = defaultdict(list)
    for fc in classes:
        for s in fc.everywhere:
            by_leaves[s.leaves].append(fc)

    key = {"freed": lambda c: (-c.freed_area, -c.cone_area),
           "cone": lambda c: -c.cone_area,
           "sites": lambda c: -len(c.everywhere)}[args.sort]
    shown = sorted((c for c in classes
                    if len(c.everywhere) >= args.min_sites
                    and not (args.new_only and c.lib_cells)),
                   key=key)
    shown_pairs = [p for p in pairs
                   if p.count >= args.min_sites
                   and not (args.new_only and p.first.lib_cells and p.second.lib_cells)]
    rank_of = {id(c): i for i, c in enumerate(shown, 1)}

    print(f"[rank] {len(sites):,} multi-cell site(s) over cuts of up to {args.k} "
          f"input(s), {len(classes)} NPN class(es), {time.time() - t0:.1f}s\n")
    print(f"One cell per function.  sites: the cell frees more than the root it "
          f"replaces; shared: everything\nbelow the root is read elsewhere too, so "
          f"only a pair of cells frees it (second table).\nfreed: area removed, "
          f"before paying for the new cell; %comb of {total_area:,.0f} um2 of "
          f"gates.\nmed: median per site, in CoreSites of {SITE_AREA:.4f} um2.  "
          f"A cell pays where it is narrower than med freed.\n")
    print_classes(shown, graph, total_area, args.rows)
    if shown_pairs and args.pairs:
        print(f"\nTwo cells on the same leaves, replacing both roots at once "
              f"(a full adder is XOR3 + MAJ3):\n")
        print_pairs(shown_pairs, total_area, args.pairs)
    for fc in shown[:args.explain]:
        explain(fc, graph, by_leaves, rank_of)
    print("\nRows are not additive: one gate has cuts in several classes.")

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(to_json(shown, shown_pairs, graph, args), indent=2))
        print(f"[rank] wrote {_rel(args.json)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
