# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-01
#  Description:               AION SoC - TinyTapeout Register Interface Cocotb TB
# ================================================================

import logging
import random

import cocotb
import posit
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

logging.getLogger("cocotb").setLevel(logging.DEBUG)

OPCODE_ADD = 0b0000
OPCODE_MULT = 0b0001
OPCODE_EQ = 0b0010
OPCODE_LT = 0b0011
OPCODE_AND = 0b0100
OPCODE_OR = 0b0101
OPCODE_XOR = 0b0110

# MAC array. reg_control(6:4) selects the lane, so a MAC command is
# (lane << 4) | opcode, plus bit 7 to fire it.
OPCODE_MAC_LOAD = 0b1000
OPCODE_MAC_RUN = 0b1001
OPCODE_MAC_CLEAR = 0b1010
OPCODE_MAC_READ = 0b1011

# reg_control(6:4) is three bits, so at most eight lanes are addressable. How
# many actually exist is a generic, and the test discovers it rather than being
# told: the read multiplexer returns zero for a lane that was never built.
MAC_MAX_LANES = 8

POSIT_NBITS = 16
POSIT_ES = 2

# Fixed integer test patterns
OP_A_INT_FIXED = [1, 2, 100]
OP_B_INT_FIXED = [3, 6, 20]

# Fixed float test patterns
OP_A_FLOAT_FIXED = [0.0, 0.1, 0.5, 3.1]
OP_B_FLOAT_FIXED = [2.5, 3.2, 12.2, 6.0]

# Posit<16,2> bit patterns worth naming, crossed with each other below.
#
# The last four are the ones the previous hand-written reference model got
# wrong: their exponent field runs off the end of the word, so the missing
# bits have to be read as zeros, and the old model read them as absent
# instead -- every one of them decoded to half its true value. A reference
# that is wrong at the ends of the range cannot fail there, so the hardware
# was never actually checked against them.
POSIT_EDGE_CASES = (
    (0x0000, "zero"),
    (0x4000, "one"),
    (0xC000, "-one"),
    (0x0001, "minpos"),
    (0xFFFF, "-minpos"),
    (0x7FFF, "maxpos"),
    (0x8001, "-maxpos"),
    (0x8000, "NaR"),
    (0x0003, "small, truncated exponent"),
    (0xFFFD, "-small, truncated exponent"),
    (0x7FFD, "large, truncated exponent"),
    (0x8003, "-large, truncated exponent"),
)

REG_OP_A_LO = 0
REG_OP_A_HI = 1
REG_OP_B_LO = 2
REG_OP_B_HI = 3
REG_CONTROL = 4
REG_RESULT_LO = 5
REG_RESULT_HI = 6
REG_STATUS = 7

CLK_PERIOD_NS = 50


async def reset(dut):
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


async def write_reg(dut, addr, data):
    dut.ui_in.value = 0x80 | (addr & 0x07)
    dut.uio_in.value = data & 0xFF
    await RisingEdge(dut.clk)
    dut.ui_in.value = addr & 0x07
    dut.uio_in.value = 0


async def read_reg(dut, addr):
    dut.ui_in.value = addr & 0x07
    await RisingEdge(dut.clk)
    return int(dut.uo_out.value)


async def compute_posit(dut, opA, opB, opcode):
    await write_reg(dut, REG_OP_A_LO, opA)
    await write_reg(dut, REG_OP_A_HI, opA >> 8)
    await write_reg(dut, REG_OP_B_LO, opB)
    await write_reg(dut, REG_OP_B_HI, opB >> 8)
    await write_reg(dut, REG_CONTROL, (opcode & 0x0F) | 0x80)

    while True:
        status = await read_reg(dut, REG_STATUS)
        if status & 0x01:
            break

    result_lo = await read_reg(dut, REG_RESULT_LO)
    result_hi = await read_reg(dut, REG_RESULT_HI)
    return (result_hi << 8) | result_lo


