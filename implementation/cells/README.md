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

Instances whose name matches `RSZ_DONT_TOUCH_RX` are hidden from the resizer,
so it will not size or buffer them away. The regex matches **instance and net
names, not cell masters** — `implementation/config.json` sets `^_AION_.*`,
which is the instance prefix `aion_opt`'s rewriter emits (`_instance_prefix`
turns the `AION_` cell prefix into `_AION_`). Change `CELL_PREFIX` in the flow
and you must change this regex with it, or the resizer is free to buffer and
resize away the cells you spent step 6 drawing.
