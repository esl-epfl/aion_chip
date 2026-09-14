# AI-generated standard cells

Flow step 6 publishes here, one directory per cell (`./flow.py 6`). You can
also drop views in by hand. `make pnr` discovers them by file stem and wires
them into LibreLane as `EXTRA_LEFS`, `EXTRA_LIBS`, `EXTRA_GDS`,
`EXTRA_VERILOG_MODELS`, `EXTRA_SPICE_MODELS` and `EXTRA_CDLS`.

A cell has **one Liberty, or one per timing corner**:

- `foo.lib` goes to `EXTRA_LIBS`, which LibreLane reads into all three STA
  corners alike, so slow and fast are timed with that one file's data.
- `foo_typ_1p20V_25C.lib`, `foo_slow_1p08V_125C.lib` and
  `foo_fast_1p32V_m40C.lib` (step 6 with `LAYOUT_CORNERS=all`) are grouped into
  the cell `foo`, and each is appended to its own corner's `CELL_LIBS` entry,
  after the PDK's libraries for that corner.

All three corners or none: a corner with no Liberty for a cell cannot link
it, so `make pnr` refuses a partial set, and a `foo.lib` left beside the set.

Any layout works — discovery is a recursive walk grouped by stem:

```
cells/foo.lef                      flat
cells/foo/foo.lef                  one directory per cell
cells/lef/foo.lef                  one directory per view
```

## Required per cell

| View    | Extensions                    | Used for                          |
| ------- | ----------------------------- | --------------------------------- |
| LEF     | `.lef`                        | placement, routing blockages, pins |
| Liberty | `.lib`                        | STA and the resizer                |
| GDS     | `.gds`, `.gds.gz`, `.gdsii`   | streamout                          |

## Recommended

| View    | Extensions                       | Used for                       |
| ------- | -------------------------------- | ------------------------------ |
| Verilog | `.v`, `.sv`                      | gate-level simulation          |
| SPICE   | `.spice`, `.spi`, `.sp`, `.cir`  | LVS                            |
| CDL     | `.cdl`                           | LVS                            |

## What the LEF must satisfy

`make pnr` refuses to start (unless `LENIENT=1`) if a cell breaks any of these,
because none of them fail until detailed placement otherwise:

- `CLASS CORE ;` — anything else makes OpenROAD treat the cell as a macro
- `SITE CoreSite ;`
- height exactly `3.78` um (one `CoreSite` row)
- width an exact multiple of `0.48` um (the `CoreSite` pitch)
- `PIN VDD` and `PIN VSS` present, so the PDN can strap it
- `Metal2`..`Metal5` clear of the PDN's rail via pads, pins and OBS alike:
  `Metal2`/`Metal4` inside `y = 0.355 .. 3.425` um, `Metal3` inside
  `0.310 .. 3.470`, `Metal5` inside `0.520 .. 3.260`
- every routing metal at least half its spacing from the left and right cell
  edges — 0.09 um on `Metal1`, 0.105 um on `Metal2`..`Metal5` — except the
  `VDD`/`VSS` rails
- every signal pin can be entered by a via — some `ViaN` overlaps one of its
  routing-layer port rectangles with its enclosure, and keeps both of its
  metal shapes clear of every *other* net (the OBS and the other pins):
  0.18 um on `Metal1`, 0.21 um on `Metal2`..`Metal5`
- every signal pin has two ways up — following free `Metal1` and `Metal2` from
  the port, and `Via1` wherever it clears the other nets, a `Via2` fits on at
  least two `Metal3` tracks `y = 0.42k` um

The via rule survives placement and then aborts the run. An unreachable pin
places and globally routes without complaint, then aborts the whole design in
detailed routing with `DRT-0073 No access point` — a hard abort in pin access,
which `LENIENT=1` does not downgrade. `scripts/collect_cells.py` grades it
before PnR starts; step 6 grades the same LEF with the same rule, both in
`make verify` and when it publishes.

The via may hang off the port. That is what TritonRoute does when a port's own
landing is crowded, so a port does **not** have to cross a routing track or be
0.21 um across — both used to be required here, and both rejected cells the
router reaches. Calibrated against OpenROAD's `pin_access` on the mined cells,
the extension cells and deliberate mutations of them: `AION_mux2_0`'s I0/I1/I3
(refused by the old rule, routed clean in PnR) pass; `AION_mux2i_1/I2` and
`AION_mux2i_2/I2` (DRT-0073) fail. The rule is a necessary condition: a pin it
passes can still miss when the only room is a few nanometres wide, because
the router tries only certain positions. Every signal pin of the PDK
`sg13g2_stdcell` library passes.

