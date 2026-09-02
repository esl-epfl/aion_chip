# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-01
#  Description:               AION SoC - TinyTapeout Register Interface Cocotb TB
# ================================================================

import logging

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

logging.getLogger("cocotb").setLevel(logging.DEBUG)

OPCODE_ADD = 0
OPCODE_MULT = 1

# Posit16 (es=2) golden vectors: (opA, opB, expected_add, expected_mult)
OPERATIONS = [
    # 1.0 + 1.0 = 2.0, 1.0 * 1.0 = 1.0
    {
        "opA": 0b0100000000000000,
        "opB": 0b0100000000000000,
        "result_add": 0b0100100000000000,
        "result_mult": 0b0100000000000000,
    },
    # 4.0 + 6.0 = 10.0, 4.0 * 6.0 = 24.0
    {
        "opA": 0b0101000000000000,
        "opB": 0b0110000011000000,
        "result_add": 0b0110000111000000,
        "result_mult": 0b0110100011000000,
    },
]

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
    await write_reg(dut, REG_CONTROL, (opcode & 1) | 0x02)

    while True:
        status = await read_reg(dut, REG_STATUS)
        if status & 0x01:
            break

    result_lo = await read_reg(dut, REG_RESULT_LO)
    result_hi = await read_reg(dut, REG_RESULT_HI)
    return (result_hi << 8) | result_lo


@cocotb.test()
async def test_posit_add(dut):
    """Test Posit16 addition via the TinyTapeout register interface"""

    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    await reset(dut)

    for i, op in enumerate(OPERATIONS):
        result = await compute_posit(dut, op["opA"], op["opB"], OPCODE_ADD)
        expected = op["result_add"]

        dut._log.info(
            f"ADD[{i}]: opA=0x{op['opA']:04X} opB=0x{op['opB']:04X} "
            f"result=0x{result:04X} expected=0x{expected:04X}"
        )

        if result != expected:
            raise AssertionError(
                f"ADD[{i}] mismatch: got 0x{result:04X}, expected 0x{expected:04X}"
            )

    await FallingEdge(dut.clk)


@cocotb.test()
async def test_posit_mult(dut):
    """Test Posit16 multiplication via the TinyTapeout register interface"""

    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    await reset(dut)

    for i, op in enumerate(OPERATIONS):
        result = await compute_posit(dut, op["opA"], op["opB"], OPCODE_MULT)
        expected = op["result_mult"]

        dut._log.info(
            f"MULT[{i}]: opA=0x{op['opA']:04X} opB=0x{op['opB']:04X} "
            f"result=0x{result:04X} expected=0x{expected:04X}"
        )

        if result != expected:
            raise AssertionError(
                f"MULT[{i}] mismatch: got 0x{result:04X}, expected 0x{expected:04X}"
            )

    await FallingEdge(dut.clk)
