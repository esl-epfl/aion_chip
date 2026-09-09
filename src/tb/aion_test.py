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

POSIT_NBITS = 32
POSIT_ES = 2
POSIT_MASK = (1 << POSIT_NBITS) - 1
POSIT_BYTES = POSIT_NBITS // 8

#: Width for the hex in every log line and assertion message.
HEX = POSIT_NBITS // 4

# Fixed integer test patterns
OP_A_INT_FIXED = [1, 2, 100]
OP_B_INT_FIXED = [3, 6, 20]

# Fixed float test patterns
OP_A_FLOAT_FIXED = [0.0, 0.1, 0.5, 3.1]
OP_B_FLOAT_FIXED = [2.5, 3.2, 12.2, 6.0]

# Posit<32,2> bit patterns worth naming, crossed with each other below.
#
# The last four are the ones a hand-written reference model gets wrong: their
# exponent field runs off the end of the word, so the missing bits have to be
# read as zeros, and a model that reads them as absent decodes every one of
# them to half its true value. A reference that is wrong at the ends of the
# range cannot fail there, so the hardware would never actually be checked
# against them -- which is why the reference here is Stillwater Universal.
POSIT_EDGE_CASES = (
    (0x00000000, "zero"),
    (0x40000000, "one"),
    (0xC0000000, "-one"),
    (0x00000001, "minpos"),
    (0xFFFFFFFF, "-minpos"),
    (0x7FFFFFFF, "maxpos"),
    (0x80000001, "-maxpos"),
    (0x80000000, "NaR"),
    (0x00000003, "small, truncated exponent"),
    (0xFFFFFFFD, "-small, truncated exponent"),
    (0x7FFFFFFD, "large, truncated exponent"),
    (0x80000003, "-large, truncated exponent"),
)

# Register map. A Posit<32,2> operand is four bytes, so the map spans fourteen
# addresses and `ui_in[3:0]` carries the address (it was `ui_in[2:0]` for the
# eight-register Posit<16,2> map).
REG_OP_A = 0x0  # 0x0 .. 0x3, little-endian
REG_OP_B = 0x4  # 0x4 .. 0x7, little-endian
REG_CONTROL = 0x8
REG_RESULT = 0x9  # 0x9 .. 0xC, little-endian
REG_STATUS = 0xD

CLK_PERIOD_NS = 50


async def reset(dut):
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


async def write_reg(dut, addr, data):
    dut.ui_in.value = 0x80 | (addr & 0x0F)
    dut.uio_in.value = data & 0xFF
    await RisingEdge(dut.clk)
    dut.ui_in.value = addr & 0x0F
    dut.uio_in.value = 0


async def read_reg(dut, addr):
    dut.ui_in.value = addr & 0x0F
    await RisingEdge(dut.clk)
    return int(dut.uo_out.value)


async def write_operand(dut, base, value):
    """Write one 32-bit operand, low byte first."""
    for i in range(POSIT_BYTES):
        await write_reg(dut, base + i, (value >> (8 * i)) & 0xFF)


async def read_result(dut):
    """Read the 32-bit result register, low byte first."""
    value = 0
    for i in range(POSIT_BYTES):
        value |= (await read_reg(dut, REG_RESULT + i)) << (8 * i)
    return value


async def probe_status(dut, addr=None):
    """Sample `status` mid-cycle, where the level is unambiguous.

    `read_reg` samples on a rising edge, which is exactly when `done` changes;
    which side of that edge cocotb reads is not something a test about the
    handshake should depend on. This waits for the falling edge instead, so the
    assertion is about the level during a named cycle.
    """
    dut.ui_in.value = REG_STATUS if addr is None else addr
    await FallingEdge(dut.clk)
    return int(dut.uo_out.value)


