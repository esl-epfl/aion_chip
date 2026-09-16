<!--
  SPDX-FileCopyrightText:    2026 Filippo Quadri
  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
  Description:               Placement and routing plan for AION_maj3i_1
-->

# AION_maj3i_1 — floorplan

Derived from `AION_maj3i_1.spice`. This is the "decide on paper" step the
`aion-layout` skill puts before drawing: **§1–§4 and §7–§9 are decided, and the
generator implements them.** §5 is the routing budget, and it is not a
solution.

All coordinates are integers in nanometres, origin at the cell's lower-left.

---

## 0. The target

| | devices | width | area |
|---|---|---|---|
| PDK baseline `sg13g2_or2_1` + `sg13g2_a22oi_1` | 6 + 8 | 2400 + 2880 = 5280 nm (11 sites) | 19.96 µm² |
| **this cell** | **10** | **3360 nm (7 sites)** | **12.70 µm² — 36% less** |

`AION_maj3i_1.provisional.lib` tells the mapper `area : 12.7008`. §6 prices
the fallback.

## 1. Which devices share a column

A column is one poly stripe crossing both rows, so its nmos and pmos must have
the same gate net. The pull-up is the pull-down's own graph (majority is
self-dual), so the pairing is immediate:

| column | gate | nmos | pmos |
|---|---|---|---|
| A | `I0` | XN0 `O0–n1` | XP0 `O0–p1` |
| B | `I0` | XN3 `O0–n2` | XP3 `O0–p2` |
| C | `I1` | XN4 `n2–VSS` | XP4 `p2–VDD` |
| D | `I2` | XN2 `n1–VSS` | XP2 `p1–VDD` |
| E | `I1` | XN1 `O0–n1` | XP1 `O0–p1` |

Five full-height poly stripes, no split poly, no dummy gate.

## 2. One diffusion strip per row, no break

The order A B C D E is an Euler path of both networks at once:

```
nmos:  n1 —XN0— O0 —XN3— n2 —XN4— VSS —XN2— n1 —XN1— O0
pmos:  p1 —XP0— O0 —XP3— p2 —XP4— VDD —XP2— p1 —XP1— O0
```

An exhaustive search over every column order and orientation (5 columns,
149 orders) finds no break-free order with fewer than 4 nets crossing one
vertical line. This is one of the two orders that reach 4, and the one whose
rows carry the same nets at the same x, so every row tie is vertical.

## 3. The plan

```
   x      340     595     850    1105    1360    1615    1870    2125    2380    2635    2890
   pmos   p1             O0              p2*             VDD             p1              O0
   gate          A=I0            B=I0            C=I1            D=I2            E=I1
   nmos   n1             O0              n2*             VSS             n1              O0

   Activ 150 … 3080 (both rows)                                 * no contact
```

Node centres `XN = [340, 850, 1360, 1870, 2380, 2890]`, gate centres
`XG = [595, 1105, 1615, 2125, 2635]`, pitch 510.

- `n2` and `p2` are series-internal nodes touched by exactly two devices.
  **Draw no contact and no metal on them.**
- `VSS` (1870, nmos) and `VDD` (1870, pmos) drop straight to their rails.
- `n1`, `p1` and `O0` each appear twice per row and need a strap (§5).

## 4. Width

Six nodes, no break: `5 × 510 = 2550` nm node-centre span, plus 190 nm of
diffusion each end = **2930 nm of Activ**. Seven sites is 3360.

The 430 nm of slack is split **150 left / 280 right**, and that is
load-bearing. `O0`'s row tie is an outboard Metal1 riser right of column E,
because the node at 2890 is the only `O0` node with no gate pad beside it:

- pad E ends at 2635 + 140 = 2775;
- the riser starts 180 later, at 2955, and is drawn 210 wide (2955 … 3165)
  so it can carry the pin's via (§9);
- that leaves 3360 − 3165 = 195 nm to the boundary, against the 90 the skill
  requires.

