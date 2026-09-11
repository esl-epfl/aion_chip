<!--
  SPDX-FileCopyrightText:    2026 Filippo Quadri
  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
  Description:               Placement and routing plan for AION_mux2i_2
-->

# AION_mux2i_2 — floorplan

Derived from `AION_mux2i_2.spice`. This is the "decide on paper" step the
`aion-layout` skill puts before drawing: **the structure below is decided, and
the generator implements it.** Do not re-derive it. What is left open is said
so explicitly, and it is only rectangle-level detail.

All coordinates are integers in nanometres, origin at the cell's lower-left.

Read `../AION_mux2i_1/FLOORPLAN.md` first. This cell is that one with the
output pair folded to ng=2; §1, §2 and §5 restate only what the fold changes,
and §7–§9 are the same skeleton with different x.

---

## 0. The target

| | devices | width | area |
|---|---|---|---|
| PDK baseline `sg13g2_mux2_1` + `sg13g2_inv_2` | 12 + 2 | 4800 + 1920 = 6720 nm (14 sites) | 25.40 µm² |
| **this cell** | **8** | **3840 nm (8 sites)** | **14.52 µm² — 43% less** |

The baseline is `mux2_1 + inv_2` and not `mux2_2 + inv_2` because it has to be
what the mapper would build if this cell did not exist: the cheapest PDK pair
whose *output* drive is X2. `AION_mux2i_2.v` argues that at length.

8 sites is not a guess to be revisited — `AION_mux2i_2.provisional.lib` already
tells the mapper `area : 14.5152`. Landing wider invalidates mapping decisions
already made upstream. §6 prices the fallback.

## 1. What the fold changes

