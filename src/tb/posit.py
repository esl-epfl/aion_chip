#!/usr/bin/env python3
"""
Decimal to Posit converter.

Based on the algorithm in posit.js by Siew Hoon Leong (Cerlane).
Supports arbitrary (n_bits, es) but defaults to Posit<16,2>.
"""

from __future__ import annotations

import math
import struct


def _posit_max(n_bits: int, es: int) -> float:
    """Maximum finite value representable by Posit<n_bits, es>."""
    useed = 2 ** (2 ** es)
    # For n_bits N, max regime run is N-2 ones after the initial 01,
    # so k = N-2, giving useed**(N-2).
    return float(useed ** (n_bits - 2))


def _float_to_binary(x: float) -> str:
    """Return the binary fraction representation of a float in [1, 2)."""
    # Use the IEEE-754 double's mantissa for an exact-ish binary expansion.
    # x is assumed positive and >= 1.
    packed = struct.pack("!d", x)
    bits = int.from_bytes(packed, "big")
    exponent = ((bits >> 52) & 0x7FF) - 1023
    mantissa = bits & 0xFFFFFFFFFFFFF
    if exponent == -1023:  # subnormal
        exponent += 1
    else:
        mantissa |= 1 << 52

    # mantissa has implicit leading 1, so value = (mantissa / 2^52) * 2^exponent
    # We want binary string of x. Since x in [1,2), exponent should be 0 after
    # normalising, but we keep it general.
    # Build integer representation with enough bits.
    value_int = mantissa << max(0, -exponent)
    shift = 52 + max(0, exponent)
    # Binary is value_int / 2^shift.  Integer part is value_int >> shift.
    int_part = value_int >> shift
    frac_int = value_int & ((1 << shift) - 1)

    s = bin(int_part)[2:]
    if frac_int:
        s += "."
        # Emit up to enough bits; stop when we hit a repeating zero tail.
        seen = frac_int
        while seen and len(s) < 2000:
            seen <<= 1
            if seen >= (1 << shift):
                s += "1"
                seen -= 1 << shift
            else:
                s += "0"
    return s


def _round_to_nearest_even(posit: str, n_bits: int, es: int) -> tuple[str, float]:
    """Round an overlong posit bit-string back to n_bits."""
    assert len(posit) > n_bits

    def value_of(bits: str) -> float:
        return posit_to_float(bits, n_bits, es)

    round_up = False
    if posit[n_bits] == "1":
        # Look for any set bit beyond the guard bit
        if any(b == "1" for b in posit[n_bits + 1 :]):
            round_up = True
        else:
            # tie: round to nearest even (LSB == 1 -> round up)
            if posit[n_bits - 1] == "1":
                round_up = True

    truncated = posit[:n_bits]
    value = value_of(truncated)

    if round_up:
        incremented = bin(int(truncated, 2) + 1)[2:].zfill(n_bits)
        new_value = value_of(incremented)
        if new_value != math.inf:
            return incremented, new_value
        # overflow guard: keep truncated value
        return truncated, value

    # underflow guard: if rounding down produced zero, use next posit
    if value == 0.0:
        incremented = bin(int(truncated, 2) + 1)[2:].zfill(n_bits)
        return incremented, value_of(incremented)

    return truncated, value


def float_to_posit(x: float, n_bits: int = 16, es: int = 2) -> str:
    """Convert a Python float to a Posit<n_bits, es> bit-string."""
    if n_bits < 2:
        raise ValueError("n_bits must be at least 2")
    if es < 0:
        raise ValueError("es must be non-negative")

    useed = 2 ** (2 ** es)
    p_max = _posit_max(n_bits, es)
    abs_x = abs(x)
    sign = -1 if x < 0 else 1
    posit = ""

    # Zero
    if x == 0.0:
        return "0" * n_bits

    # Overflow -> maxpos / minpos
    if abs_x > p_max:
        if sign < 0:
            # minpos: 1 followed by zeros then 1
            return "1" + "0" * (n_bits - 2) + "1"
        else:
            # maxpos: 0 followed by all ones
            return "0" + "1" * (n_bits - 1)

    # Regime encoding
    temp = abs_x
    if abs_x < 1:
        # k < 0: regime bits are 0...01
        posit += "0"
        while True:
            temp2 = temp * useed
            if temp2 >= useed:
                break
            temp = temp2
            posit += "0"
        posit += "1"
    elif 1 <= abs_x < useed:
        # k = 0: regime is 010
        posit = "010"
    else:
        # k > 0: regime is 011...10
        posit = "01"
        while temp >= useed:
            temp = temp / useed
            posit += "1"
        posit += "0"

    # Exponent and fraction
    # temp is now in [1, useed).  Reduce to [1, 2) and record exponent bits.
    j = 0
    while temp >= 2.0:
        temp = temp / 2.0
        j += 1

    if es > 0:
        e_binary = bin(j)[2:].zfill(es)
        posit += e_binary

    frac_binary = _float_to_binary(temp)
    # _float_to_binary returns e.g. "1.10101"; drop the leading "1."
    posit += frac_binary[2:]

    # Pad or round
    if len(posit) < n_bits:
        posit = posit.ljust(n_bits, "0")
    elif len(posit) > n_bits:
        posit, _ = _round_to_nearest_even(posit, n_bits, es)

    # Apply sign (two's complement for negative)
    if sign < 0:
        int_val = int(posit, 2)
        neg = (-int_val) & ((1 << n_bits) - 1)
        posit = bin(neg)[2:].zfill(n_bits)

    return posit


