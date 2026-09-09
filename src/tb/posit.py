#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Posit reference model, backed by Stillwater
#                             Universal (github.com/stillwater-sc/universal).
#
#  This used to be a hand-written port of posit.js.  It encoded correctly but
#  decoded four bit patterns wrong -- the ones whose exponent field runs off
#  the end of the word, where the missing exponent bits have to be read as
#  zeros and it read them as absent instead.  0x0003 came back as 2^-51
#  rather than 2^-50.  A testbench whose reference model is wrong at the ends
#  of the range is a testbench that cannot fail there, so the model is now the
#  reference implementation rather than a second opinion.
#
#  Universal is header-only C++20 with no Python bindings, so the arithmetic
#  happens in `universal_posit.cpp` -- a small `extern "C"` surface -- and
#  this module is ctypes over it.  ctypes is in the standard library, so the
#  testbench gains no Python dependency.
#
#  Run `make universal` to fetch the headers and build the library.  If it is
#  missing, every call here raises: falling back to a Python approximation
#  would mean a run that reports "checked against Universal" while checking
#  against something else.
#
#  Arithmetic is done IN the posit type, not in double.  At Posit<16,2> the
#  two agreed -- a double has enough mantissa to make one rounding step exact
#  -- but that was a property of that size, not a rule, and the design is now
#  Posit<32,2>, whose 27-bit fraction leaves a double no room to spare on the
#  wide products.  Decode-operate-re-encode is not a valid reference here.
# ================================================================

from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
from typing import Optional

DEFAULT_NBITS = 32
DEFAULT_ES = 2

#: Where `make universal` puts the shim, and the escape hatch for a build
#: somewhere else.
_LIB_ENV = "AION_UNIVERSAL_LIB"
_LIB_NAME = "libuniversal_posit.so"

_BUILD_HINT = (
    "the Universal posit shim is not built.\n"
    "  run `make universal` from the project root (it fetches the headers "
    "and compiles src/tb/universal_posit.cpp),\n"
    f"  or point {_LIB_ENV} at an existing {_LIB_NAME}."
)


class UniversalUnavailable(RuntimeError):
    """The shim is missing, so there is no reference model to check against."""


class UnsupportedFormat(ValueError):
    """`universal_posit.cpp` has no instantiation for this (nbits, es)."""


_lib: Optional[ctypes.CDLL] = None


def _load() -> ctypes.CDLL:
    """Open the shim once, and say how to build it when it is not there."""
    global _lib
    if _lib is not None:
        return _lib

    candidates = []
    override = os.environ.get(_LIB_ENV)
    if override:
        candidates.append(Path(override))
    candidates.append(Path(__file__).resolve().parent / _LIB_NAME)

    for path in candidates:
        if path.is_file():
            try:
                lib = ctypes.CDLL(str(path))
            except OSError as exc:
                raise UniversalUnavailable(f"{path}: {exc}") from exc
            break
    else:
        looked = ", ".join(str(c) for c in candidates)
        raise UniversalUnavailable(f"{_BUILD_HINT}\n  looked in: {looked}")

    u64, u64p = ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64)
    intp, dblp = ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_double)
    ci, cd = ctypes.c_int, ctypes.c_double
    for name, argtypes in (
            ("up_encode", [ci, ci, cd, u64p]),
            ("up_decode", [ci, ci, u64, dblp]),
            ("up_add", [ci, ci, u64, u64, u64p]),
            ("up_sub", [ci, ci, u64, u64, u64p]),
            ("up_mul", [ci, ci, u64, u64, u64p]),
            ("up_div", [ci, ci, u64, u64, u64p]),
            ("up_lt", [ci, ci, u64, u64, intp]),
            ("up_eq", [ci, ci, u64, u64, intp]),
            ("up_isnar", [ci, ci, u64, intp]),
    ):
        handle = getattr(lib, name)
        handle.argtypes = argtypes
        handle.restype = ci
    lib.up_version.argtypes = []
    lib.up_version.restype = ctypes.c_char_p

    _lib = lib
    return _lib


def universal_version() -> str:
    """The Universal release the shim was compiled against."""
    return _load().up_version().decode() or "unknown"


def _check(code: int, nbits: int, es: int) -> None:
    if code != 0:
        raise UnsupportedFormat(
            f"Posit<{nbits},{es}> is not instantiated in universal_posit.cpp; "
            "add it to the dispatch table there and rebuild")


