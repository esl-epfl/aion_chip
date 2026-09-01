# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-08-19 11:31:46
#  Updated:                   2026-09-01 10:24:14
#  Description:               Testbench for pm32 32x32 multiplier
# ================================================================

import logging
import random

import cocotb
import cocotb.result
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, with_timeout

logging.getLogger("cocotb").setLevel(logging.DEBUG)

# Global timeout for every pm32 test so the regression cannot hang forever.
PM32_TIMEOUT_TIME = 1
PM32_TIMEOUT_UNIT = "ms"


def pm32_test(**kwargs):
    """Decorator wrapper for cocotb.test with a per-test timeout."""
    return cocotb.test(
        timeout_time=PM32_TIMEOUT_TIME, timeout_unit=PM32_TIMEOUT_UNIT, **kwargs
    )


MASK32 = (1 << 32) - 1
MASK64 = (1 << 64) - 1
MAX_SIGNED_MC = (1 << 31) - 1  # largest multiplicand treated identically as unsigned


async def reset(dut):
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    dut.rst.value = 1

    # Reset all the signals of the dut
    dut.start.value = 0
    dut.mc.value = 0
    dut.mp.value = 0

    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 5)


async def multiply(dut, mc, mp):
    """Drive one multiplication and return the unsigned 64-bit product."""
    await FallingEdge(dut.clk)
    dut.mc.value = mc & MASK32
    dut.mp.value = mp & MASK32
    dut.start.value = 1
    await FallingEdge(dut.clk)
    dut.start.value = 0
    try:
        # A 32x32 SPM multiplication needs ~64 clock cycles; allow generous margin.
        await with_timeout(RisingEdge(dut.done), 100, "us")
    except cocotb.result.SimTimeoutError as exc:
        raise AssertionError(
            f"Timeout waiting for done after starting {mc & MASK32:#010x} * {mp & MASK32:#010x}"
        ) from exc
    await FallingEdge(dut.clk)

    return int(dut.p.value) & MASK64


async def run_unsigned_test(dut, mc, mp):
    """Reset the DUT, run one unsigned multiplication and check the result."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    expected = ((mc & MASK32) * (mp & MASK32)) & MASK64
    result = await multiply(dut, mc, mp)
    assert result == expected, (
        f"{mc & MASK32:#010x} * {mp & MASK32:#010x}: "
        f"expected {expected:#018x}, got {result:#018x}"
    )
    return result


@pm32_test()
async def test_pm32_basic(dut):
    """Simple unsigned multiplication."""
    await run_unsigned_test(dut, 3, 5)


@pm32_test()
async def test_pm32_zero(dut):
    """Multiplication by zero."""
    await run_unsigned_test(dut, 0x12345678, 0)


@pm32_test()
async def test_pm32_identity(dut):
    """Multiplication by one."""
    await run_unsigned_test(dut, 0x12345678, 1)


@pm32_test()
async def test_pm32_large_values(dut):
    """Large unsigned values."""
    await run_unsigned_test(dut, 123456789, 987654321)


@pm32_test()
async def test_pm32_max_positive_multiplicand(dut):
    """Maximum multiplicand that remains unsigned in the SPM (0x7fffffff)."""
    await run_unsigned_test(dut, MAX_SIGNED_MC, 0x55555555)


@pm32_test()
async def test_pm32_max_unsigned_multiplier(dut):
    """Maximum unsigned 32-bit multiplier."""
    await run_unsigned_test(dut, 0x12345678, MASK32)


@pm32_test()
async def test_pm32_alternating_bits(dut):
    """Patterns with alternating bits."""
    await run_unsigned_test(dut, 0x2AAAAAAA, 0x55555555)


@pm32_test()
async def test_pm32_powers_of_two_multiplier(dut):
    """Products with powers of two as multiplier."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    for shift in range(32):
        mc = 0xABCDEF01 & MAX_SIGNED_MC
        mp = 1 << shift
        expected = ((mc & MASK32) * (mp & MASK32)) & MASK64
        result = await multiply(dut, mc, mp)
        assert result == expected, (
            f"{mc:#010x} * 2^{shift}: expected {expected:#018x}, got {result:#018x}"
        )


@pm32_test()
async def test_pm32_powers_of_two_multiplicand(dut):
    """Products with powers of two as multiplicand."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    for shift in range(31):
        mc = 1 << shift
        mp = 0xCAFEBABE
        expected = ((mc & MASK32) * (mp & MASK32)) & MASK64
        result = await multiply(dut, mc, mp)
        assert result == expected, (
            f"2^{shift} * {mp:#010x}: expected {expected:#018x}, got {result:#018x}"
        )


@pm32_test()
async def test_pm32_small_values(dut):
    """Exhaustive sweep over small unsigned operands."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    for mc in range(16):
        for mp in range(16):
            expected = (mc * mp) & MASK64
            result = await multiply(dut, mc, mp)
            assert result == expected, (
                f"{mc} * {mp}: expected {expected:#018x}, got {result:#018x}"
            )


@pm32_test()
async def test_pm32_back_to_back(dut):
    """Several consecutive multiplications without reset between them."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    pairs = [(123, 456), (0x76543210, 0xABCDEF01), (1024, 4096)]
    for mc, mp in pairs:
        expected = ((mc & MASK32) * (mp & MASK32)) & MASK64
        result = await multiply(dut, mc, mp)
        assert result == expected, (
            f"{mc & MASK32:#010x} * {mp & MASK32:#010x}: "
            f"expected {expected:#018x}, got {result:#018x}"
        )


@pm32_test()
async def test_pm32_random(dut):
    """Random unsigned 32-bit multiplications."""
    random.seed(0xACE)

    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset(dut)

    for _ in range(20):
        mc = random.randint(0, MAX_SIGNED_MC)
        mp = random.randint(0, MASK32)
        expected = ((mc & MASK32) * (mp & MASK32)) & MASK64
        result = await multiply(dut, mc, mp)
        assert result == expected, (
            f"{mc:#010x} * {mp:#010x}: expected {expected:#018x}, got {result:#018x}"
        )