def posit_to_float(bits: str, n_bits: int | None = None, es: int | None = None) -> float:
    """Decode a Posit bit-string back to a Python float."""
    if n_bits is None:
        n_bits = len(bits)
    if es is None:
        es = 2

    if len(bits) != n_bits:
        bits = bits.zfill(n_bits)

    # Infinity
    if bits == "1" + "0" * (n_bits - 1):
        return math.inf

    int_val = int(bits, 2)
    if int_val == 0:
        return 0.0

    sign = -1 if bits[0] == "1" else 1
    if sign < 0:
        int_val = (-int_val) & ((1 << n_bits) - 1)
        bits = bin(int_val)[2:].zfill(n_bits)

    regime_sign = bits[1]
    runlength = 1
    is_regime = True
    expo_bitvalue = 0
    fraction_bitvalue = 0
    fractionlength = 0

    for j in range(2, n_bits):
        if is_regime:
            if bits[j] == regime_sign:
                runlength += 1
            else:
                is_regime = False
                expo_start = runlength + 2
                expo_end = min(expo_start + es, n_bits)
                if es > 0 and expo_start < n_bits:
                    expo_bitvalue = int(bits[expo_start:expo_end], 2)
                expo_length_max = expo_start + es
                if expo_length_max < n_bits:
                    fractionlength = n_bits - expo_length_max
                    fraction_bitvalue = int(bits[expo_length_max:], 2)
                break

    if regime_sign == "0":
        k = -runlength
    else:
        k = runlength - 1

    useed = 2 ** (2 ** es)
    regime = useed ** k
    exponent = 2 ** expo_bitvalue
    if fractionlength == 0:
        fraction = 1.0
    else:
        fraction = 1.0 + fraction_bitvalue / (2 ** fractionlength)

    return sign * regime * exponent * fraction


def convert(x: float, n_bits: int = 16, es: int = 2) -> dict:
    """Convert a number to Posit and return details."""
    bits = float_to_posit(x, n_bits, es)
    value = posit_to_float(bits, n_bits, es)
    if x == 0.0:
        rel_err = 0.0
    else:
        rel_err = abs((x - value) / x)
    return {
        "format": f"Posit<{n_bits},{es}>",
        "input": x,
        "bits": bits,
        "hex": f"0x{int(bits, 2):0{(n_bits + 3) // 4}X}",
        "value": value,
        "relative_error": rel_err,
        "decimals_of_accuracy": -math.log10(rel_err) if rel_err > 0 else math.inf,
    }


if __name__ == "__main__":
    import sys

    def usage() -> None:
        print("Usage: python posit.py <decimal> [n_bits] [es]")
        print("Example: python posit.py 3.14159265358979 16 2")
        sys.exit(1)

    if len(sys.argv) < 2:
        usage()

    try:
        x = float(sys.argv[1])
        n_bits = int(sys.argv[2]) if len(sys.argv) > 2 else 16
        es = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    except ValueError:
        usage()

    result = convert(x, n_bits, es)
    print(f"{result['format']}: {result['bits']} ({result['hex']})")
    print(f"Decoded value: {result['value']}")
    print(f"Relative error: {result['relative_error']:.6e}")
    print(f"Decimals of accuracy: {result['decimals_of_accuracy']:.2f}")
