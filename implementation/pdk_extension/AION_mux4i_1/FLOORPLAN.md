<!--
  SPDX-FileCopyrightText:    2026 Filippo Quadri
  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
  Description:               Placement and routing plan for AION_mux4i_1
-->

# AION_mux4i_1 — floorplan

Derived from `AION_mux4i_1.spice`, a transmission-gate inverting 4:1 mux.
This is the "decide on paper" step the `aion-layout` skill puts before
drawing. **Everything here is decided, and drawn:**
`aion_layout_claude/cells/AION_mux4i_1.py` implements it and passes
`make verify` (Magic and KLayout DRC clean, LVS 18/18 devices and 14/14 nets,
pin access, rail band, side edges, two ways up, TritonRoute).

All coordinates are integers in nanometres, origin at the cell's lower-left.

---

## 0. The target

| | devices | width | area |
|---|---|---|---|
| PDK baseline `sg13g2_mux4_1` + `sg13g2_inv_1` | 26 + 2 | 10080 + 1440 = 11520 nm (24 sites) | 43.55 µm² |
| **this cell** | **18** | **8160 nm (17 sites)** | **30.84 µm² — 29% less** |

`AION_mux4i_1.provisional.lib` tells the mapper `area : 30.8448`.

## 1. Why this column order

The first plan put the columns in the order `OUT+INV(I5) | TC | TA | TB |
INV(I4)`, at 16 sites, because an exhaustive search found it has the fewest
nets across any vertical line (five). The drawing agent looped on it for an
hour without closing it. A negotiated-congestion router run on the same
structure did no better: nothing at 16–20 sites, and one seed in three at 22
and at 24 once every diffusion break is widened alike. A small cut is not
enough.

What separates the orders that route is where the I4 inverter sits. Read the
gate pads left to right:

- first plan, TA | TB | INV(I4): `I4  I4b  I4  I4b  I4` — the two nets
  alternate, so they must cross each other somewhere, on top of the four
  data diagonals that already compete for the break windows;
- this plan, TB | INV(I4) | TA: `I4b  I4  I4  I4  I4b` — the three `I4` pads
  are consecutive and `I4b` wraps around them, so neither crosses the other.

```
TB | INV(I4) | TA | TC | INV(I5)+OUT
```

The I5 inverter sits beside TC, the pair it steers, and the output inverter
at the right edge, where its riser needs no break window.

Router results on five-strip orders (two seeds each; four at 16 sites for
the orders that closed at 17; 400 iterations; a count is seeds that reached
zero conflicts):

| order | 16 sites | 17 | 18 | 19 |
|---|---|---|---|---|
| `OUT+INV(I5) \| TC \| TA \| TB \| INV(I4)` (the first plan) | 0 | 0 | 0 | 0 |
| INV(I4) between TC and TA, or at the far end | — | 0 | 0 | 0 |
| INV(I4) between TA and TB, INV(I5)+OUT at the left (two orientations) | 0 | 1, 1 | 1, 1 | 0, 2 |
| **INV(I4) between TA and TB, INV(I5)+OUT at the right** | 0 (best 12 conflicts) | **2** | 0 | 2 |

17 sites is the smallest width anything closed at, and this order closed there
with both seeds. The drawn cell is seed 1.

## 2. Which devices share a column

A column is one poly stripe crossing both rows, so its nmos and pmos have the
same gate net. The transmission gates pair **crosswise**, as in `AION_mux2i_1`:

| col | x | gate | nmos | pmos | block |
|---|---|---|---|---|---|
| A | 670 | `I4b` | XN6 `nB–I2` | XP7 `nB–I3` | TB |
| B | 1180 | `I4` | XN7 `nB–I3` | XP6 `nB–I2` | TB |
| C | 2400 | `I4` | XN1 `I4b–VSS` | XP1 `I4b–VDD` | select inverter I4 |
| D | 3620 | `I4` | XN3 `nA–I1` | XP2 `nA–I0` | TA |
| E | 4130 | `I4b` | XN2 `nA–I0` | XP3 `nA–I1` | TA |
| F | 5350 | `I5b` | XN4 `m–nA` | XP5 `m–nB` | TC |
| G | 5860 | `I5` | XN5 `m–nB` | XP4 `m–nA` | TC |
| H | 7080 | `I5` | XN0 `I5b–VSS` | XP0 `I5b–VDD` | select inverter I5 |
| J | 7590 | `m` | XN8 `O0–VSS` | XP8 `O0–VDD` | output inverter |