async def mac_command(dut, opcode, lane=0, opA=None, opB=None):
    """Issue one MAC command and return the selected lane's accumulator.

    Every MAC opcode leaves the accumulator on `result`, so the read-back is
    the same sequence for LOAD, RUN, CLEAR and READ.
    """
    if opA is not None:
        await write_reg(dut, REG_OP_A_LO, opA)
        await write_reg(dut, REG_OP_A_HI, opA >> 8)
    if opB is not None:
        await write_reg(dut, REG_OP_B_LO, opB)
        await write_reg(dut, REG_OP_B_HI, opB >> 8)

    await write_reg(dut, REG_CONTROL, ((lane & 0x07) << 4) | (opcode & 0x0F) | 0x80)
    while True:
        if (await read_reg(dut, REG_STATUS)) & 0x01:
            break

    result_lo = await read_reg(dut, REG_RESULT_LO)
    result_hi = await read_reg(dut, REG_RESULT_HI)
    return (result_hi << 8) | result_lo


def _to_posit(value: float) -> int:
    return posit.encode(value, POSIT_NBITS, POSIT_ES)


async def _run_arith_pairs(dut, opcode, pairs, op_name, quiet=False):
    """Drive (op_a, op_b) posit bit patterns and check against Universal.

    `quiet` logs only the summary: the exhaustive sweeps below run hundreds of
    transactions, and a line each buries the one that matters.
    """
    symbol = "+" if opcode == OPCODE_ADD else "*"
    for i, (op_a, op_b) in enumerate(pairs):
        result = await compute_posit(dut, op_a, op_b, opcode)
        if opcode == OPCODE_ADD:
            expected = posit.add(op_a, op_b, POSIT_NBITS, POSIT_ES)
        else:
            expected = posit.mul(op_a, op_b, POSIT_NBITS, POSIT_ES)

        if not quiet:
            dut._log.info(
                f"{op_name}[{i}]: 0x{op_a:04X} {symbol} 0x{op_b:04X} "
                f"result=0x{result:04X} expected=0x{expected:04X}"
            )
        if result != expected:
            raise AssertionError(
                f"{op_name}[{i}] mismatch: "
                f"0x{op_a:04X} ({posit.decode(op_a, POSIT_NBITS, POSIT_ES)!r}) "
                f"{symbol} 0x{op_b:04X} ({posit.decode(op_b, POSIT_NBITS, POSIT_ES)!r}): "
                f"got 0x{result:04X}, expected 0x{expected:04X}"
            )
    if quiet:
        dut._log.info(f"{op_name}: {len(pairs)} pair(s) match Universal")


async def _run_fixed_tests(dut, opcode, op_a_list, op_b_list, op_name):
    for i, (a, b) in enumerate(zip(op_a_list, op_b_list)):
        op_a = _to_posit(a)
        op_b = _to_posit(b)
        result = await compute_posit(dut, op_a, op_b, opcode)

        # The reference is Universal operating on the same encoded operands
        # the hardware sees -- not the original Python floats, and not double
        # arithmetic re-encoded afterwards.
        if opcode == OPCODE_ADD:
            expected = posit.add(op_a, op_b, POSIT_NBITS, POSIT_ES)
        else:
            expected = posit.mul(op_a, op_b, POSIT_NBITS, POSIT_ES)

        dut._log.info(
            f"{op_name}[{i}]: {a} {('*' if opcode == OPCODE_MULT else '+')} {b} "
            f"opA=0x{op_a:04X} opB=0x{op_b:04X} "
            f"result=0x{result:04X} expected=0x{expected:04X}"
        )

        if result != expected:
            raise AssertionError(
                f"{op_name}[{i}] mismatch: got 0x{result:04X}, expected 0x{expected:04X}"
            )