The two clearance rules fail later still, and silently: the design places and
routes, and then Magic, KLayout and Netgen report it at the end. Wherever a
`TopMetal1` strap crosses a row the PDN drops a via stack onto both rails, with
pads centred on the rail line (0.29 um tall on `Metal2`), and a cell's metal
has to keep that half-height plus the layer's spacing. Seven cells drew `Metal2`
at `y = 0.11` um and shorted 130 nets to VGND with 111 `M2.b` errors besides;
`AION_xnor2_xor2_7` drew `Metal1` 10 nm from its right edge and had 50 `M1.b`
errors against its neighbours — all of them DRC- and LVS-clean on their own.
The same two rules are `metrics.lef_abutment_problems` in step 6 and
`check_abutment` here; every cell of the PDK library passes both.

The two-ways-up rule never fails outright: detailed routing just stops
converging. Moving the `Metal2` bars out of the rail band left `AION_xor2_5`
(I0, I2), `AION_xor2_8` (I0) and `AION_xnor2_xor2_9` (I1) each with an inner
pin boxed in between a neighbour pin's U of `Metal2` and an obstruction bar,
with a `Via2` fitting on one track. Step 7 then sat at ~415 violations, `Metal2`
shorts through the neighbour's bar, for 60+ iterations at every die size; with
the same instances swapped to abstracts that had a second track, the same
placement routed to 0 by iteration 8. The rule is
`metrics.lef_pin_escape_problems` in step 6 and `check_pin_escape` here. Of the
ten mined cells it flags exactly those four pins, and all 283 signal pins of the
PDK library reach eight tracks. `LENIENT=1` lets PnR start past it; detailed
routing still will not converge.

## What step 6 checks before a cell lands here

The rules above are static. Before step 6 copies a cell into this directory it
also asks the router: `make aion-layout-pin-access` places the exported LEF in
an `N` and an `FS` row between two `sg13g2_inv_1` and runs OpenROAD's
`pin_access`, the stage of TritonRoute that aborts with `DRT-0073`. A pin it
cannot reach keeps the cell out ("not published: TritonRoute cannot reach
it"). `make verify` runs the same check inside the drawing loop, so the agent
sees it first.

The published LEFs declare their via cuts. Magic writes none, and without them
TritonRoute dropped an access `Via1` right beside a cell's own on
`AION_xnor2_1/I0` and `AION_xnor2_xor2_2/I0` — `V1.a` in the placed chip. The
exporter now copies every `Via1`..`Via4` cut from the GDS into the pin's `PORT`
or into `OBS`. LEFs published before that change have no cuts until step 6
exports them again.

The part of every signal pin's `Metal2` within 0.565 um of a rail line is
published as `OBS`, not pin: the metal stays where it is drawn, but the router
no longer lands on it. Under each power strap the PDN's rail via pad sits there,
and a wire or `Via2` the router puts on a pin bar at `y = 0.355` comes within
spacing of it. That left 14 violations detailed routing never cleared, all on
xor/xnor pin bars under a VGND strap. With the split, the same placement routed
to 0. LEFs exported before this change still have those bars in the pin, so
re-export the cells before `make pnr`.

## Why the cells are *not* don't-touch

`RSZ_DONT_TOUCH_RX` looks like the way to stop the resizer sizing away the
cells step 6 spends its time drawing, and `implementation/config.json` used to
set it to `^_AION_.*` (the instance prefix `aion_opt`'s rewriter emits —
`_instance_prefix` turns the `AION_` cell prefix into `_AION_`; the regex
matches **instance and net names, not cell masters**).

It cannot be used. OpenROAD's `set_dont_touch` on an instance also forbids
inserting a buffer in front of that instance's *input* pins, so the first
high-fanout net that drives an AI cell aborts `repair_design`:

```
[WARNING ODB-1211] InsertBufferBeforeLoads: Load pin '_AION_129_/I2' is dont_touch.
[ERROR RSZ-3006] Failed to insert buffer before loads for net _1984_
```

46 of the 65 nets in the hardened netlist with fanout above
`MAX_FANOUT_CONSTRAINT` drive an AI cell, so there is nothing to route around.

The protection is not needed anyway: the fused cells are 4-input functions
with no equivalent in sg13g2, so OpenSTA finds no cell to swap them for; they
are neither buffers nor inverters, and `DESIGN_REPAIR_REMOVE_BUFFERS` is
false. After a run, confirm it held:

```sh
grep -c 'AION_' flow/7_pnr/nl/tt_um_aion.nl.v
```
