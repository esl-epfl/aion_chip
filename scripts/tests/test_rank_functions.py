# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-15
#  Description:               rank_functions.py: Liberty functions, NPN
#                             classes, and a full adder found as XOR3 + MAJ3
# ================================================================

"""The function ranking finds what the structural miner cannot.

A full adder built from PDK cells shares its half-sum XOR between sum and
carry.  Replacing the sum alone frees one cell, replacing the carry frees the
carry logic, and replacing both frees the whole adder -- which is the whole
reason the pair table exists.
"""

import pytest

import rank_functions as rf
from aion_opt.graph.circuit import Circuit, Instance, TopPort
from aion_opt.io.cell_lib import CellLib

V3, M3 = rf._vars(3), rf._mask(3)
A, B, C = V3


def _table(expr: str, pins=("A", "B", "C")) -> int:
    fn = rf.compile_function(expr, list(pins))
    assert fn is not None, expr
    n = len(pins)
    return fn(rf._vars(n), rf._mask(n)) & rf._mask(n)


# ---------------------------------------------------------------------------
@pytest.mark.parametrize("expr, expected", [
    # Liberty binds ^ tighter than *; yosys' read_liberty parses these the same.
    ("A^B*C", (A ^ B) & C),
    ("A*B^C", A & (B ^ C)),
    ("A B+C'", (A & B) | (M3 ^ C)),
    ("!((A*B)+C)", M3 ^ ((A & B) | C)),
])
def test_function_precedence(expr, expected):
    assert _table(expr) == expected


def test_function_must_be_over_the_input_pins():
    # A flip-flop's IQ, and a tristate's ignored enable, make black boxes.
    assert rf.compile_function("IQ", ["D"]) is None
    assert rf.compile_function("A", ["A", "TE_B"]) is None
    tie = rf.compile_function("1", [])
    assert tie is not None and tie((), 1) == 1


def test_npn_classes():
    maj = (A & B) | (A & C) | (B & C)
    inverted_inputs = ((M3 ^ A) & (M3 ^ B)) | ((M3 ^ A) & (M3 ^ C)) | ((M3 ^ B) & (M3 ^ C))
    assert rf.npn_class(maj, 3) == rf.npn_class(M3 ^ maj, 3) == rf.npn_class(inverted_inputs, 3)
    assert rf.npn_class(A ^ B ^ C, 3) != rf.npn_class(maj, 3)
    # One exact cell up to input order, but not up to polarity.
    assert rf.p_class(_table("(!S*A0)+(S*A1)", ("A0", "A1", "S")), 3) == \
        rf.p_class(_table("(!A*B)+(A*C)"), 3)
    assert rf.p_class(maj, 3) != rf.p_class(M3 ^ maj, 3)


# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def full_adder():
    """x = a^b; s = x^c; co = !(!(x c) !(a b)) = maj(a, b, c)."""
    a, b, c, x, s, p, q, co = range(2, 10)
    cells = {
        "g1": ("sg13g2_xor2_1", {"A": [a], "B": [b], "X": [x]}),
        "g2": ("sg13g2_xor2_1", {"A": [x], "B": [c], "X": [s]}),
        "g3": ("sg13g2_nand2_1", {"A": [x], "B": [c], "Y": [p]}),
        "g4": ("sg13g2_nand2_1", {"A": [a], "B": [b], "Y": [q]}),
        "g5": ("sg13g2_nand2_1", {"A": [p], "B": [q], "Y": [co]}),
    }
    circuit = Circuit(
        name="fa",
        ports=[TopPort("a", "input", [a]), TopPort("b", "input", [b]),
               TopPort("c", "input", [c]), TopPort("s", "output", [s]),
               TopPort("co", "output", [co])],
        instances={n: Instance(n, t, connections=conn) for n, (t, conn) in cells.items()},
    )
    cell_lib = CellLib(rf.DEFAULT_CELL_LIB)
    graph = rf.build_graph(circuit, cell_lib)
    sites = rf.find_sites(graph, k=3, limit=20, min_cells=2)
    classes = rf.classify(graph, sites, rf.library_classes(cell_lib, 3), min_cells=2)
    return graph, {fc.name: fc for fc in classes}, rf.pair_stats(graph, classes)


def test_carry_is_a_merging_majority(full_adder):
    graph, classes, _ = full_adder
    maj = classes["MAJ3"]
    assert [s.root for s in maj.sites] == ["g5"]
    # The half-sum XOR is read by the sum, so the carry cell leaves it behind.
    assert maj.sites[0].freed == {"g5", "g3", "g4"}
    assert maj.sites[0].cone == {"g5", "g3", "g4", "g1"}


def test_sum_alone_frees_only_itself(full_adder):
    _, classes, _ = full_adder
    xor3 = classes["XOR3"]
    assert xor3.sites == []
    assert [(s.root, s.freed) for s in xor3.shared] == [("g2", {"g2"})]


def test_sum_and_carry_together_free_the_adder(full_adder):
    graph, _, pairs = full_adder
    [pair] = [p for p in pairs if {p.first.name, p.second.name} == {"MAJ3", "XOR3"}]
    assert pair.count == 1
    assert pair.joint_freed[0] == pytest.approx(sum(g.area for g in graph.gates.values()))