async def _run_compare_tests(dut, op_a_list, op_b_list, op_name, quiet=False):
    """`op_a_list`/`op_b_list` are posit BIT PATTERNS, not floats.

    They used to be floats, and the random compare test passed already-encoded
    posits into it -- so every "random" comparison was between the posits of
    two encodings, which are always large and always positive. It never
    compared a negative value at all.
    """
    for i, (op_a, op_b) in enumerate(zip(op_a_list, op_b_list)):
        expected = {
            OPCODE_EQ: 1 if posit.eq(op_a, op_b, POSIT_NBITS, POSIT_ES) else 0,
            OPCODE_LT: 1 if posit.lt(op_a, op_b, POSIT_NBITS, POSIT_ES) else 0,
        }

        for opcode, exp in expected.items():
            result = await compute_posit(dut, op_a, op_b, opcode)
            op_label = "EQ" if opcode == OPCODE_EQ else "LT"
            if not quiet:
                dut._log.info(
                    f"{op_name}_{op_label}[{i}]: "
                    f"opA=0x{op_a:04X} opB=0x{op_b:04X} "
                    f"result=0x{result:04X} expected=0x{exp:04X}"
                )
            if result != exp:
                raise AssertionError(
                    f"{op_name}_{op_label}[{i}] mismatch for "
                    f"0x{op_a:04X} vs 0x{op_b:04X}: "
                    f"got 0x{result:04X}, expected 0x{exp:04X}"
                )
    if quiet:
        dut._log.info(f"{op_name}: {len(op_a_list)} pair(s) match Universal")


async def _run_bitwise_tests(dut, op_a_list, op_b_list, op_name):
    for i, (a, b) in enumerate(zip(op_a_list, op_b_list)):
        op_a = a & 0xFFFF
        op_b = b & 0xFFFF

        expected = {
            OPCODE_AND: op_a & op_b,
            OPCODE_OR: op_a | op_b,
            OPCODE_XOR: op_a ^ op_b,
        }

        for opcode, exp in expected.items():
            result = await compute_posit(dut, op_a, op_b, opcode)
            op_label = ["AND", "OR", "XOR"][opcode - OPCODE_AND]
            dut._log.info(
                f"{op_name}_{op_label}[{i}]: 0x{op_a:04X} 0x{op_b:04X} "
                f"result=0x{result:04X} expected=0x{exp:04X}"
            )
            if result != exp:
                raise AssertionError(
                    f"{op_name}_{op_label}[{i}] mismatch: got 0x{result:04X}, expected 0x{exp:04X}"
                )