## 3. The plan

```
   x      415   670   925  1180  1435 ║ 2145  2400  2655 ║ 3365  3620  3875  4130  4385 ║ 5095  5350  5605  5860  6115 ║ 6825  7080  7335  7590  7845
   pmos   I3          nB          I2  ║ I4b         VDD  ║ I0          nA          I1   ║ nB          m           nA   ║ I5b         VDD         O0
   gate         A=I4b       B=I4      ║       C=I4       ║       D=I4        E=I4b      ║       F=I5b       G=I5       ║       H=I5        J=m
   nmos   I2          nB          I3  ║ I4b         VSS  ║ I1          nA          I0   ║ nA          m           nB   ║ I5b         VSS         O0
```

Activ, both rows: `225 … 1625, 1955 … 2845, 3175 … 4575, 4905 … 6305, 6635 … 8035`.

- Node centres: strips start at 415, 2145, 3365, 5095 and 6825, pitch 510
  inside a strip.
- Gate centres `XG = [670, 1180, 2400, 3620, 4130, 5350, 5860, 7080, 7590]`.
- Every node carries a net that reaches metal, so **all 28 source/drain
  positions get contacts**: one row on nmos (670 … 830), two on pmos.

## 4. Width

- Five strips of three, two, three, three and three nodes: four breaks of
  590 + 120 = 710 nm between node centres, and 510 nm pitches inside a strip.
  The breaks are 120 nm wider than the rule so a Metal1 riser fits in each
  window beside the stubs; 0 extra did not route (§1).
- With 190 nm of diffusion past each outer node, Activ runs 225 … 8035:
  7810 nm.
- Seventeen sites is 8160.

The 350 nm of slack is split **225 left / 125 right**:

- **left**: a riser fits at 190 … 350; `I3` uses it.
- **right**: pad J ends at 7730, so a riser fits at 7910 … 8070, exactly 90 nm
  from the boundary; `O0` uses it.

`Act.b` against an abutted copy: 225 + 125 = 350 ≥ 210. ✔

## 5. Routing, as drawn

`ROUTING` in the generator lists every rectangle per net. It was found by the
router on this structure and then graded by `make verify`; three details the
router does not model were fixed by hand and are what to keep if it is ever
re-routed:

- two overlapping `Via1` cuts on `m` merge into one non-square cut (`V1.a`):
  keep one;
- a Metal1 wire in lane L3 longer than 1 µm runs 205 nm from the 440 nm VDD
  rail, and `M1.e` wants 220: lower all but its first 735 nm by 15 nm, with a
  160 nm wide joint so no cross-section drops under `M1.a`;
- `I4b`'s riser beside its L2 wire leaves a 105 nm notch (`M1.b`): fill it
  with `1605 … 2030 × 1545 … 2130`.

### 5.1 Lanes

| lane | y | reaches |
|---|---|---|
| L1 Metal1 | 1110 … 1270 | nmos stubs that rise into it (others stop at 930), gate pads |
| pad band | 1450 … 1790 | blocked at every pad |
| L2 Metal1 | 1970 … 2130 | pmos stubs that drop into it (others start at 2310), gate pads |
| L3 Metal1 | 3090 … 3355 | pmos stubs only; cut by the `VDD` stubs at 2655 and 7335 |
| Metal2 | 355 … 3425 | bars at 1115 … 1315, 1945 … 2145, 2355 … 2555, 2765 … 2965, 3175 … 3375 |

### 5.2 Net by net

