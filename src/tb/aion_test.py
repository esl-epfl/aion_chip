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
from posit import convert

logging.getLogger("cocotb").setLevel(logging.DEBUG)

OPCODE_ADD = 0b0000
OPCODE_MULT = 0b0001
OPCODE_EQ = 0b0010
OPCODE_LT = 0b0011
OPCODE_AND = 0b0100
OPCODE_OR = 0b0101
OPCODE_XOR = 0b0110

POSIT_NBITS = 16
POSIT_ES = 2

# Fixed integer test patterns
OP_A_INT_FIXED = [1, 2, 100]
OP_B_INT_FIXED = [3, 6, 20]

# Fixed float test patterns
OP_A_FLOAT_FIXED = [0.0, 0.1, 0.5, 3.1]
OP_B_FLOAT_FIXED = [2.5, 3.2, 12.2, 6.0]

REG_OP_A_LO = 0
REG_OP_A_HI = 1
REG_OP_B_LO = 2
REG_OP_B_HI = 3
REG_CONTROL = 4
REG_RESULT_LO = 5
REG_RESULT_HI = 6
REG_STATUS = 7


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


def _to_posit(value: float) -> int:
    return int(convert(value, POSIT_NBITS, POSIT_ES)["bits"], 2)


def _from_posit(value: int) -> float:
    return posit.posit_to_float(f"{value:0{POSIT_NBITS}b}", POSIT_NBITS, POSIT_ES)


def _signed_posit(value: int) -> int:
    return int.from_bytes(value.to_bytes(2, "big"), "big", signed=True)


async def _run_fixed_tests(dut, opcode, op_a_list, op_b_list, op_name):
    for i, (a, b) in enumerate(zip(op_a_list, op_b_list)):
        op_a = _to_posit(a)
        op_b = _to_posit(b)
        result = await compute_posit(dut, op_a, op_b, opcode)

        # Use the decoded posit values for the expected result, since the
        # hardware operates on the encoded posit operands, not the original
        # Python floats.
        a_posit = _from_posit(op_a)
        b_posit = _from_posit(op_b)
        if opcode == OPCODE_ADD:
            expected_float = a_posit + b_posit
        else:
            expected_float = a_posit * b_posit
        expected = _to_posit(expected_float)

        dut._log.info(
            f"{op_name}[{i}]: {a} {('*' if opcode == OPCODE_MULT else '+')} {b} "
            f"opA=0x{op_a:04X} opB=0x{op_b:04X} "
            f"result=0x{result:04X} expected=0x{expected:04X}"
        )

        if result != expected:
            raise AssertionError(
                f"{op_name}[{i}] mismatch: got 0x{result:04X}, expected 0x{expected:04X}"
            )


async def _run_compare_tests(dut, op_a_list, op_b_list, op_name):
    for i, (a, b) in enumerate(zip(op_a_list, op_b_list)):
        op_a = _to_posit(a)
        op_b = _to_posit(b)

        a_signed = _signed_posit(op_a)
        b_signed = _signed_posit(op_b)

        expected = {
            OPCODE_EQ: 1 if a_signed == b_signed else 0,
            OPCODE_LT: 1 if a_signed < b_signed else 0,
        }

        for opcode, exp in expected.items():
            result = await compute_posit(dut, op_a, op_b, opcode)
            op_label = "EQ" if opcode == OPCODE_EQ else "LT"
            dut._log.info(
                f"{op_name}_{op_label}[{i}]: {a} vs {b} "
                f"opA=0x{op_a:04X} opB=0x{op_b:04X} "
                f"result=0x{result:04X} expected=0x{exp:04X}"
            )
            if result != exp:
                raise AssertionError(
                    f"{op_name}_{op_label}[{i}] mismatch: got 0x{result:04X}, expected 0x{exp:04X}"
                )


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
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_ADD, OP_A_INT_FIXED, OP_B_INT_FIXED, "FIXED_INT_ADD"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_int_mult(dut):
    """Test Posit16 fixed integer multiplication"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_MULT, OP_A_INT_FIXED, OP_B_INT_FIXED, "FIXED_INT_MULT"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_float_add(dut):
    """Test Posit16 fixed float addition"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_ADD, OP_A_FLOAT_FIXED, OP_B_FLOAT_FIXED, "FIXED_FLOAT_ADD"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_float_mult(dut):
    """Test Posit16 fixed float multiplication"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_fixed_tests(
        dut, OPCODE_MULT, OP_A_FLOAT_FIXED, OP_B_FLOAT_FIXED, "FIXED_FLOAT_MULT"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_int_compare(dut):
    """Test Posit16 fixed integer comparisons through register interface"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_compare_tests(
        dut, OP_A_INT_FIXED, OP_B_INT_FIXED, "FIXED_INT_COMPARE"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_fixed_int_bitwise(dut):
    """Test Posit16 fixed integer bitwise ops through register interface"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    await _run_bitwise_tests(
        dut, OP_A_INT_FIXED, OP_B_INT_FIXED, "FIXED_INT_BITWISE"
    )
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_random_add_mult(dut):
    """Test Posit16 random addition and multiplication"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    rng = random.Random(42)
    for i in range(20):
        a = rng.uniform(-100, 100)
        b = rng.uniform(-100, 100)

        for opcode, op_symbol in ((OPCODE_ADD, "+"), (OPCODE_MULT, "*")):
            op_a = _to_posit(a)
            op_b = _to_posit(b)
            result = await compute_posit(dut, op_a, op_b, opcode)

            # Use the decoded posit values for the expected result.
            a_posit = _from_posit(op_a)
            b_posit = _from_posit(op_b)
            if opcode == OPCODE_ADD:
                expected_float = a_posit + b_posit
            else:
                expected_float = a_posit * b_posit
            expected = _to_posit(expected_float)

            dut._log.info(
                f"RANDOM_{op_symbol}[{i}]: {a:.6f} {op_symbol} {b:.6f} "
                f"opA=0x{op_a:04X} opB=0x{op_b:04X} "
                f"result=0x{result:04X} expected=0x{expected:04X}"
            )

            if result != expected:
                raise AssertionError(
                    f"RANDOM_{op_symbol}[{i}] mismatch: got 0x{result:04X}, expected 0x{expected:04X}"
                )

    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_random_compare(dut):
    """Test Posit16 random comparisons through register interface"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    rng = random.Random(42)
    op_a_list = []
    op_b_list = []
    for _ in range(20):
        op_a_list.append(_to_posit(rng.uniform(-100, 100)))
        op_b_list.append(_to_posit(rng.uniform(-100, 100)))

    await _run_compare_tests(dut, op_a_list, op_b_list, "RANDOM_COMPARE")
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_random_bitwise(dut):
    """Test Posit16 random bitwise ops through register interface"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    rng = random.Random(42)
    op_a_list = [rng.randint(0, 0xFFFF) for _ in range(20)]
    op_b_list = [rng.randint(0, 0xFFFF) for _ in range(20)]

    await _run_bitwise_tests(dut, op_a_list, op_b_list, "RANDOM_BITWISE")
    await FallingEdge(dut.clk)