@cocotb.test()
async def test_posit_fixed_int_add(dut):
    """Test Posit16 fixed integer addition"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_ADD, OP_A_INT_FIXED, OP_B_INT_FIXED, "FIXED_INT_ADD"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_int_mult(dut):
    """Test Posit16 fixed integer multiplication"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_MULT, OP_A_INT_FIXED, OP_B_INT_FIXED, "FIXED_INT_MULT"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_float_add(dut):
    """Test Posit16 fixed float addition"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_ADD, OP_A_FLOAT_FIXED, OP_B_FLOAT_FIXED, "FIXED_FLOAT_ADD"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_float_mult(dut):
    """Test Posit16 fixed float multiplication"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_MULT, OP_A_FLOAT_FIXED, OP_B_FLOAT_FIXED, "FIXED_FLOAT_MULT"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_int_compare(dut):
    """Test Posit16 fixed integer comparisons through register interface"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_compare_tests(
        dut,
        [_to_posit(v) for v in OP_A_INT_FIXED],
        [_to_posit(v) for v in OP_B_INT_FIXED],
        "FIXED_INT_COMPARE",
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_int_bitwise(dut):
    """Test Posit16 fixed integer bitwise ops through register interface"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_bitwise_tests(dut, OP_A_INT_FIXED, OP_B_INT_FIXED, "FIXED_INT_BITWISE")
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_edge_add_mult(dut):
    """Every named edge pattern against every other, ADD and MULT.

    Zero, one, minpos, maxpos, NaR and the four truncated-exponent patterns,
    crossed: 144 pairs per operation. These are the values the old reference
    model could not describe, so they were never checked.
    """
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    bits = [value for value, _ in POSIT_EDGE_CASES]
    pairs = [(a, b) for a in bits for b in bits]

    for opcode, name in ((OPCODE_ADD, "EDGE_ADD"), (OPCODE_MULT, "EDGE_MULT")):
        await _run_arith_pairs(dut, opcode, pairs, name, quiet=True)

    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_edge_nar_propagates(dut):
    """NaR in, NaR out.

    `is_nar <= X_nar OR Y_nar` in the RTL, and Universal agrees, so this is
    the one special case with a rule simple enough to assert directly rather
    than only through the reference model.
    """
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    nar = 0x8000
    for value, label in POSIT_EDGE_CASES:
        for op_a, op_b, side in ((nar, value, "NaR op x"), (value, nar, "x op NaR")):
            for opcode, symbol in ((OPCODE_ADD, "+"), (OPCODE_MULT, "*")):
                result = await compute_posit(dut, op_a, op_b, opcode)
                if not posit.isnar(result, POSIT_NBITS, POSIT_ES):
                    raise AssertionError(
                        f"NaR did not propagate ({side}, {symbol}, x={label}): "
                        f"0x{op_a:04X} {symbol} 0x{op_b:04X} = 0x{result:04X}, "
                        f"expected 0x{nar:04X}"
                    )
    dut._log.info(
        f"NaR propagates through + and * for all "
        f"{len(POSIT_EDGE_CASES)} edge operands, both sides"
    )

    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_edge_compare(dut):
    """EQ and LT over every pair of named edge patterns, NaR included."""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    bits = [value for value, _ in POSIT_EDGE_CASES]
    op_a_list = [a for a in bits for _ in bits]
    op_b_list = [b for _ in bits for b in bits]

    await _run_compare_tests(dut, op_a_list, op_b_list, "EDGE_COMPARE", quiet=True)
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_random_add_mult(dut):
    """Test Posit16 random addition and multiplication"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    # Random ENCODINGS, not random floats in a narrow range. Drawing from
    # uniform(-100, 100) and encoding only ever produced ordinary mid-range
    # posits: no NaR, no minpos, no maxpos, nothing near the ends of the
    # range where the arithmetic has to saturate.
    rng = random.Random(42)
    pairs = [
        (rng.getrandbits(POSIT_NBITS), rng.getrandbits(POSIT_NBITS)) for _ in range(64)
    ]

    for opcode, name in ((OPCODE_ADD, "RANDOM_ADD"), (OPCODE_MULT, "RANDOM_MULT")):
        await _run_arith_pairs(dut, opcode, pairs, name, quiet=True)

    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_random_compare(dut):
    """Test Posit16 random comparisons through register interface"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    rng = random.Random(42)
    op_a_list = [rng.getrandbits(POSIT_NBITS) for _ in range(64)]
    op_b_list = [rng.getrandbits(POSIT_NBITS) for _ in range(64)]

    await _run_compare_tests(dut, op_a_list, op_b_list, "RANDOM_COMPARE", quiet=True)
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_random_bitwise(dut):
    """Test Posit16 random bitwise ops through register interface"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    rng = random.Random(42)
    op_a_list = [rng.randint(0, 0xFFFF) for _ in range(20)]
    op_b_list = [rng.randint(0, 0xFFFF) for _ in range(20)]

    await _run_bitwise_tests(dut, op_a_list, op_b_list, "RANDOM_BITWISE")
    await FallingEdge(dut.clk)


# ----------------------------------------------------------------
# MAC array
#
# The accumulator is posit arithmetic, so the reference has to accumulate in
# the posit type too -- summing in float and encoding at the end rounds once
# instead of once per step and disagrees with the hardware on the third term.
# ----------------------------------------------------------------


@cocotb.test()
async def test_mac_single_lane_dot_product(dut):
    """acc <- acc + x*y, one lane, checked against Universal step by step."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)

    pairs = [(1.0, 2.0), (0.5, 4.0), (3.0, 0.25), (2.5, -1.5)]

    acc = await mac_command(dut, OPCODE_MAC_CLEAR)
    assert acc == 0, f"CLEAR left the accumulator at {acc:#06x}, expected 0"

    expected = 0
    for i, (x, y) in enumerate(pairs):
        op_a, op_b = _to_posit(x), _to_posit(y)
        await mac_command(dut, OPCODE_MAC_LOAD, opA=op_a, opB=op_b)
        acc = await mac_command(dut, OPCODE_MAC_RUN)

        product = posit.mul(op_a, op_b, POSIT_NBITS, POSIT_ES)
        expected = posit.add(expected, product, POSIT_NBITS, POSIT_ES)
        assert acc == expected, (
            f"MAC[{i}]: after {x} * {y} the accumulator is {acc:#06x} "
            f"({posit.decode(acc, POSIT_NBITS, POSIT_ES)}), expected "
            f"{expected:#06x} ({posit.decode(expected, POSIT_NBITS, POSIT_ES)})"
        )

    dut._log.info(
        f"MAC dot product over {len(pairs)} terms = "
        f"{posit.decode(acc, POSIT_NBITS, POSIT_ES)}"
    )

    acc = await mac_command(dut, OPCODE_MAC_CLEAR)
    assert acc == 0, f"CLEAR after accumulating left {acc:#06x}, expected 0"


