# Physical implementation

Everything LibreLane needs. Hand-written and tracked; the outputs of a run go
to `../flow/` instead, one directory per target — see `../flow/README.md`.

```
config.json              flow configuration template (see below)
constraints/aion.sdc     timing constraints, used for PnR and signoff STA
pin_order.cfg            pin placement, consumed by Odb.CustomIOPlacement
cells/                   AI-generated standard cells (see cells/README.md)
```

## Targets

| Target            | In                                  | Out                                  |
| ----------------- | ----------------------------------- | ------------------------------------ |
| `make synth`      | VHDL via FuseSoC                    | `flow/synth/` — netlist + pre-PnR STA |
| `make pnr`        | `NETLIST=` + `CELLS_DIR=`           | `flow/pnr/` — GDS                     |
| `make pnr_simple` | VHDL via FuseSoC                    | `flow/pnr_simple/` — GDS, PDK cells only |

Each of these hardens in `.build/<target>_aion/` and then copies the last run's
views, metrics and reports into `flow/<target>/`, which is the fixed path the
gate-level simulation targets read.

`make pnr` skips synthesis: it hands LibreLane the netlist as the flow's initial
state (`--from Checker.NetlistAssignStatements -e nl=…`) and runs the rest of
VHDLClassic on it. That netlist is expected to already instantiate the custom
cells; `CELLS_DIR` only supplies their LEF/LIB/GDS/Verilog/SPICE views.

Add `LENIENT=1` to any target to downgrade the hard checkers to warnings — a
malformed custom cell then still produces a GDS and a full set of reports.

## config.json

A template, never handed to LibreLane directly.
`scripts/prepare_librelane_config.py` resolves the FuseSoC source list, rewrites
paths relative to the run directory, applies the `_modes` overlay for the
requested target and writes the result to `<run dir>/config.json`. Keys named
`//` and `_modes` are stripped on the way out.

## pin_order.cfg

`#W`, `#N`, `#E`, `#S` open a side; every following token is a pin regex, listed
from the low coordinate of that edge to the high one. **The format has no
comment syntax** — any stray line is parsed as a pin regex, and a line starting
with `#` followed by N/E/W/S silently opens a new side.

Current layout for the 660 x 210 um tile:

- **west** (210 um edge): `clk`, `rst_n`, kept away from the data buses
- **north** (660 um edge): `ui_in`, `uio_in` — everything the tile consumes
- **south** (660 um edge): `uo_out`, then `uio_out`/`uio_oe` interleaved per bit
  so a bidirectional pad sees output and enable next to each other

This is our own floorplan constraint, not the official TinyTapeout harness
pinout. When submitting to a TT shuttle, replace it with TT's DEF template:
pass `--def-template` from the Makefile, which sets `FP_DEF_TEMPLATE` and
overrides pin positions wholesale.

## Grid constraints

`sg13g2_stdcell` places on `CoreSite`: 0.48 um wide, 3.78 um tall. `CORE_AREA`
therefore starts at y = 11.34 (3 rows) and spans 189 um (50 rows); starting it
anywhere else makes OpenROAD snap it and warn. `DIE_AREA` is 660 x 210 um, and
660 is exactly 1375 site widths.