async def compute_posit(dut, opA, opB, opcode):
    await write_operand(dut, REG_OP_A, opA)
    await write_operand(dut, REG_OP_B, opB)
    await write_reg(dut, REG_CONTROL, (opcode & 0x0F) | 0x80)

    while True:
        status = await read_reg(dut, REG_STATUS)
        if status & 0x01:
            break

    return await read_result(dut)


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
                f"{op_name}[{i}]: 0x{op_a:0{HEX}X} {symbol} 0x{op_b:0{HEX}X} "
                f"result=0x{result:0{HEX}X} expected=0x{expected:0{HEX}X}"
            )
        if result != expected:
            raise AssertionError(
                f"{op_name}[{i}] mismatch: "
                f"0x{op_a:0{HEX}X} ({posit.decode(op_a, POSIT_NBITS, POSIT_ES)!r}) "
                f"{symbol} 0x{op_b:0{HEX}X} ({posit.decode(op_b, POSIT_NBITS, POSIT_ES)!r}): "
                f"got 0x{result:0{HEX}X}, expected 0x{expected:0{HEX}X}"
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
            f"opA=0x{op_a:0{HEX}X} opB=0x{op_b:0{HEX}X} "
            f"result=0x{result:0{HEX}X} expected=0x{expected:0{HEX}X}"
        )

        if result != expected:
            raise AssertionError(
                f"{op_name}[{i}] mismatch: got 0x{result:0{HEX}X}, "
                f"expected 0x{expected:0{HEX}X}"
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
                    f"opA=0x{op_a:0{HEX}X} opB=0x{op_b:0{HEX}X} "
                    f"result=0x{result:0{HEX}X} expected=0x{exp:0{HEX}X}"
                )
            if result != exp:
                raise AssertionError(
                    f"{op_name}_{op_label}[{i}] mismatch for "
                    f"0x{op_a:0{HEX}X} vs 0x{op_b:0{HEX}X}: "
                    f"got 0x{result:0{HEX}X}, expected 0x{exp:0{HEX}X}"
                )
    if quiet:
        dut._log.info(f"{op_name}: {len(op_a_list)} pair(s) match Universal")


async def _run_bitwise_tests(dut, op_a_list, op_b_list, op_name):
    for i, (a, b) in enumerate(zip(op_a_list, op_b_list)):
        op_a = a & POSIT_MASK
        op_b = b & POSIT_MASK

        expected = {
            OPCODE_AND: op_a & op_b,
            OPCODE_OR: op_a | op_b,
            OPCODE_XOR: op_a ^ op_b,
        }

        for opcode, exp in expected.items():
            result = await compute_posit(dut, op_a, op_b, opcode)
            op_label = ["AND", "OR", "XOR"][opcode - OPCODE_AND]
            dut._log.info(
                f"{op_name}_{op_label}[{i}]: 0x{op_a:0{HEX}X} 0x{op_b:0{HEX}X} "
                f"result=0x{result:0{HEX}X} expected=0x{exp:0{HEX}X}"
            )
            if result != exp:
                raise AssertionError(
                    f"{op_name}_{op_label}[{i}] mismatch: got 0x{result:0{HEX}X}, "
                    f"expected 0x{exp:0{HEX}X}"
                )


@cocotb.test()
async def test_register_map(dut):
    """Every operand byte is its own address, and reads back what was written.

    A four-byte operand needs fourteen addresses where two-byte operands
    needed eight, so this checks the widened decode directly rather than only
    through an arithmetic result -- a byte lane wired to the wrong address
    would otherwise only show up as a wrong posit.
    """
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)

    op_a, op_b = 0x89ABCDEF, 0x01234567
    await write_operand(dut, REG_OP_A, op_a)
    await write_operand(dut, REG_OP_B, op_b)

    for base, value, name in ((REG_OP_A, op_a, "opA"), (REG_OP_B, op_b, "opB")):
        for i in range(POSIT_BYTES):
            got = await read_reg(dut, base + i)
            exp = (value >> (8 * i)) & 0xFF
            assert got == exp, (
                f"{name} byte {i} (addr {base + i:#x}): got {got:#04x}, "
                f"expected {exp:#04x}"
            )

    # An address past the end of the map reads zero rather than aliasing onto
    # a real register.
    for addr in (0xE, 0xF):
        got = await read_reg(dut, addr)
        assert got == 0, f"unmapped address {addr:#x} read {got:#04x}, expected 0"

    await FallingEdge(dut.clk)


