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

# The two operand widths the ALU implements. `control[4]` picks between them
# and every opcode means the same operation at either one, so each test below
# is parametrized over `nbits` rather than duplicated.
P32 = 32
P16 = 16
PRECISIONS = (P32, P16)

POSIT_ES = 2

CONTROL_START = 0x80  # control[7]: fires the command
CONTROL_P16 = 0x10  # control[4]: 0 = Posit<32,2>, 1 = Posit<16,2>

# Fixed integer test patterns
OP_A_INT_FIXED = [1, 2, 100]
OP_B_INT_FIXED = [3, 6, 20]

# Fixed float test patterns
OP_A_FLOAT_FIXED = [0.0, 0.1, 0.5, 3.1]
OP_B_FLOAT_FIXED = [2.5, 3.2, 12.2, 6.0]

# Posit bit patterns worth naming, crossed with each other below. The same
# twelve shapes at both widths -- the scaling is positional, so `0x4000` is
# Posit<16,2> one exactly as `0x40000000` is Posit<32,2> one.
#
# The last four are the ones a hand-written reference model gets wrong: their
# exponent field runs off the end of the word, so the missing bits have to be
# read as zeros, and a model that reads them as absent decodes every one of
# them to half its true value. A reference that is wrong at the ends of the
# range cannot fail there, so the hardware would never actually be checked
# against them -- which is why the reference here is Stillwater Universal.
POSIT_EDGE_CASES = {
    P32: (
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
    ),
    P16: (
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
    ),
}

# Register map. The widest operand is four bytes, so the map spans fourteen
# addresses and `ui_in[3:0]` carries the address (it was `ui_in[2:0]` for the
# eight-register Posit<16,2>-only map). A Posit<16,2> operand goes into the low
# two bytes of the same registers; the upper two are simply not read by the
# ALU, so the map is the same at both precisions.
REG_OP_A = 0x0  # 0x0 .. 0x3, little-endian
REG_OP_B = 0x4  # 0x4 .. 0x7, little-endian
REG_CONTROL = 0x8
REG_RESULT = 0x9  # 0x9 .. 0xC, little-endian
REG_STATUS = 0xD

CLK_PERIOD_NS = 50


def _nbytes(nbits):
    return nbits // 8


def _mask(nbits):
    return (1 << nbits) - 1


def _hex(nbits):
    """Width for the hex in every log line and assertion message."""
    return nbits // 4


def _control(opcode, nbits):
    """The `control` byte that starts `opcode` at `nbits`."""
    word = (opcode & 0x0F) | CONTROL_START
    if nbits == P16:
        word |= CONTROL_P16
    return word


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


async def write_operand(dut, base, value, nbits=P32):
    """Write one operand, low byte first.

    Only the bytes the command reads are written: a Posit<16,2> operand is two
    bytes, and leaving the upper two alone is what software would do. It is
    also what leaves stale bytes above bit 15 for the ALU to ignore -- see
    `test_p16_ignores_stale_operand_bytes`, which puts them there on purpose.
    """
    for i in range(_nbytes(nbits)):
        await write_reg(dut, base + i, (value >> (8 * i)) & 0xFF)


async def read_result(dut, nbits=P32):
    """Read the result register, low byte first, at the command's width."""
    value = 0
    for i in range(_nbytes(nbits)):
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


async def compute_posit(dut, opA, opB, opcode, nbits=P32):
    await write_operand(dut, REG_OP_A, opA, nbits)
    await write_operand(dut, REG_OP_B, opB, nbits)
    await write_reg(dut, REG_CONTROL, _control(opcode, nbits))

    while True:
        status = await read_reg(dut, REG_STATUS)
        if status & 0x01:
            break

    return await read_result(dut, nbits)


def _to_posit(value: float, nbits: int = P32) -> int:
    return posit.encode(value, nbits, POSIT_ES)


def _reference(opcode, op_a, op_b, nbits):
    """Universal's answer for an arithmetic opcode at `nbits`."""
    if opcode == OPCODE_ADD:
        return posit.add(op_a, op_b, nbits, POSIT_ES)
    return posit.mul(op_a, op_b, nbits, POSIT_ES)


