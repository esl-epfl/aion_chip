# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-03
#  Description:               Posit ALU - Direct Unit Testbench
# ================================================================

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

import posit

OPCODE_ADD = 0b0000
OPCODE_MULT = 0b0001
OPCODE_EQ = 0b0010
OPCODE_LT = 0b0011
OPCODE_AND = 0b0100
OPCODE_OR = 0b0101
OPCODE_XOR = 0b0110

POSIT_NBITS = 16
POSIT_ES = 2


def _to_posit(value: float) -> int:
    return int(posit.convert(value, POSIT_NBITS, POSIT_ES)["bits"], 2)


def _from_posit(value: int) -> float:
    return posit.posit_to_float(f"{value:0{POSIT_NBITS}b}", POSIT_NBITS, POSIT_ES)


async def reset(dut):
    dut.rst_n.value = 0
    dut.opA.value = 0
    dut.opB.value = 0
    dut.opcode.value = 0
    dut.start.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


async def compute(dut, op_a, op_b, opcode):
    dut.opA.value = op_a
    dut.opB.value = op_b
    dut.opcode.value = opcode
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    await RisingEdge(dut.clk)
    return int(dut.result.value)


@cocotb.test()
async def test_posit_alu_add_mult(dut):
    """Test ADD and MULT through the ALU wrapper."""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    test_cases = [(1.0, 2.0), (0.5, 3.2), (-10.0, 4.0)]
    for a, b in test_cases:
        op_a = _to_posit(a)
        op_b = _to_posit(b)

        a_posit = _from_posit(op_a)
        b_posit = _from_posit(op_b)

        expected_add = _to_posit(a_posit + b_posit)
        result_add = await compute(dut, op_a, op_b, OPCODE_ADD)
        assert result_add == expected_add, (
            f"ADD mismatch: {a} + {b}: got 0x{result_add:04X}, expected 0x{expected_add:04X}"
        )

        expected_mult = _to_posit(a_posit * b_posit)
        result_mult = await compute(dut, op_a, op_b, OPCODE_MULT)
        assert result_mult == expected_mult, (
            f"MULT mismatch: {a} * {b}: got 0x{result_mult:04X}, expected 0x{expected_mult:04X}"
        )

    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_alu_compare(dut):
    """Test comparator operations using signed posit ordering."""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    test_cases = [
        (1.0, 2.0),
        (2.0, 1.0),
        (3.0, 3.0),
        (-5.0, -1.0),
        (-1.0, 5.0),
        (0.0, 0.0),
    ]

    for a, b in test_cases:
        op_a = _to_posit(a)
        op_b = _to_posit(b)

        a_signed = int.from_bytes(op_a.to_bytes(2, "big"), "big", signed=True)
        b_signed = int.from_bytes(op_b.to_bytes(2, "big"), "big", signed=True)

        expected = {
            OPCODE_EQ: 1 if a_signed == b_signed else 0,
            OPCODE_LT: 1 if a_signed < b_signed else 0,
        }

        for opcode, exp in expected.items():
            result = await compute(dut, op_a, op_b, opcode)
            op_label = "EQ" if opcode == OPCODE_EQ else "LT"
            assert result == exp, (
                f"CMP mismatch op={op_label} for {a} vs {b}: "
                f"got 0x{result:04X}, expected 0x{exp:04X}"
            )

    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_alu_bitwise(dut):
    """Test bitwise AND/OR/XOR on raw posit bit patterns."""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    rng = random.Random(42)
    for _ in range(20):
        op_a = rng.randint(0, 0xFFFF)
        op_b = rng.randint(0, 0xFFFF)

        expected = {
            OPCODE_AND: op_a & op_b,
            OPCODE_OR: op_a | op_b,
            OPCODE_XOR: op_a ^ op_b,
        }

        for opcode, exp in expected.items():
            result = await compute(dut, op_a, op_b, opcode)
            assert result == exp, (
                f"BITWISE mismatch op=0x{opcode:04X} for 0x{op_a:04X}, 0x{op_b:04X}: "
                f"got 0x{result:04X}, expected 0x{exp:04X}"
            )

    await FallingEdge(dut.clk)