The left edge needs no riser, since `n1` and `p1` never change rows.
`Act.b` against an abutted copy: 150 + 280 = 430 ≥ 210. ✔

## 5. Routing — constraints and one assignment that closes on paper

**Everything above is decided. This section is not.** It lists what must be
connected and one lane assignment that fits on paper. Draw it, run
`make verify`, and let the evidence packet overrule it.

### 5.1 What has to be connected

| net | terminals |
|---|---|
| `n1` | nmos 340, nmos 2380 |
| `p1` | pmos 340, pmos 2380 |
| `O0` | nmos 850, nmos 2890, pmos 850, pmos 2890 — **output** |
| `I0` | pads A (595), B (1105) |
| `I1` | pads C (1615), E (2635) — pad D sits between them |
| `I2` | pad D (2125) |

### 5.2 Lanes

| lane | y | reaches |
|---|---|---|
| L1 Metal1 | 1090 … 1250 | nmos stubs that stop at 930 are passed; stubs that rise into it are tapped; gate pads by a short riser |
| pad band Metal1 | 1450 … 1790 | blocked at every pad |
| L2 Metal1 | 1970 … 2130 | gate pads by a short riser; pmos stubs from 2310 are passed |
| L3 Metal1 | 3090 … 3290 | pmos stubs only |
| Metal2 | 355 … 3425 | crosses Metal1 freely; a Via1 needs 290 × 210 of Metal1 |

A Metal1 riser cannot pass a node column with gate pads on both sides, which
rules out 850 and every other inner node. The only Metal1 row crossing is the
outboard riser at 2955.

### 5.3 An assignment that closes on paper

| net | route |
|---|---|
| `I0` | Metal1 bar in the pad band, pad A to pad B (595 … 1105), pad A's Metal1 grown left to 250 (§9) |
| `n1` | nmos stubs at 340 and 2380 rise into **L1**; L1 from 340 to 2380 |
| `p1` | pmos stubs at 340 and 2380 drop into **L2**; L2 from 340 to 2380 |
| `O0` | Metal1 riser 2955 … 3165 from the nmos stub at 2890 to the pmos stub at 2890. **Metal2 strap** at x = 850 from a Via1 on the nmos stub to a Via1 on the pmos stub. **Metal2 horizontal** at nmos height (y ≈ 650 … 850) from that strap to a Via1 on the riser at 2890 |
| `I1` | a Via1 on pad C and one on pad E, Metal2 up from each to y ≈ 2560 … 2760, one **Metal2 horizontal** there from 1615 to 2635 |
| `I2` | pad D only |

Why this and not the obvious all-Metal1 version: L3 cannot carry `p1`,
because the pmos `VDD` stub at 1870 must cross L3 to reach its rail. That
pushes `p1` onto L2, which pushes `I1` onto Metal2 above the pmos row. `I1`
cannot take the pad band instead, because a Metal2 wire over pad D would take
`I2`'s only via landing.

Checks this assignment was built to pass:

- `I2`'s Via1 pad on pad D (1980 … 2270) is 265 nm from both `I1` Metal2
  verticals (1515 … 1715, 2535 … 2735) and far below the `I1` horizontal.
- `O0`'s Metal2 strap at 850 (750 … 950) runs over the `I0` bar, so `I0`'s
  via goes at the bar's left end. Pad A's Metal1 is grown left to 250, and
  a Via1 centred at 395 keeps its Metal2 pad (250 … 540) 210 clear of the
  strap.
- Every Metal2 shape stays inside 355 … 3425.
- The nmos stubs at 850 (`O0`) and 1870 (`VSS`) stop at 930 so L1 passes over
  them. The pmos stubs at 850 (`O0`) and 1870 (`VDD`) start at 2310 so L2
  passes under them. The `O0` stubs still hold a 210 × 290 Via1 landing
  (stub 260 wide, 310 tall).

### 5.4 Draw in this order, verifying between steps

1. Structure: boundary, wells, implants, rails, taps, both Activ strips, the
   five poly stripes with landing pads, and contacts on every node except
   1360. Expect LVS at 10 devices with disconnected nets.