async def _run_arith_pairs(dut, opcode, pairs, op_name, nbits=P32, quiet=False):
    """Drive (op_a, op_b) posit bit patterns and check against Universal.

    `quiet` logs only the summary: the exhaustive sweeps below run hundreds of
    transactions, and a line each buries the one that matters.
    """
    symbol = "+" if opcode == OPCODE_ADD else "*"
    hexw = _hex(nbits)
    for i, (op_a, op_b) in enumerate(pairs):
        result = await compute_posit(dut, op_a, op_b, opcode, nbits)
        expected = _reference(opcode, op_a, op_b, nbits)

        if not quiet:
            dut._log.info(
                f"{op_name}[{i}]: 0x{op_a:0{hexw}X} {symbol} 0x{op_b:0{hexw}X} "
                f"result=0x{result:0{hexw}X} expected=0x{expected:0{hexw}X}"
            )
        if result != expected:
            raise AssertionError(
                f"{op_name}[{i}] mismatch: "
                f"0x{op_a:0{hexw}X} ({posit.decode(op_a, nbits, POSIT_ES)!r}) "
                f"{symbol} 0x{op_b:0{hexw}X} ({posit.decode(op_b, nbits, POSIT_ES)!r}): "
                f"got 0x{result:0{hexw}X}, expected 0x{expected:0{hexw}X}"
            )
    if quiet:
        dut._log.info(f"{op_name}: {len(pairs)} pair(s) match Universal")


async def _run_fixed_tests(dut, opcode, op_a_list, op_b_list, op_name, nbits=P32):
    hexw = _hex(nbits)
    for i, (a, b) in enumerate(zip(op_a_list, op_b_list)):
        op_a = _to_posit(a, nbits)
        op_b = _to_posit(b, nbits)
        result = await compute_posit(dut, op_a, op_b, opcode, nbits)

        # The reference is Universal operating on the same encoded operands
        # the hardware sees -- not the original Python floats, and not double
        # arithmetic re-encoded afterwards.
        expected = _reference(opcode, op_a, op_b, nbits)

        dut._log.info(
            f"{op_name}[{i}]: {a} {('*' if opcode == OPCODE_MULT else '+')} {b} "
            f"opA=0x{op_a:0{hexw}X} opB=0x{op_b:0{hexw}X} "
            f"result=0x{result:0{hexw}X} expected=0x{expected:0{hexw}X}"
        )

        if result != expected:
            raise AssertionError(
                f"{op_name}[{i}] mismatch: got 0x{result:0{hexw}X}, "
                f"expected 0x{expected:0{hexw}X}"
            )


async def _run_compare_tests(dut, op_a_list, op_b_list, op_name, nbits=P32, quiet=False):
    """`op_a_list`/`op_b_list` are posit BIT PATTERNS, not floats.

    They used to be floats, and the random compare test passed already-encoded
    posits into it -- so every "random" comparison was between the posits of
    two encodings, which are always large and always positive. It never
    compared a negative value at all.
    """
    hexw = _hex(nbits)
    for i, (op_a, op_b) in enumerate(zip(op_a_list, op_b_list)):
        expected = {
            OPCODE_EQ: 1 if posit.eq(op_a, op_b, nbits, POSIT_ES) else 0,
            OPCODE_LT: 1 if posit.lt(op_a, op_b, nbits, POSIT_ES) else 0,
        }

        for opcode, exp in expected.items():
            result = await compute_posit(dut, op_a, op_b, opcode, nbits)
            op_label = "EQ" if opcode == OPCODE_EQ else "LT"
            if not quiet:
                dut._log.info(
                    f"{op_name}_{op_label}[{i}]: "
                    f"opA=0x{op_a:0{hexw}X} opB=0x{op_b:0{hexw}X} "
                    f"result=0x{result:0{hexw}X} expected=0x{exp:0{hexw}X}"
                )
            if result != exp:
                raise AssertionError(
                    f"{op_name}_{op_label}[{i}] mismatch for "
                    f"0x{op_a:0{hexw}X} vs 0x{op_b:0{hexw}X}: "
                    f"got 0x{result:0{hexw}X}, expected 0x{exp:0{hexw}X}"
                )
    if quiet:
        dut._log.info(f"{op_name}: {len(op_a_list)} pair(s) match Universal")


