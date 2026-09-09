<!--
  SPDX-FileCopyrightText:    2026 Filippo Quadri
  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
  Description:               Placement and routing plan for AION_mux2i_1
-->

# AION_mux2i_1 — floorplan

Derived from `AION_mux2i_1.spice`. This is the "decide on paper" step the
`aion-layout` skill puts before drawing: **the structure below is decided, and
the generator implements it.** Do not re-derive it. What is left open is said
so explicitly, and it is only rectangle-level detail.

All coordinates are integers in nanometres, origin at the cell's lower-left.

---

## 0. The target

| | devices | width | area |
|---|---|---|---|
| PDK baseline `sg13g2_mux2_1` + `sg13g2_inv_1` | 12 + 2 | 4800 + 1440 = 6240 nm (13 sites) | 23.59 µm² |
| **this cell** | **8** | **3360 nm (7 sites)** | **12.70 µm² — 46% less** |

7 sites is not a guess to be revisited — `AION_mux2i_1.provisional.lib` already
tells the mapper `area : 12.7008`, and the mapper has been choosing this cell on
that number. Landing wider than 7 sites invalidates mapping decisions already
made upstream. §6 prices the fallbacks if 7 proves impossible.

## 1. Which devices share a column

A standard-cell column is **one poly stripe crossing both rows**, so the nmos
and the pmos in a column must have the *same gate net*. For a transmission-gate
mux that looks impossible — the two halves of a TG are gated by complements.
It is not, and this is the whole reason the cell fits:

```
nmos gates:  XN0=I2   XN1=Sb   XN2=I2   XN3=m     → {I2, I2, Sb, m}
pmos gates:  XP0=I2   XP1=I2   XP2=Sb   XP3=m     → {I2, I2, Sb, m}
```

The multisets are equal, so a column-by-column pairing exists. It pairs the two
TGs **crosswise**:

| column | gate net | nmos | pmos |
|---|---|---|---|
| A | `I2` | XN0 (select inv) | XP0 (select inv) |
| B | `I2` | XN2 (TG1 nmos) | XP1 (TG0 pmos) |
| C | `Sb` | XN1 (TG0 nmos) | XP2 (TG1 pmos) |
| D | `m`  | XN3 (output inv) | XP3 (output inv) |

TG0's nmos shares a column with TG1's pmos, and vice versa. Four full-height
poly stripes, no split poly, no dummy gates.

## 2. Two diffusion segments, not one

Which devices can share a diffusion node:

```
nmos:  XN0{Sb,VSS} — XN3{O0,VSS} via VSS          XN1{m,I0} — XN2{m,I1} via m
pmos:  XP0{Sb,VDD} — XP3{O0,VDD} via VDD          XP1{m,I0} — XP2{m,I1} via m
```

Two components per row, with **no node in common between them** — the two
inverters share only a rail with each other, the two TGs share only `m`. So one
unbroken diffusion strip per row is impossible; the cell is **two segments of
three nodes each**, separated by an `Act.b` diffusion break.

A break costs 190 + 210 + 190 = 590 nm of node-to-node pitch against 510 for a
gate, i.e. **80 nm**. A dummy isolation gate would cost 510. Take the break.

## 3. The plan

```
            ┌─────────── segment 1: inverters ───────────┐    ┌────────── segment 2: transmission gates ─────────┐
   x        415        670        925       1180       1435      2025       2280       2535       2790       3045
   pmos     O0        (m)        VDD        (I2)        Sb   ║   I1        (Sb)        m         (I2)        I0
   gate            D=XP3                 A=XP0              ║           C=XP2                 B=XP1
   nmos     O0        (m)        VSS        (I2)        Sb   ║   I0        (Sb)        m         (I2)        I1
                                                             ║
                                              Activ 225…1625 ║ 1835…3235      break = 210 nm
```

Node centres `XN = [415, 925, 1435, 2025, 2535, 3045]`, gate centres
`XG = [670, 1180, 2280, 2790]`, pitch 510.

Why this order, and not a mirror of it:

- `Sb` (segment 1's right-hand node) sits **directly across the break** from
  column C, the gate it drives. One riser plus one short wire.
- `m`'s node is segment 2's centre and column D is segment 1's second gate;
  nothing brings those closer, so `m` takes the long run (§5).
- `O0` lands on segment 1's outer node, where a Metal1 row crossing still fits.

Every one of the six nodes carries a net that must reach metal, so **all twelve
source/drain positions get contacts** — unlike the worked example, there is no
series-shared node to skip.

## 4. Width

Six nodes, one break: `2×510 + 590 + 2×510 = 2630` nm node-centre span, plus
190 nm diffusion overhang each end = **3010 nm of Activ**. Rounded up to sites:
`3360 = 7 × 480`. ✔

The 350 nm of slack is split **225 left / 125 right**, not evenly. That is
load-bearing: it is what lets `O0`'s row-crossing riser fit at the left edge
(§5), needing 180 nm clear of the boundary and 180 nm clear of column D's pin
pad. At a symmetric 175/175 the window is 120 nm and `M1.a` min width is 160 —
the riser does not fit, and that is the difference between this floorplan
working and not.

`Act.b` when abutted with a copy of itself: 125 + 225 = 350 ≥ 210. ✔

## 5. Routing — constraints, not a solution

**Everything above this line is decided. This section is not.** It is the
constraint set and the lane budget, so that the routing is solved at the
terminal against `make verify`, not on paper. Read §5.3 before you draw
anything.

### 5.1 What has to be connected

Six things, not five. Five nets have geometry in **both** rows and must be tied
across the row gap — `O0`, `Sb`, `m`, `I0`, `I1` — and on top of those, `I2`
must tie **column A's gate to column B's gate**, which is a horizontal run of
1610 nm with column C's pin pad sitting in the middle of it.

`I0` and `I1` are the awkward pair: each is a **diagonal**, nmos on one side of
segment 2 and pmos on the other (§3). That is inherent to a 2-TG mux drawn as
one diffusion pair, not a consequence of this particular ordering.

### 5.2 The lane budget

| lane | y | notes |
|---|---|---|
| Metal1 lower channel | 1090 … 1250 | same y as the top of the node stubs — passable only where a node keeps its lower contact row only (stub 620 … 910), the trick the PDK uses on VSS nodes |
| Metal1 pad band | 1450 … 1790 | blocked at every gate that carries a pin pad |
| Metal1 upper channel | 1970 … 2130 | clear of the pmos stubs (2310 +), the roomiest Metal1 lane |
| Metal2 | anywhere | crosses Metal1 freely; costs a router blockage and shows up in the export |

Outboard Metal1 crossing slots: **two.** The left cell edge (window 180 … 350,
§4) and the 210 nm break. Nowhere else, because a Metal1 riser **cannot pass a
node column with gate pin pads on both sides** — 230 nm of window for a 160 nm
wire, 35 nm against `M1.b`'s 180.

Six connections, two Metal1 crossing slots, and the node at 2535 is boxed in by
pads at 2280 and 2790. **Metal2 is unavoidable.** Widening the cell does not
change this: the trap at 2535 is local, its two neighbours are gates, and 8 or
9 sites still leaves it boxed (§6).

A riser in the break also **cuts all three Metal1 lanes** at that x, so a net
crossing there and a net running past it cannot both be Metal1. That single
fact is what makes the assignment hard, and it is why this section stops here
instead of prescribing rectangles that do not close.

### 5.3 How to resolve it — draw, do not derive

Do **not** try to settle the routing analytically before drawing. The lane
interactions do not fit in a plan; they fit in a report. `make verify` builds
the GDS and runs Magic + KLayout DRC and Netgen LVS in about three minutes, and
`make evidence` names every short **by coordinate** and every missing device.
Three minutes of that beats an hour of reasoning, and a broken GDS on disk is
worth more than a perfect plan that is not.

So draw in this order, verifying between steps:

1. **Structure only** — boundary, wells, implants, rails, taps, the two
   diffusion segments, the four poly stripes with their landing pads, and every
   source/drain contact. No signal routing at all. Verify: expect LVS to report
   8 devices and a pile of disconnected nets, and DRC to be nearly clean. That
   confirms §1–§4 are drawn right, which is the part that is decided.
2. **The easy nets** — `VDD`, `VSS`, `O0` (left-edge riser), `Sb` (break
   riser). Verify.
3. **The hard four** — `I2`, `m`, `I0`, `I1`, using Metal2 where the budget
   above says Metal1 cannot reach. Verify after each one; the cross-net overlap
   table tells you immediately if a lane choice shorted something.
4. **Pins** (§9) last, once the nets they sit on are final.

Keep Metal2 to short straps and one horizontal run per net. If step 3 will not
close after a few real attempts, that is the signal to take §6, not to keep
reasoning.

## 6. If 7 sites will not close

Do not silently grow the cell. These are the priced alternatives:

- **8 sites (3840 nm, 14.52 µm²)** — spend the extra 480 nm widening the break
  to 690 nm. The break's pad-band window grows to ~940 nm and holds three
  Metal1 risers instead of one, which is what the crossing budget is short of.
- **9 sites (4320 nm, 16.33 µm²)** — also widen the right margin to ~605 nm so
  a riser fits outboard of node 3045 as well.

Neither frees the node at 2535, so `m` stays on Metal2 in every case; what the
width buys is Metal1 crossings for `I0`, `I1` and the `I2` tie.

Both still beat the 13-site baseline (by 38% and 31%). Both need
`provisional.lib` regenerated and the mapper re-run, so this is the author's
call — take it only after §5.3 has been tried for real.

## 7. The vertical skeleton — copy it, vary only x

This is the shipped sg13g2 library's y-stack, so it is known DRC-good.

| what | y (nm) |
|---|---|
| VSS Metal1 rail | −220 … 220 |
| p+ tap Activ / Cont | −150 … 150 / −80 … 80 |
| PSD bottom | −180 … 180 |
| nmos Activ (W = 740, netlist exact) | 590 … 1330 |
| nmos Cont rows | 670 … 830, 1010 … 1170 |
| Metal1 lower channel | 1090 … 1250 |
| gate pin pads (Metal1) | 1450 … 1790 |
| poly landing pad / its Cont | 1500 … 1800 / 1570 … 1730 |
| Metal1 upper channel | 1970 … 2130 |
| pmos Activ (W = 1120, netlist exact) | 2075 … 3195 |
| pmos Cont rows | 2360 … 2520, 2700 … 2860 |
| NWell | 1750 … 4170 |
| PSD top | 1760 … 3600 |
| n+ tap Activ / Cont | 3630 … 3930 / 3700 … 3860 |
| VDD Metal1 rail | 3560 … 4000 |
| poly stripe | 410 … 3375 |

Wells and implants overhang the prBoundary on purpose; an abutted neighbour
shares them. Do not trim them.

Both device widths are the row's exact diffusion heights, so every device is a
**single unbroken finger** and the extracted W matches the netlist with no
quantisation error. No folding, no `ng > 1`.

## 8. Taps

Missing taps are `LU.a`/`LU.b`, and they are fixed by drawing geometry, not by
moving it. One p+ tap row under VSS, one n+ tap row under VDD, both full width.

```python
TAP_CONT_X = [240 + 480 * k for k in range(7)]   # 240 720 1200 1680 2160 2640 3120
```

**Not** `150 + 430*k`. Rows abut, so two neighbouring cells' tap cuts land in
one band and must be the same rectangles; the 50 nm beat between a 430 pitch
and the 480 CoreSite produced 10322 Magic and 2872 KLayout errors on the first
two AION cells while each was individually clean. Every one of the 2497 rail
tap contacts in the PDK library sits at `240 + 480k`.

## 9. Pins

Every port needs a Metal1 label on the shape carrying the net, a `Port` with
the right direction, **a `y = n × 420` routing track inside it**, and **≥ 210 nm
across** so a Via1's 290 × 210 landing fits. A port that misses the track
aborts detailed routing with `DRT-0073`, an hour into the flow.

| pin | dir | shape | track |
|---|---|---|---|
| `I2` | INPUT | column A's gate pin pad, 1450…1790 | 1680 ✔ |
| `I0` | INPUT | nmos node stub at x 2025, 620…1250 | 840 ✔ |
| `I1` | INPUT | pmos node stub at x 2025, 2310…2910 | 2520 ✔ |
| `O0` | OUTPUT | nmos node stub at x 415, 620…1250 | 840 ✔ |
| `VDD` | POWER | full-width rail | 3780 ✔ |
| `VSS` | GROUND | full-width rail | 0 ✔ |

All four signal stubs are 260 nm across. ✔

`O0`'s port is the **stub**, not the riser — the riser is 160 nm wide and can
never carry a via. And keep Metal2 out of the four signal-pin rectangles: the
LEF exports only the labelled rect as `PORT` and everything else as `OBS`, so a
strap parked over a pin is a legal-looking pin with its via landing blocked.
The Metal2 in this cell all lives in segment 2 above/below the pad band, clear
of `I0` and `I1`'s stubs — keep it that way.

## 10. What "done" looks like

```
Magic DRC    CLEAN
KLayout DRC  CLEAN
LVS          Circuits match uniquely.  devices 8/8, nets 8/8
geometry     3360 x 3780 nm = 12.7008 um^2, 7 sites, row-legal
gate count   8 GatPoly-over-Activ regions
shorts       none
```

Nets are `I0 I1 I2 O0 Sb m VDD VSS`. `Sb` and `m` are internal and need no
label. If the device count reads 6, a poly stripe is missing or does not cross
both diffusions; if it reads more, something is folded that should not be.