@cocotb.test()
async def test_mac_lanes_are_independent(dut):
    """Each lane keeps its own operands and its own accumulator.

    Loading is per-lane and RUN fires every lane at once, so this also checks
    that one RUN advances all of them -- the property that makes an array worth
    building rather than one unit used N times.

    The lane count is a generic, so it is discovered here instead of asserted:
    a lane that was never built reads back zero, and no lane in this test has
    zero as its expected value.
    """
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)

    await mac_command(dut, OPCODE_MAC_CLEAR)

    # A different product per lane, so a lane reading another lane's state is a
    # mismatch rather than a coincidence. None of them is zero.
    operands = [(float(i + 1), 2.0) for i in range(MAC_MAX_LANES)]
    for lane, (x, y) in enumerate(operands):
        await mac_command(
            dut, OPCODE_MAC_LOAD, lane=lane, opA=_to_posit(x), opB=_to_posit(y)
        )

    await mac_command(dut, OPCODE_MAC_RUN)

    built = 0
    for lane, (x, y) in enumerate(operands):
        acc = await mac_command(dut, OPCODE_MAC_READ, lane=lane)
        if acc == 0:
            continue  # this lane was not built
        built += 1
        expected = posit.mul(_to_posit(x), _to_posit(y), POSIT_NBITS, POSIT_ES)
        assert acc == expected, (
            f"lane {lane}: accumulator {acc:#06x} after one RUN of {x} * {y}, "
            f"expected {expected:#06x}"
        )

    assert built >= 1, "no MAC lane responded; lane 0 must always exist"
    dut._log.info(f"{built} MAC lane(s) built, all accumulated on one RUN")


@cocotb.test()
async def test_mac_does_not_disturb_the_alu(dut):
    """The ALU opcodes bypass into lane 0; that must not touch its state."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)

    op_a, op_b = _to_posit(3.0), _to_posit(4.0)
    await mac_command(dut, OPCODE_MAC_CLEAR)
    await mac_command(dut, OPCODE_MAC_LOAD, opA=op_a, opB=op_b)
    acc_before = await mac_command(dut, OPCODE_MAC_RUN)

    # Plain add and multiply run through lane 0's adder and multiplier.
    other_a, other_b = _to_posit(7.0), _to_posit(9.0)
    got_add = await compute_posit(dut, other_a, other_b, OPCODE_ADD)
    got_mul = await compute_posit(dut, other_a, other_b, OPCODE_MULT)
    assert got_add == posit.add(other_a, other_b, POSIT_NBITS, POSIT_ES)
    assert got_mul == posit.mul(other_a, other_b, POSIT_NBITS, POSIT_ES)

    acc_after = await mac_command(dut, OPCODE_MAC_READ)
    assert acc_after == acc_before, (
        f"the accumulator moved from {acc_before:#06x} to {acc_after:#06x} "
        "while the ALU borrowed lane 0"
    )