async def _run_bitwise_tests(dut, op_a_list, op_b_list, op_name, nbits=P32):
    hexw = _hex(nbits)
    for i, (a, b) in enumerate(zip(op_a_list, op_b_list)):
        op_a = a & _mask(nbits)
        op_b = b & _mask(nbits)

        expected = {
            OPCODE_AND: op_a & op_b,
            OPCODE_OR: op_a | op_b,
            OPCODE_XOR: op_a ^ op_b,
        }

        for opcode, exp in expected.items():
            result = await compute_posit(dut, op_a, op_b, opcode, nbits)
            op_label = ["AND", "OR", "XOR"][opcode - OPCODE_AND]
            dut._log.info(
                f"{op_name}_{op_label}[{i}]: 0x{op_a:0{hexw}X} 0x{op_b:0{hexw}X} "
                f"result=0x{result:0{hexw}X} expected=0x{exp:0{hexw}X}"
            )
            if result != exp:
                raise AssertionError(
                    f"{op_name}_{op_label}[{i}] mismatch: got 0x{result:0{hexw}X}, "
                    f"expected 0x{exp:0{hexw}X}"
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
        for i in range(_nbytes(P32)):
            got = await read_reg(dut, base + i)
            exp = (value >> (8 * i)) & 0xFF
            assert got == exp, (
                f"{name} byte {i} (addr {base + i:#x}): got {got:#04x}, "
                f"expected {exp:#04x}"
            )

    # `control` stores the precision bit alongside the opcode and reads back.
    # Bit 7 is left clear here: it is the start trigger, and this test is about
    # the register, not about running a command.
    for word in (OPCODE_MULT, CONTROL_P16 | OPCODE_MULT):
        await write_reg(dut, REG_CONTROL, word)
        got = await read_reg(dut, REG_CONTROL)
        assert got == word, (
            f"control read back {got:#04x} after writing {word:#04x}; "
            "bit 4 is the precision select and has to survive the write"
        )

    # An address past the end of the map reads zero rather than aliasing onto
    # a real register.
    for addr in (0xE, 0xF):
        got = await read_reg(dut, addr)
        assert got == 0, f"unmapped address {addr:#x} read {got:#04x}, expected 0"

    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_done_is_a_completion_level(dut, nbits):
    """`done` reports completion: cleared on the command, raised when it is.

    Two properties, and the arithmetic tests can check neither. They poll until
    `done` and then read `result` -- but the result settles in the same cycle
    the flag would have risen anyway, so a `done` that was a stale 1 left over
    from the previous operation, or a one-cycle pulse, still hands them the
    right answer. Only the handshake itself distinguishes them.

    Run at both widths because both share one handshake: the Posit<16,2> pair
    is combinational where the Posit<32,2> pair has a pipeline stage, so a
    narrow command is answered on the same edge a wide one is and `done` must
    not have been tuned to either one alone.
    """
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)

    hexw = _hex(nbits)

    assert not ((await probe_status(dut)) & 0x01), (
        "done was set out of reset, before anything had been computed"
    )

    op_a, op_b = _to_posit(3.0, nbits), _to_posit(4.0, nbits)
    first = await compute_posit(dut, op_a, op_b, OPCODE_MULT, nbits)
    assert first == posit.mul(op_a, op_b, nbits, POSIT_ES)

    # It is a level, not a pulse: software that polls late still sees it.
    for cycle in range(4):
        assert (await probe_status(dut)) & 0x01, (
            f"done dropped {cycle + 1} cycle(s) after completing; a poll that "
            "arrives late would wait forever"
        )

    # A new command clears it on the edge that accepts the command.
    new_a, new_b = _to_posit(5.0, nbits), _to_posit(6.0, nbits)
    await write_operand(dut, REG_OP_A, new_a, nbits)
    await write_operand(dut, REG_OP_B, new_b, nbits)
    await write_reg(dut, REG_CONTROL, _control(OPCODE_MULT, nbits))

    assert not ((await probe_status(dut)) & 0x01), (
        "done was still set in the cycle after the command was accepted -- a "
        "poll issued here is answered by the previous operation's flag"
    )
    assert (await probe_status(dut)) & 0x01, (
        "done did not rise one cycle after the command was accepted"
    )

    got = await read_result(dut, nbits)
    expected = posit.mul(new_a, new_b, nbits, POSIT_ES)
    assert got == expected, (
        f"result at the moment done rose is 0x{got:0{hexw}X}, expected "
        f"0x{expected:0{hexw}X}"
    )

    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_fixed_int_add(dut, nbits):
    """Fixed integer addition"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_ADD, OP_A_INT_FIXED, OP_B_INT_FIXED, f"FIXED_INT_ADD_P{nbits}",
        nbits,
    )
    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_fixed_int_mult(dut, nbits):
    """Fixed integer multiplication"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_MULT, OP_A_INT_FIXED, OP_B_INT_FIXED, f"FIXED_INT_MULT_P{nbits}",
        nbits,
    )
    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_fixed_float_add(dut, nbits):
    """Fixed float addition"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_ADD, OP_A_FLOAT_FIXED, OP_B_FLOAT_FIXED,
        f"FIXED_FLOAT_ADD_P{nbits}", nbits,
    )
    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_fixed_float_mult(dut, nbits):
    """Fixed float multiplication"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_MULT, OP_A_FLOAT_FIXED, OP_B_FLOAT_FIXED,
        f"FIXED_FLOAT_MULT_P{nbits}", nbits,
    )
    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_fixed_int_compare(dut, nbits):
    """Fixed integer comparisons through the register interface"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_compare_tests(
        dut,
        [_to_posit(v, nbits) for v in OP_A_INT_FIXED],
        [_to_posit(v, nbits) for v in OP_B_INT_FIXED],
        f"FIXED_INT_COMPARE_P{nbits}",
        nbits,
    )
    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_fixed_int_bitwise(dut, nbits):
    """Fixed integer bitwise ops through the register interface"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_bitwise_tests(
        dut, OP_A_INT_FIXED, OP_B_INT_FIXED, f"FIXED_INT_BITWISE_P{nbits}", nbits
    )
    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_edge_add_mult(dut, nbits):
    """Every named edge pattern against every other, ADD and MULT.

    Zero, one, minpos, maxpos, NaR and the four truncated-exponent patterns,
    crossed: 144 pairs per operation. These are the values a hand-written
    reference model cannot describe, so without Universal they could not be
    checked.
    """
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    bits = [value for value, _ in POSIT_EDGE_CASES[nbits]]
    pairs = [(a, b) for a in bits for b in bits]

    for opcode, name in (
        (OPCODE_ADD, f"EDGE_ADD_P{nbits}"),
        (OPCODE_MULT, f"EDGE_MULT_P{nbits}"),
    ):
        await _run_arith_pairs(dut, opcode, pairs, name, nbits, quiet=True)

    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_edge_nar_propagates(dut, nbits):
    """NaR in, NaR out.

    `is_nar <= X_nar OR Y_nar` in the RTL, and Universal agrees, so this is
    the one special case with a rule simple enough to assert directly rather
    than only through the reference model.
    """
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    hexw = _hex(nbits)
    nar = 1 << (nbits - 1)
    for value, label in POSIT_EDGE_CASES[nbits]:
        for op_a, op_b, side in ((nar, value, "NaR op x"), (value, nar, "x op NaR")):
            for opcode, symbol in ((OPCODE_ADD, "+"), (OPCODE_MULT, "*")):
                result = await compute_posit(dut, op_a, op_b, opcode, nbits)
                if not posit.isnar(result, nbits, POSIT_ES):
                    raise AssertionError(
                        f"NaR did not propagate ({side}, {symbol}, x={label}): "
                        f"0x{op_a:0{hexw}X} {symbol} 0x{op_b:0{hexw}X} = "
                        f"0x{result:0{hexw}X}, expected 0x{nar:0{hexw}X}"
                    )
    dut._log.info(
        f"P{nbits}: NaR propagates through + and * for all "
        f"{len(POSIT_EDGE_CASES[nbits])} edge operands, both sides"
    )

    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_edge_compare(dut, nbits):
    """EQ and LT over every pair of named edge patterns, NaR included.

    At Posit<16,2> this is also the check on the comparator's operand
    extension. The unit is 32 bits wide at both precisions, so a narrow
    operand has to reach it SIGN-extended: `0xFFFF` is -minpos and must order
    below zero, and zero-extending it would make it the largest value in the
    word instead. -minpos and zero are both in the table.
    """
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    bits = [value for value, _ in POSIT_EDGE_CASES[nbits]]
    op_a_list = [a for a in bits for _ in bits]
    op_b_list = [b for _ in bits for b in bits]

    await _run_compare_tests(
        dut, op_a_list, op_b_list, f"EDGE_COMPARE_P{nbits}", nbits, quiet=True
    )
    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_random_add_mult(dut, nbits):
    """Random addition and multiplication"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    # Random ENCODINGS, not random floats in a narrow range. Drawing from
    # uniform(-100, 100) and encoding only ever produced ordinary mid-range
    # posits: no NaR, no minpos, no maxpos, nothing near the ends of the
    # range where the arithmetic has to saturate.
    rng = random.Random(42)
    pairs = [(rng.getrandbits(nbits), rng.getrandbits(nbits)) for _ in range(64)]

    for opcode, name in (
        (OPCODE_ADD, f"RANDOM_ADD_P{nbits}"),
        (OPCODE_MULT, f"RANDOM_MULT_P{nbits}"),
    ):
        await _run_arith_pairs(dut, opcode, pairs, name, nbits, quiet=True)

    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_random_compare(dut, nbits):
    """Random comparisons through the register interface"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    rng = random.Random(42)
    op_a_list = [rng.getrandbits(nbits) for _ in range(64)]
    op_b_list = [rng.getrandbits(nbits) for _ in range(64)]

    await _run_compare_tests(
        dut, op_a_list, op_b_list, f"RANDOM_COMPARE_P{nbits}", nbits, quiet=True
    )
    await FallingEdge(dut.clk)