2. `VDD`, `VSS`, `I0`, `n1`, `p1`.
3. `I1`, then `O0`.
4. Pins (§9) last.

## 6. If 7 sites will not close

- **8 sites (3840 nm, 14.52 µm²)**: put the extra 480 nm on the left. The
  diffusion then starts 630 nm in, which leaves room for a second outboard
  riser left of column A; `n1`/`p1` still need it only as a lane, so it is
  free for `I1` or `O0`. It still beats the 11-site baseline by 27%.

This needs `provisional.lib` regenerated, so it is the author's call. Take it
only after §5 has been tried against `make verify`.

## 7. The vertical skeleton — copy it, vary only x

| what | y (nm) |
|---|---|
| VSS Metal1 rail | −220 … 220 |
| p+ tap Activ / Cont | −150 … 150 / −80 … 80 |
| PSD bottom | −180 … 180 |
| nmos Activ (W = 740, netlist exact) | 590 … 1330 |
| nmos Cont rows | 670 … 830, 1010 … 1170 (upper row only where no lane passes) |
| Metal1 lower channel L1 | 1090 … 1250 |
| gate pin pads (Metal1) | 1450 … 1790 |
| poly landing pad / its Cont | 1500 … 1800 / 1570 … 1730 |
| Metal1 upper channel L2 | 1970 … 2130 |
| pmos Activ (W = 1120, netlist exact) | 2075 … 3195 |
| pmos Cont rows | 2360 … 2520, 2700 … 2860 |
| NWell | 1750 … 4170 |
| PSD top | 1760 … 3600 |
| n+ tap Activ / Cont | 3630 … 3930 / 3700 … 3860 |
| VDD Metal1 rail | 3560 … 4000 |
| poly stripe | 410 … 3375 |

Every device is a single finger at the row's exact diffusion height, so the
extracted W matches the netlist. Wells and implants overhang the prBoundary on
purpose.

## 8. Taps

```python
TAP_CONT_X = [240 + 480 * k for k in range(7)]   # 240 720 1200 1680 2160 2640 3120
```

One p+ tap row under VSS and one n+ tap row under VDD, both full width, with
their contacts exactly on that grid so abutted rows share the rectangles.

## 9. Pins

Every port needs a Metal1 label on the shape carrying the net, a `y = n × 420`
track inside it, ≥ 210 nm of width for a Via1 landing, and a Via1 position
with its Metal2 pad 210 nm clear of other nets' Metal2. Every signal pin also
needs Via2 access on at least two Metal3 tracks.

| pin | dir | shape | track |
|---|---|---|---|
| `I0` | INPUT | pad-band bar 250 … 1245 × 1450 … 1790 (pad A grown left) | 1680 ✔ |
| `I1` | INPUT | pad C, 1475 … 1755 × 1450 … 1790 (its own Via1 is the way up) | 1680 ✔ |
| `I2` | INPUT | pad D, 1985 … 2265 × 1450 … 1790 | 1680 ✔ |
| `O0` | OUTPUT | riser 2955 … 3165 × 620 … 2910 | 840, 1260, 1680, 2100, 2520 ✔ |
| `VDD` | POWER | full-width rail | 3780 ✔ |
| `VSS` | GROUND | full-width rail | 0 ✔ |

All three input pads sit in one pad band with Metal2 overhead in places, so
these are the pins most likely to fail "two ways up". If `make verify` names
one, move the Metal2 that boxes it, not the pad.

## 10. What "done" looks like

```
Magic DRC    CLEAN
KLayout DRC  CLEAN
LVS          Circuits match uniquely.  devices 10/10
geometry     3360 x 3780 nm = 12.7008 um^2, 7 sites, row-legal
abstract     pin access, rail band, side edges, two ways up: PASS
```

Nets are `I0 I1 I2 O0 n1 n2 p1 p2 VDD VSS`. `n2` and `p2` have no contact; if
LVS merges them with something, a contact was drawn on 1360.