# =====================================================================
# Raw-bits API. This is the one new code should use: a posit is its bits,
# and going through a Python float to do posit arithmetic is exactly the
# round-trip this module exists to avoid.
# =====================================================================
def encode(x: float, nbits: int = DEFAULT_NBITS, es: int = DEFAULT_ES) -> int:
    """The Posit<nbits,es> encoding of `x`, as an integer."""
    lib = _load()
    out = ctypes.c_uint64()
    _check(lib.up_encode(nbits, es, float(x), ctypes.byref(out)), nbits, es)
    return int(out.value)


def decode(bits: int, nbits: int = DEFAULT_NBITS, es: int = DEFAULT_ES) -> float:
    """The value of a Posit<nbits,es> bit pattern. NaR decodes to nan."""
    lib = _load()
    out = ctypes.c_double()
    _check(lib.up_decode(nbits, es, int(bits), ctypes.byref(out)), nbits, es)
    return float(out.value)


def _binary(name: str):
    def op(a: int, b: int, nbits: int = DEFAULT_NBITS,
           es: int = DEFAULT_ES) -> int:
        lib = _load()
        out = ctypes.c_uint64()
        _check(getattr(lib, name)(nbits, es, int(a), int(b), ctypes.byref(out)),
               nbits, es)
        return int(out.value)
    return op


#: Posit arithmetic on bit patterns, rounded by Universal, not by Python.
add = _binary("up_add")
sub = _binary("up_sub")
mul = _binary("up_mul")
div = _binary("up_div")


def _predicate(name: str):
    def op(a: int, b: int, nbits: int = DEFAULT_NBITS,
           es: int = DEFAULT_ES) -> bool:
        lib = _load()
        out = ctypes.c_int()
        _check(getattr(lib, name)(nbits, es, int(a), int(b), ctypes.byref(out)),
               nbits, es)
        return bool(out.value)
    return op


lt = _predicate("up_lt")
eq = _predicate("up_eq")


def isnar(bits: int, nbits: int = DEFAULT_NBITS, es: int = DEFAULT_ES) -> bool:
    """Is this the Not-a-Real encoding? (0x80000000 for a 32-bit posit.)"""
    lib = _load()
    out = ctypes.c_int()
    _check(lib.up_isnar(nbits, es, int(bits), ctypes.byref(out)), nbits, es)
    return bool(out.value)


# =====================================================================
# Bit-string API, kept so existing testbenches read the same.
# =====================================================================
def float_to_posit(x: float, n_bits: int = DEFAULT_NBITS,
                   es: int = DEFAULT_ES) -> str:
    """Convert a Python float to a Posit<n_bits, es> bit-string."""
    return format(encode(x, n_bits, es), f"0{n_bits}b")


def posit_to_float(bits: str, n_bits: Optional[int] = None,
                   es: Optional[int] = None) -> float:
    """Decode a Posit bit-string back to a Python float."""
    if n_bits is None:
        n_bits = len(bits)
    if es is None:
        es = DEFAULT_ES
    return decode(int(bits, 2), n_bits, es)


def convert(x: float, n_bits: int = DEFAULT_NBITS, es: int = DEFAULT_ES) -> dict:
    """Convert a number to Posit and return details."""
    raw = encode(x, n_bits, es)
    value = decode(raw, n_bits, es)
    if x == 0.0 or not math.isfinite(value):
        rel_err = 0.0 if x == value else math.inf
    else:
        rel_err = abs((x - value) / x)
    return {
        "format": f"Posit<{n_bits},{es}>",
        "input": x,
        "bits": format(raw, f"0{n_bits}b"),
        "hex": f"0x{raw:0{(n_bits + 3) // 4}X}",
        "value": value,
        "relative_error": rel_err,
        "decimals_of_accuracy": -math.log10(rel_err) if rel_err > 0 else math.inf,
    }


if __name__ == "__main__":
    import sys

    def usage() -> None:
        print("Usage: python posit.py <decimal> [n_bits] [es]")
        print("Example: python posit.py 3.14159265358979 32 2")
        sys.exit(1)

    if len(sys.argv) < 2:
        usage()

    try:
        value_in = float(sys.argv[1])
        bits_in = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_NBITS
        es_in = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_ES
    except ValueError:
        usage()

    try:
        result = convert(value_in, bits_in, es_in)
    except (UniversalUnavailable, UnsupportedFormat) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"{result['format']}: {result['bits']} ({result['hex']})   "
          f"[Universal {universal_version()}]")
    print(f"Decoded value: {result['value']}")
    print(f"Relative error: {result['relative_error']:.6e}")
    print(f"Decimals of accuracy: {result['decimals_of_accuracy']:.2f}")