@cocotb.test()
async def test_done_is_a_completion_level(dut):
    """`done` reports completion: cleared on the command, raised when it is.

    Two properties, and the arithmetic tests can check neither. They poll until
    `done` and then read `result` -- but the result settles in the same cycle
    the flag would have risen anyway, so a `done` that was a stale 1 left over
    from the previous operation, or a one-cycle pulse, still hands them the
    right answer. Only the handshake itself distinguishes them.
    """
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)

    assert not ((await probe_status(dut)) & 0x01), (
        "done was set out of reset, before anything had been computed"
    )

    op_a, op_b = _to_posit(3.0), _to_posit(4.0)
    first = await compute_posit(dut, op_a, op_b, OPCODE_MULT)
    assert first == posit.mul(op_a, op_b, POSIT_NBITS, POSIT_ES)

    # It is a level, not a pulse: software that polls late still sees it.
    for cycle in range(4):
        assert (await probe_status(dut)) & 0x01, (
            f"done dropped {cycle + 1} cycle(s) after completing; a poll that "
            "arrives late would wait forever"
        )

    # A new command clears it on the edge that accepts the command.
    new_a, new_b = _to_posit(5.0), _to_posit(6.0)
    await write_operand(dut, REG_OP_A, new_a)
    await write_operand(dut, REG_OP_B, new_b)
    await write_reg(dut, REG_CONTROL, OPCODE_MULT | 0x80)

    assert not ((await probe_status(dut)) & 0x01), (
        "done was still set in the cycle after the command was accepted -- a "
        "poll issued here is answered by the previous operation's flag"
    )
    assert (await probe_status(dut)) & 0x01, (
        "done did not rise one cycle after the command was accepted"
    )

    got = await read_result(dut)
    expected = posit.mul(new_a, new_b, POSIT_NBITS, POSIT_ES)
    assert got == expected, (
        f"result at the moment done rose is 0x{got:0{HEX}X}, expected "
        f"0x{expected:0{HEX}X}"
    )

    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_int_add(dut):
    """Test Posit32 fixed integer addition"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_ADD, OP_A_INT_FIXED, OP_B_INT_FIXED, "FIXED_INT_ADD"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_int_mult(dut):
    """Test Posit32 fixed integer multiplication"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_MULT, OP_A_INT_FIXED, OP_B_INT_FIXED, "FIXED_INT_MULT"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_float_add(dut):
    """Test Posit32 fixed float addition"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_ADD, OP_A_FLOAT_FIXED, OP_B_FLOAT_FIXED, "FIXED_FLOAT_ADD"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_float_mult(dut):
    """Test Posit32 fixed float multiplication"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_MULT, OP_A_FLOAT_FIXED, OP_B_FLOAT_FIXED, "FIXED_FLOAT_MULT"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_int_compare(dut):
    """Test Posit32 fixed integer comparisons through register interface"""
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
    """Test Posit32 fixed integer bitwise ops through register interface"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_bitwise_tests(dut, OP_A_INT_FIXED, OP_B_INT_FIXED, "FIXED_INT_BITWISE")
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_edge_add_mult(dut):
    """Every named edge pattern against every other, ADD and MULT.

    Zero, one, minpos, maxpos, NaR and the four truncated-exponent patterns,
    crossed: 144 pairs per operation. These are the values a hand-written
    reference model cannot describe, so without Universal they could not be
    checked.
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

    nar = 0x80000000
    for value, label in POSIT_EDGE_CASES:
        for op_a, op_b, side in ((nar, value, "NaR op x"), (value, nar, "x op NaR")):
            for opcode, symbol in ((OPCODE_ADD, "+"), (OPCODE_MULT, "*")):
                result = await compute_posit(dut, op_a, op_b, opcode)
                if not posit.isnar(result, POSIT_NBITS, POSIT_ES):
                    raise AssertionError(
                        f"NaR did not propagate ({side}, {symbol}, x={label}): "
                        f"0x{op_a:0{HEX}X} {symbol} 0x{op_b:0{HEX}X} = "
                        f"0x{result:0{HEX}X}, expected 0x{nar:0{HEX}X}"
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
    """Test Posit32 random addition and multiplication"""
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
    """Test Posit32 random comparisons through register interface"""
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
    """Test Posit32 random bitwise ops through register interface"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    rng = random.Random(42)
    op_a_list = [rng.getrandbits(POSIT_NBITS) for _ in range(20)]
    op_b_list = [rng.getrandbits(POSIT_NBITS) for _ in range(20)]

    await _run_bitwise_tests(dut, op_a_list, op_b_list, "RANDOM_BITWISE")
    await FallingEdge(dut.clk)