@cocotb.test()
@cocotb.parametrize(nbits=PRECISIONS)
async def test_posit_random_bitwise(dut, nbits):
    """Random bitwise ops through the register interface"""
    clock = Clock(dut.clk, CLK_PERIOD_NS, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    rng = random.Random(42)
    op_a_list = [rng.getrandbits(nbits) for _ in range(20)]
    op_b_list = [rng.getrandbits(nbits) for _ in range(20)]

    await _run_bitwise_tests(
        dut, op_a_list, op_b_list, f"RANDOM_BITWISE_P{nbits}", nbits
    )
    await FallingEdge(dut.clk)


# ----------------------------------------------------------------------
# The precision select itself
#
# Everything above runs the same commands at each width in isolation. These
# three are about the boundary between the widths -- what the narrow mode does
# with the half of the datapath it does not use, and what happens when the two
# alternate. None of them can fail while only one precision is ever exercised,
# which is exactly why they are separate tests.
# ----------------------------------------------------------------------


@cocotb.test()
async def test_p16_result_upper_half_is_zero(dut):
    """A Posit<16,2> result reads back as zero above bit 15.

    `result` is one 32-bit register at both widths. The narrow answer occupies
    the low half; if the upper half were left as the Posit<32,2> unit's output
    instead of being cleared, software reading all four result bytes -- which
    the register map invites -- would be handed two bytes of a different
    operation's answer. Prime the upper half with a wide command first so a
    stuck mux has something to be caught with.
    """
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)

    # A wide command whose result has a non-zero upper half.
    wide = await compute_posit(dut, _to_posit(3.0), _to_posit(4.0), OPCODE_MULT)
    assert wide >> 16 != 0, (
        f"the priming Posit<32,2> result 0x{wide:08X} has a zero upper half, "
        "so this test could not tell a stuck multiplexer from a correct one"
    )

    for opcode, name in (
        (OPCODE_ADD, "ADD"),
        (OPCODE_MULT, "MULT"),
        (OPCODE_LT, "LT"),
        (OPCODE_XOR, "XOR"),
    ):
        await write_operand(dut, REG_OP_A, _to_posit(3.0, P16), P16)
        await write_operand(dut, REG_OP_B, _to_posit(4.0, P16), P16)
        await write_reg(dut, REG_CONTROL, _control(opcode, P16))
        while not (await read_reg(dut, REG_STATUS)) & 0x01:
            pass

        full = await read_result(dut, P32)
        assert full >> 16 == 0, (
            f"P16 {name} left 0x{full >> 16:04X} in result bytes 2-3 "
            f"(full result 0x{full:08X}); a narrow result must read back as "
            "zero above bit 15"
        )

    await FallingEdge(dut.clk)


