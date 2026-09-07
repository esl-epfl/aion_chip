# AI-generated standard cells

Flow step 6 publishes here, one directory per cell (`./flow.py 6`). You can
also drop views in by hand. `make pnr` discovers them by file stem and wires
them into LibreLane as `EXTRA_LEFS`, `EXTRA_LIBS`, `EXTRA_GDS`,
`EXTRA_VERILOG_MODELS`, `EXTRA_SPICE_MODELS` and `EXTRA_CDLS`.

Exactly **one Liberty per cell**. Discovery groups by file stem, so a set of
per-corner libs (`foo_typ_1p20V_25C.lib`, `foo_slow_...`) becomes three
phantom cells with no LEF and no GDS, and PnR refuses to start. That is why
the flow pins `LAYOUT_CORNERS=typ`.

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
- every signal pin covers a routing track — a `Metal1` port must contain a
  `y = n * 0.42` um line, a `Metal2` port an `x = n * 0.48` um line
  (`tracks.info`, and `DIRECTION` in `sg13g2_tech.lef`)

That last one is the only one that survives placement. A pin off the track
grid places and globally routes without complaint, then aborts the whole
design in detailed routing with `DRT-0073 No access point` — a hard abort in
pin access, which `LENIENT=1` does not downgrade. `scripts/collect_cells.py`
grades it before PnR starts, and `make export` refuses to publish a cell that
fails it. All 283 signal pins of the PDK `sg13g2_stdcell` library pass.

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