Only the output pair. `XN3`/`XP3` go from one finger to two (1480/2240,
sg13g2_inv_2's devices verbatim); the select inverter and both transmission
gates are untouched, so §1 of `AION_mux2i_1`'s floorplan — the crosswise
column pairing that lets two complement-gated transmission gates share four
full-height poly stripes — carries over unchanged.

A folded device is **three nodes and two gates**, and which terminal lands in
the middle is a decision, not a detail:

```
    VSS ─[m]─ O0 ─[m]─ VSS          drain in the middle: one switching node
    O0  ─[m]─ VSS ─[m]─ O0          source in the middle: two of them
```

Take the first. `sg13g2_inv_2` takes it too — its `ad` is one 380 nm node and
its `as` is two 340 nm ones — and the reason is that `O0` is the node that
swings. Drawing it twice doubles the junction capacitance on the output for
nothing. The second form would also put a `VSS` node in the middle, which
cannot then be shared with the select inverter, and §2 shows that sharing is
worth a whole CoreSite.

The cost of taking it is §5's whole problem: `O0` ends up **between the two
fold gates**, and a Metal1 riser cannot pass a node column with gate pin pads
on both sides. That is settled in §5.1, not left open.

## 2. Two diffusion segments, not one

Which devices can share a diffusion node:

```
nmos:  XN3{O0,VSS,VSS} — XN0{Sb,VSS} via VSS          XN1{m,I0} — XN2{m,I1} via m
pmos:  XP3{O0,VDD,VDD} — XP0{Sb,VDD} via VDD          XP1{m,I0} — XP2{m,I1} via m
```

Two components per row with no node in common — the inverters share only a
rail with each other, the two transmission gates share only `m` — so one
unbroken strip per row is impossible and the cell is **two segments**, split by
an `Act.b` diffusion break. Same conclusion as `AION_mux2i_1`, and the break is
still 80 nm cheaper than a dummy isolation gate (590 node-to-node against 510,
versus 510 spent on a gate that computes nothing).

What is new is that the folded inverter's **second `VSS` node is shared with
the select inverter**:

```
    VSS ─[m]─ O0 ─[m]─ VSS ─[I2]─ Sb        4 nodes, 3 gates, one segment
```

`VSS` is internal to that path exactly once (degree 2: one fold finger and the
select inverter) and an endpoint the other time. That is the arrangement that
costs 4 nodes; keeping the fold and the select inverter apart costs 5 and one
more break, which is 590 + 80 = a whole extra site.

## 3. The plan

```
            ┌───────────── segment 1: inverters ─────────────┐    ┌────────── segment 2: transmission gates ─────────┐
   x        350        605        860       1115       1370       1625       1880      2470       2725       2980       3235       3490
   pmos     VDD       (m)         O0        (m)        VDD        (I2)       Sb    ║   I1        (Sb)        m         (I2)        I0
   gate           A=XP3(f1)             B=XP3(f2)            C=XP0                 ║           D=XP1                E=XP2
   nmos     VSS       (m)         O0        (m)        VSS        (I2)       Sb    ║   I0        (Sb)        m         (I2)        I1
                                                                                   ║
                                                        Activ 160…2070             ║ 2280…3680       break = 210 nm
```

Node centres `XN = [350, 860, 1370, 1880, 2470, 2980, 3490]`, gate centres
`XG = [605, 1115, 1625, 2725, 3235]`, pitch 510, break 590.

Columns A and B are the two fingers of `XN3`/`XP3` — one device per row, two
poly stripes. Column C is the select inverter, D and E are the transmission
gates paired crosswise exactly as in `AION_mux2i_1`.

Why this order:

- `Sb` is segment 1's right-hand node, **directly across the break** from
  column D, the gate it drives. One riser plus one short pad-band run, and it
  is the only Metal1 row crossing the cell has (§5.2).
- `m`'s node is segment 2's centre and its two gates are segment 1's first two
  columns; nothing brings those closer, so `m` takes the long run.
- Segment 1's outer node is a rail, so the left cell edge carries no signal
  and needs no riser — which is why the slack in §4 is split evenly here and
  was not in `AION_mux2i_1`.

All fourteen source/drain positions get contacts: every node carries a net that
must reach metal.

## 4. Width

Seven nodes, one break: `5 × 510 + 590 = 3140` nm node-centre span, plus 190 nm
diffusion overhang each end = **3520 nm of Activ**. Rounded up to sites:
`3840 = 8 × 480`. ✔

The 320 nm of slack is split **160 left / 160 right**. Unlike `AION_mux2i_1`
nothing depends on the split: neither outer node carries a signal, so neither
cell edge has to hold a riser. What the split does have to satisfy is
`Act.b` = 210 nm against whatever abuts, and 160 is comfortably over half of
that, so any neighbour works — including a copy of this cell (320) and any PDK
cell (their margins are ≥ 105).

## 5. Routing

### 5.1 What is decided

Two things, and they are decided because §1 forced them.

**`O0` crosses on Metal2, vertically, at x = 860.** Its node is boxed: the pad
band at that x has column A's pin pad ending at 745 and column B's beginning at
975, a 230 nm window for a 160 nm wire with `M1.b` = 180 to hold on both sides.
Metal1 cannot cross there and no reordering moves it, because `O0` is the
middle of a folded device by §1. Metal2 crosses Metal1 freely and the 230 nm
window is Metal1's problem, not Metal2's, so the strap is short, straight and
unobstructed: up the nmos stub, across the pad band, down onto the pmos stub.

Keep the Via1 landings at the **inner** ends of both stubs — top of the nmos
one, bottom of the pmos one — so that `O0`'s pin rectangle (§9, the nmos stub)
keeps its `y = 840` routing track clear of the Metal2 that will export as
`OBS` over it.

**`Sb` gets the cell's one Metal1 riser**, in the break. It is the only outboard
crossing slot there is, and `Sb` is the net that needs it least glamorously and
most cheaply: node at 1880 in *both* rows, gate pad at 2725, all within one
820 nm window of clear pad band (between column C's pad ending at 1765 and
column D's beginning at 2585). One riser plus one horizontal run inside that
window ties all three.

### 5.2 The lane budget

| lane | y | notes |
|---|---|---|
| Metal1 lower channel | 1090 … 1250 | same y as the top of the node stubs — passable only where a node keeps its lower contact row only (stub 620 … 910) |
| Metal1 pad band | 1450 … 1790 | blocked at every gate that carries a pin pad; clear in the 820 nm break window 1765 … 2585 |
| Metal1 upper channel | 1970 … 2130 | clear of the pmos stubs (2310 +), the roomiest Metal1 lane |
| Metal2 | anywhere | crosses Metal1 freely; costs a router blockage and shows up in the export |

Outboard Metal1 crossing slots: **one**, the break window, and §5.1 has already
spent it. The left cell edge is not one — with a 160 nm margin the clear window
between the boundary and column A's pad runs 180 … 285, and 105 nm will not
hold a 160 nm wire; in any case the node there is `VSS`. The right edge is the
same story against column E's pad, 3555 … 3660.

Which leaves, for §5.3: `m`, `I0`, `I1`, and the `I2` gate-to-gate tie, with no
Metal1 crossings left. **Every one of them crosses on Metal2**, and the tech LEF
says how to spend it: Metal1's preferred direction is horizontal and Metal2's
is vertical (`sg13g2_tech.lef`, `DIRECTION` on both). So the shape to draw is a
short vertical Metal2 strap per crossing, on the net's own node column, with the
horizontal transport left on Metal1 in the channels — not a horizontal Metal2
lane, which is off-direction and blocks a vertical routing track for its whole
length. No PDK standard cell has any Metal2 in it at all (`OBS` in
`sg13g2_stdcell.lef` is Metal1-only, `mux2_1` and `mux2_2` included), so every
strap here is a blockage the baseline does not pay: keep them short and on
column. This cell is one site wider than `AION_mux2i_1` but has the same four
hard nets and one fewer slot to route them through, because the fold spent a
column and the pad band that came with it.

### 5.3 How to resolve it — draw, do not derive

Do **not** try to settle the rest analytically. `make verify` builds the GDS and
runs Magic + KLayout DRC and Netgen LVS in about three minutes, and
`make evidence` names every short **by coordinate**. Draw in this order,
verifying between steps:

1. **Structure only** — boundary, wells, implants, rails, taps, the two
   diffusion segments, the five poly stripes with their landing pads, and every
   source/drain contact. No signal routing. Expect LVS to report 8 devices
   (see §10 on the fold) and a pile of disconnected nets, and DRC to be nearly
   clean. That confirms §1–§4 are drawn right, which is the part that is
   decided.
2. **The easy nets** — `VDD`, `VSS`, `O0` (§5.1's Metal2 strap), `Sb` (§5.1's
   break riser). Verify.
3. **The hard four** — `m`, `I0`, `I1`, `I2`, on Metal2 where §5.2 says Metal1
   cannot reach. Verify after each one.
4. **Pins** (§9) last, once the nets they sit on are final.

Keep Metal2 to short straps and one horizontal run per net. If step 3 will not
close after a few real attempts, that is the signal to take §6.

## 6. If 8 sites will not close

Do not silently grow the cell. The priced alternative:

- **9 sites (4320 nm, 16.33 µm²)** — spend the extra 480 nm widening the break
  gap from 210 to 690 nm. The clear pad-band window across it grows from 820 to
  1300 nm, which holds three Metal1 risers instead of one, and that is exactly
  what §5.2 is short of.

Still beats the 14-site baseline by 36%. It needs `provisional.lib` regenerated
and the mapper re-run, so this is the author's call — take it only after §5.3
has been tried for real.

## 7. The vertical skeleton — copy it, vary only x

This is the shipped sg13g2 library's y-stack, so it is known DRC-good, and it
is identical to `AION_mux2i_1`'s. The fold does not touch y.

| what | y (nm) |
|---|---|
| VSS Metal1 rail | −220 … 220 |
| p+ tap Activ / Cont | −150 … 150 / −80 … 80 |
| PSD bottom | −180 … 180 |
| nmos Activ (W = 740 per finger, netlist exact) | 590 … 1330 |
| nmos Cont rows | 670 … 830, 1010 … 1170 |
| Metal1 lower channel | 1090 … 1250 |
| gate pin pads (Metal1) | 1450 … 1790 |
| poly landing pad / its Cont | 1500 … 1800 / 1570 … 1730 |
| Metal1 upper channel | 1970 … 2130 |
| pmos Activ (W = 1120 per finger, netlist exact) | 2075 … 3195 |
| pmos Cont rows | 2360 … 2520, 2700 … 2860 |
| NWell | 1750 … 4170 |
| PSD top | 1760 … 3600 |
| n+ tap Activ / Cont | 3630 … 3930 / 3700 … 3860 |
| VDD Metal1 rail | 3560 … 4000 |
| poly stripe | 410 … 3375 |

Wells and implants overhang the prBoundary on purpose; an abutted neighbour
shares them. Do not trim them.

Every finger is the row's exact diffusion height, the folded pair included — so
`XN3` is two 740 nm fingers, not one 1480 nm device quantised into the row, and
the extracted W matches the netlist with no error. No `ng > 2` anywhere.

## 8. Taps

Missing taps are `LU.a`/`LU.b`, and they are fixed by drawing geometry, not by
moving it. One p+ tap row under VSS, one n+ tap row under VDD, both full width.

```python
TAP_CONT_X = [240 + 480 * k for k in range(8)]   # 240 720 ... 3600
```

**Not** `150 + 430*k`. Rows abut, so two neighbouring cells' tap cuts land in
one band and must be the same rectangles; the 50 nm beat between a 430 pitch
and the 480 CoreSite produced 10322 Magic and 2872 KLayout errors on the first
two AION cells while each was individually clean.

## 9. Pins

Every port needs a Metal1 label on the shape carrying the net, a `Port` with
the right direction, **a `y = n × 420` routing track inside it**, and **≥ 210 nm
across** so a Via1's 290 × 210 landing fits. A port that misses the track
aborts detailed routing with `DRT-0073`, an hour into the flow.

| pin | dir | shape | track |
|---|---|---|---|
| `I2` | INPUT | column C's gate pin pad, 1450…1790 | 1680 ✔ |
| `I0` | INPUT | nmos node stub at x 2470, 620…1250 | 840 ✔ |
| `I1` | INPUT | pmos node stub at x 2470, 2310…2910 | 2520 ✔ |
| `O0` | OUTPUT | nmos node stub at x 860, 620…1250 | 840 ✔ |
| `VDD` | POWER | full-width rail | 3780 ✔ |
| `VSS` | GROUND | full-width rail | 0 ✔ |

All four signal stubs are 260 nm across. ✔

`O0`'s port is the stub, and §5.1's Metal2 strap must land on it above
`y = 1090` — the LEF exports only the labelled rect as `PORT` and everything
else as `OBS`, so a strap parked across the track is a legal-looking pin with
its via landing blocked. That is the one place in this cell where Metal2 and a
pin share an x, and it is the price of the fold.

## 10. What "done" looks like

```
Magic DRC    CLEAN
KLayout DRC  CLEAN
LVS          Circuits match uniquely.  devices 8/8, nets 8/8
geometry     3840 x 3780 nm = 14.5152 um^2, 8 sites, row-legal
gate count   10 GatPoly-over-Activ regions
shorts       none
```

Nets are `I0 I1 I2 O0 Sb m VDD VSS`. `Sb` and `m` are internal and need no
label.

**Ten poly regions, eight devices.** The extractor sees each fold finger as its
own transistor and Netgen merges the two parallel pairs back before it compares
— so an LVS run that reports 10 devices on both sides has merely not merged
yet, while one that reports 10 on the layout and 8 in the netlist is a real
mismatch and usually means the two fingers do not share the middle `O0` node.
If the region count reads 8, a poly stripe is missing or does not cross both
diffusions; if it reads more, something else is folded that should not be.