| net | terminals | how it is drawn |
|---|---|---|
| `I4` | pads B, C, D | Metal2 bar B–C at 1115 … 1315; Metal1 from pad C through L2 to pad D |
| `I4b` | pads A, E, nodes 2145 | Metal1 only: L2 from pad A to the TB\|INV(I4) window, riser there to L1, L1 to pad E |
| `I5` | pads G, H | Metal2 bar at 1115 … 1315 |
| `I5b` | pad F, nodes 6825 | Metal1 only: L2 from pad F, riser in the TC\|INV(I5) window to the nmos stub |
| `m` | pad J, nodes 5605 | Metal2 riser at 5605, bars at 1945 … 2145 and 2355 … 2555 to pad J |
| `O0` | nodes 7845 — **output** | Metal1 riser at the right edge |
| `nA` | nodes 3875, nmos 5095, pmos 6115 | Metal2 riser at 3875, bar at 1115 … 1315, riser at 5040 up to L3, L3 to 6115 |
| `nB` | nodes 925, pmos 5095, nmos 6115 | Metal2 riser at 925; L3 to 4700 with two Metal2 hops (over the `VDD` stub at 2655, and 3845 … 4845); riser at 4950 down to L1, L1 to 6115 |
| `I0` | pmos 3365, nmos 4385 — **input** | Metal1 only: L2, riser in the TA\|TC window |
| `I1` | nmos 3365, pmos 4385 — **input** | Metal2 riser at 3430, bar at 2765 … 2965 |
| `I2` | nmos 415, pmos 1435 — **input** | Metal2 riser at 415, bar at 2765 … 2965 |
| `I3` | nmos 1435, pmos 415 — **input** | Metal1 only: L1 to the left edge riser |

Metal2 total: seven nets, 21 `Via1`, all inside the 355 … 3425 rail band.

The data inputs `I0`–`I3` land on source/drain, like `AION_mux2i_1`'s.
Characterize with `--driver-cell sg13g2_buf_2`; `make pdk_cell_generation`
passes it by default.

## 6. The vertical skeleton

| what | y (nm) |
|---|---|
| VSS Metal1 rail | −220 … 220 |
| p+ tap Activ / Cont | −150 … 150 / −80 … 80 |
| PSD bottom | −180 … 180 |
| nmos Activ (W = 740, netlist exact) | 590 … 1330 |
| nmos Cont row / Metal1 stub | 670 … 830 / 590 … 930 |
| Metal1 lower channel L1 | 1110 … 1270 |
| gate pin pads (Metal1) | 1450 … 1790 |
| poly landing pad / its Cont | 1500 … 1800 / 1570 … 1730 |
| Metal1 upper channel L2 | 1970 … 2130 |
| pmos Activ (W = 1120, netlist exact) | 2075 … 3195 |
| pmos Cont rows / Metal1 stub | 2360 … 2520, 2700 … 2860 / 2310 … 2910 |
| NWell | 1750 … 4170 |
| PSD top | 1760 … 3600 |
| n+ tap Activ / Cont | 3630 … 3930 / 3700 … 3860 |
| VDD Metal1 rail | 3560 … 4000 |
| poly stripe | 410 … 3375 |

Every device is a single finger at the row's exact diffusion height. The
`VSS`/`VDD` stubs at 2655 and 7335 run from their node stub to the rail.

## 7. Taps

```python
TAP_CONT_X = [240 + 480 * k for k in range(17)]   # 240 720 … 7920
```

## 8. Pins

| pin | dir | shape | track |
|---|---|---|---|
| `I0` | INPUT | nmos stub at 4385, 4250 … 4520 × 590 … 930 | 840 ✔ |
| `I1` | INPUT | nmos stub at 3365, 3230 … 3500 × 590 … 930 | 840 ✔ |
| `I2` | INPUT | nmos stub at 415, 280 … 550 × 590 … 930 | 840 ✔ |
| `I3` | INPUT | nmos stub at 1435, 1300 … 1570 × 590 … 930 | 840 ✔ |
| `I4` | INPUT | pad B, 1040 … 1320 × 1450 … 1790 | 1680 ✔ |
| `I5` | INPUT | pad G, 5720 … 6000 × 1450 … 1790 | 1680 ✔ |
| `O0` | OUTPUT | nmos stub at 7845, 7710 … 7980 × 590 … 930 | 840 ✔ |
| `VDD` | POWER | full-width rail | 3780 ✔ |
| `VSS` | GROUND | full-width rail | 0 ✔ |

## 9. What "done" looks like

```
Magic DRC    CLEAN
KLayout DRC  CLEAN
LVS          Circuits match uniquely.  devices 18/18, nets 14/14
geometry     8160 x 3780 nm = 30.8448 um^2, 17 sites, row-legal
abstract     pin access, rail band, side edges, two ways up, TritonRoute: PASS
```

Nets are `I0 I1 I2 I3 I4 I5 O0 I4b I5b nA nB m VDD VSS`.