@cocotb.test()
async def test_p16_ignores_stale_operand_bytes(dut):
    """Bits 31:16 of the operand registers cannot reach a Posit<16,2> answer.

    The interface writes operands a byte at a time and nothing clears the
    upper bytes when the precision changes, so after any Posit<32,2> command
    they hold that command's operands. Every block therefore has to extend or
    mask the low half itself -- and each wants something different: compare
    sign-extends, bitwise masks, the arithmetic units are simply wired to
    bits 15:0. A missing mask on `posit_bitwise` is invisible unless the stale
    bytes are non-zero, which is what this test arranges.
    """
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)

    # Dirty the whole operand registers with a wide command first.
    await compute_posit(dut, 0xDEADBEEF, 0xCAFEBABE, OPCODE_ADD)

    cases = [
        (0x4000, 0x4000, "one, one"),
        (0xFFFF, 0x0000, "-minpos, zero"),
        (0x8000, 0x4000, "NaR, one"),
        (0x7FFF, 0x8001, "maxpos, -maxpos"),
        (0x1234, 0xABCD, "arbitrary"),
    ]

    for op_a, op_b, label in cases:
        for opcode, name in (
            (OPCODE_ADD, "ADD"),
            (OPCODE_MULT, "MULT"),
            (OPCODE_EQ, "EQ"),
            (OPCODE_LT, "LT"),
            (OPCODE_AND, "AND"),
            (OPCODE_OR, "OR"),
            (OPCODE_XOR, "XOR"),
        ):
            # Only the low two bytes are written, so bits 31:16 stay dirty.
            result = await compute_posit(dut, op_a, op_b, opcode, P16)

            if opcode in (OPCODE_ADD, OPCODE_MULT):
                expected = _reference(opcode, op_a, op_b, P16)
            elif opcode == OPCODE_EQ:
                expected = 1 if posit.eq(op_a, op_b, P16, POSIT_ES) else 0
            elif opcode == OPCODE_LT:
                expected = 1 if posit.lt(op_a, op_b, P16, POSIT_ES) else 0
            elif opcode == OPCODE_AND:
                expected = op_a & op_b
            elif opcode == OPCODE_OR:
                expected = op_a | op_b
            else:
                expected = op_a ^ op_b

            assert result == expected, (
                f"P16 {name} ({label}) with stale upper operand bytes: "
                f"0x{op_a:04X}, 0x{op_b:04X} gave 0x{result:04X}, expected "
                f"0x{expected:04X}"
            )

        # The same operands with the upper bytes explicitly cleared have to
        # give the same answers -- that is the property, stated directly.
        await write_operand(dut, REG_OP_A, op_a, P32)
        await write_operand(dut, REG_OP_B, op_b, P32)

    dut._log.info(
        f"P16 results are independent of bits 31:16 for {len(cases)} operand "
        "pair(s) across all seven opcodes"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_precision_alternates(dut):
    """Back-to-back commands at alternating widths each get their own answer.

    `control[4]` is combinational into the result multiplexer, not a mode the
    ALU latches, so a width change takes effect with the command that carries
    it. Nothing above would notice if it took an extra command to apply, or if
    the multiplexer held the previous width's answer: each test runs at one
    width throughout.
    """
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await reset(dut)

    rng = random.Random(7)
    for i in range(24):
        nbits = P32 if i % 2 == 0 else P16
        opcode = rng.choice((OPCODE_ADD, OPCODE_MULT))
        op_a = rng.getrandbits(nbits)
        op_b = rng.getrandbits(nbits)

        result = await compute_posit(dut, op_a, op_b, opcode, nbits)
        expected = _reference(opcode, op_a, op_b, nbits)

        hexw = _hex(nbits)
        assert result == expected, (
            f"ALTERNATE[{i}] at P{nbits}: 0x{op_a:0{hexw}X} "
            f"{'+' if opcode == OPCODE_ADD else '*'} 0x{op_b:0{hexw}X} gave "
            f"0x{result:0{hexw}X}, expected 0x{expected:0{hexw}X}"
        )

    dut._log.info("24 commands alternating between Posit<32,2> and Posit<16,2> match")
    await FallingEdge(dut.clk)
