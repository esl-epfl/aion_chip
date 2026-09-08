// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Description:               C ABI over Stillwater Universal's posit<>,
//                             so the cocotb testbenches can use the reference
//                             implementation instead of a hand-written model.
//
//  Universal is a header-only C++20 template library with no Python bindings,
//  so something has to bridge the two.  This is the smallest bridge that
//  works: one translation unit, an `extern "C"` surface, and ctypes on the
//  other side -- no pybind11, no build system beyond a single g++ call, and
//  nothing to install into the Python environment.
//
//  posit<nbits, es> is a template, so the configuration is a compile-time
//  parameter and cannot be passed in from Python.  The dispatch table below
//  instantiates the configurations a design might plausibly use and returns
//  UP_UNSUPPORTED for anything else, rather than silently answering with the
//  wrong format.
//
//  Every entry point returns 0 on success and a negative code otherwise; the
//  result travels through an out-parameter.  That keeps NaR, which is a
//  perfectly ordinary posit value, from having to be smuggled through the
//  return channel as a NaN.
//
//  Build:  `make universal`, which is also what the sim targets depend on.
//  By hand, it is one command:
//    g++ -O2 -std=c++20 -shared -fPIC -I<universal>/include/sw
//        src/tb/universal_posit.cpp -o src/tb/libuniversal_posit.so
// ================================================================

#include <universal/number/posit/posit.hpp>

#include <cstdint>
#include <cmath>

#define UP_OK           0
#define UP_UNSUPPORTED (-1)

namespace {

using sw::universal::posit;

//: Every operation, for one concrete posit<nbits, es>.
template <unsigned nbits, unsigned es>
struct Ops {
    using P = posit<nbits, es>;

    static uint64_t mask(uint64_t bits) {
        return nbits >= 64 ? bits : bits & ((uint64_t(1) << nbits) - 1);
    }

    static P from_bits(uint64_t bits) {
        P p;
        p.setbits(mask(bits));
        return p;
    }

    static uint64_t to_bits(const P& p) {
        return mask(static_cast<uint64_t>(p.bits()));
    }

    static int encode(double x, uint64_t* out) {
        P p;
        p = x;
        *out = to_bits(p);
        return UP_OK;
    }

    static int decode(uint64_t bits, double* out) {
        *out = static_cast<double>(from_bits(bits));
        return UP_OK;
    }

    static int add(uint64_t a, uint64_t b, uint64_t* out) {
        *out = to_bits(P(from_bits(a) + from_bits(b)));
        return UP_OK;
    }

    static int sub(uint64_t a, uint64_t b, uint64_t* out) {
        *out = to_bits(P(from_bits(a) - from_bits(b)));
        return UP_OK;
    }

    static int mul(uint64_t a, uint64_t b, uint64_t* out) {
        *out = to_bits(P(from_bits(a) * from_bits(b)));
        return UP_OK;
    }

    static int div(uint64_t a, uint64_t b, uint64_t* out) {
        *out = to_bits(P(from_bits(a) / from_bits(b)));
        return UP_OK;
    }

    static int lt(uint64_t a, uint64_t b, int* out) {
        *out = from_bits(a) < from_bits(b) ? 1 : 0;
        return UP_OK;
    }

    static int eq(uint64_t a, uint64_t b, int* out) {
        *out = from_bits(a) == from_bits(b) ? 1 : 0;
        return UP_OK;
    }

    static int isnar(uint64_t bits, int* out) {
        *out = from_bits(bits).isnar() ? 1 : 0;
        return UP_OK;
    }
};

// Dispatch. `es` is checked against nbits because a posit needs at least the
// sign, one regime bit and the exponent field to exist at all.
#define UP_DISPATCH(CALL)                                            \
    switch (nbits) {                                                 \
    case 8:                                                          \
        switch (es) {                                                \
        case 0: return Ops<8, 0>::CALL;                              \
        case 1: return Ops<8, 1>::CALL;                              \
        case 2: return Ops<8, 2>::CALL;                              \
        case 3: return Ops<8, 3>::CALL;                              \
        }                                                            \
        break;                                                       \
    case 16:                                                         \
        switch (es) {                                                \
        case 0: return Ops<16, 0>::CALL;                             \
        case 1: return Ops<16, 1>::CALL;                             \
        case 2: return Ops<16, 2>::CALL;                             \
        case 3: return Ops<16, 3>::CALL;                             \
        }                                                            \
        break;                                                       \
    case 32:                                                         \
        switch (es) {                                                \
        case 0: return Ops<32, 0>::CALL;                             \
        case 1: return Ops<32, 1>::CALL;                             \
        case 2: return Ops<32, 2>::CALL;                             \
        case 3: return Ops<32, 3>::CALL;                             \
        }                                                            \
        break;                                                       \
    }                                                                \
    return UP_UNSUPPORTED;

}  // namespace

extern "C" {

int up_encode(int nbits, int es, double x, uint64_t* out) {
    UP_DISPATCH(encode(x, out))
}

int up_decode(int nbits, int es, uint64_t bits, double* out) {
    UP_DISPATCH(decode(bits, out))
}

int up_add(int nbits, int es, uint64_t a, uint64_t b, uint64_t* out) {
    UP_DISPATCH(add(a, b, out))
}

int up_sub(int nbits, int es, uint64_t a, uint64_t b, uint64_t* out) {
    UP_DISPATCH(sub(a, b, out))
}

int up_mul(int nbits, int es, uint64_t a, uint64_t b, uint64_t* out) {
    UP_DISPATCH(mul(a, b, out))
}

int up_div(int nbits, int es, uint64_t a, uint64_t b, uint64_t* out) {
    UP_DISPATCH(div(a, b, out))
}

int up_lt(int nbits, int es, uint64_t a, uint64_t b, int* out) {
    UP_DISPATCH(lt(a, b, out))
}

int up_eq(int nbits, int es, uint64_t a, uint64_t b, int* out) {
    UP_DISPATCH(eq(a, b, out))
}

int up_isnar(int nbits, int es, uint64_t bits, int* out) {
    UP_DISPATCH(isnar(bits, out))
}

//: Which Universal the shim was compiled against, so a testbench can say so.
const char* up_version(void) {
#ifdef UNIVERSAL_VERSION_STRING
    return UNIVERSAL_VERSION_STRING;
#else
    return "unknown";
#endif
}

}  // extern "C"
