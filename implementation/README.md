# Physical implementation

Everything LibreLane needs. Hand-written and tracked; the outputs of a run go
to `../flow/` instead, one directory per target — see `../flow/README.md`.

```
config.json              flow configuration template (see below)
constraints/aion.sdc     timing constraints, used for PnR and signoff STA
def/                     TinyTapeout's DEF templates — pin placement per tile size
pin_order.cfg            our own pin placement, superseded by def/ (see below)
cells/                   AI-generated standard cells (see cells/README.md)
macros/                  the logo macro, drawn by `make logo` (see below)
```

## TinyTapeout compatibility

The floorplan reproduces what `tt-support-tools` generates for a **4x2
ihp-sg13g2 tile**, so a run here lands the same macro the shuttle would build.
TT composes two files — the template's `src/config.json` and the
`user_config.json` that [`project.py`][ttp]'s `create_user_config()` writes —
and merges them; both are folded into `config.json`, which says where each
value comes from and which five keys are deliberately dropped.

| What | Value | Where TT gets it |
| ---- | ----- | ---------------- |
| `DIE_AREA` | `0 0 854.40 313.74` | `tech/ihp-sg13g2/tile_sizes.yaml`, key `4x2` |
| `FP_DEF_TEMPLATE` | `def/tt_block_4x2_pgvdd.def` | `tech/<pdk>/def/tt_block_<tiles>_<def_suffix>.def` |
| `VDD_PIN` / `GND_PIN` | `VPWR` / `VGND` | `create_user_config()` |
| `RT_MAX_LAYER` | `TopMetal1` | `tech.py`, `IHPTech.project_top_metal_layer` |
| `CLOCK_PERIOD` | 20 ns | the template's `src/config.json` |

Change tile size with `make pnr_simple TT_TILES=3x2` once the matching
`tt_block_<tiles>_pgvdd.def` is in `def/`, and update `DIE_AREA` to that tile's
row of `tile_sizes.yaml` — the two have to agree, and nothing checks it for you.

The DEF template fixes all 43 pins on **Metal4 along the north edge**, which is
where the multiplexer routes to, so it supersedes `pin_order.cfg`:
`prepare_librelane_config.py` ignores `--pin-order` when `--def-template` is
given, and the Makefile passes the latter. `pin_order.cfg` is kept for the
non-TT floorplan — set `LIBRELANE_DEF_TEMPLATE=` empty to go back to it.

The DEF is vendored rather than referenced because `scripts/docker_run.sh`
mounts only this project into the container, so TT's `dir::../tt/tech/…` path
does not resolve here.

### The port list

`check_ports()` rejects a `tt_um_*` module missing any of `clk`, `ena`,
`rst_n`, `ui_in[8]`, `uio_in[8]`, `uio_oe[8]`, `uio_out[8]`, `uo_out[8]`.
`ena` is high whenever the tile is selected and powered; AION gates nothing on
it, so `tt_um_aion.vhd` reads it into a signal called `unused` — a port with no
reader and a port whose only reader says dropping it was deliberate are
different things to anyone reading the file later.

[ttp]: https://github.com/TinyTapeout/tt-support-tools/blob/main/project.py

## Targets

| Target            | In                                  | Out                                  |
| ----------------- | ----------------------------------- | ------------------------------------ |
| `make synth`      | VHDL via FuseSoC                    | `flow/1_synth/` — netlist + pre-PnR STA |
| `make pnr`        | `NETLIST=` + `CELLS_DIR=`           | `flow/7_pnr/` — GDS                   |
| `make pnr_simple` | VHDL via FuseSoC                    | `flow/pnr_simple/` — GDS, PDK cells only |

Each of these hardens in `.build/<target>_aion/` and then copies the last run's
views, metrics and reports into the directory in the table, which is the fixed
path the gate-level simulation targets read. The destination is a variable, so
the flow handler and a hand-run `make` land in the same place:

| Variable | Default |
| -------- | ------- |
| `SYNTH_OUT_DIR` | `flow/1_synth` |
| `PNR_OUT_DIR` | `flow/7_pnr` |
| `PNR_SIMPLE_OUT_DIR` | `flow/pnr_simple` |

`_save_run` refuses an output directory outside `flow/`, and **wipes it** before
copying — so never point one at a directory holding anything you want to keep.

`make synth` is flow step 1 and `make pnr` is flow step 7; `./flow.py` calls
them with these variables set. `make pnr_simple` is the PDK-only baseline and
belongs to no step.

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

## macros/

`make logo` turns `logo/AION_Logo_CorrectSize.png` into a macro —
`aion_logo.gds`, `aion_logo.lef`, and an SVG and PNG to look at.
`scripts/logo_to_gds.py` does the work; `make logo LOGO_ARGS=--help` lists
every knob. `make pnr` and `make pnr_simple` then merge it into the hardened
GDS, which is a post-step for a reason — see *Getting it onto the die* below.

| Variable | Default | What it sets |
| -------- | ------- | ------------ |
| `LOGO_PNG` | `logo/AION_Logo_CorrectSize.png` | the picture |
| `LOGO_CELL` | `aion_logo` | cell and macro name |
| `LOGO_LAYER` | `TopMetal1` | the layer the art lands on |
| `LOGO_WIDTH` | `640` | picture width in microns; the height follows its aspect ratio |
| `LOGO_CLASS` | `COVER` | LEF class; `BLOCK` would reserve the whole footprint |
| `LOGO_AT` | `105.6,52.92` | lower-left corner on the die; empty harden without it |
| `LOGO_ARGS` | — | anything else to pass the script (`--invert`, `--crop`, …) |

The art is on **TopMetal1 (126/0)** and the footprint is a **prBoundary
(189/4)** rectangle from the origin, so the LEF's `SIZE` and the GDS agree and
a placement at (x, y) puts the boundary's lower-left corner there.

The picture is rasterised onto a grid whose pixel is the layer's coarsest
minimum — 1.64 um for TopMetal1 — so every edge is a multiple of it and no
feature and no gap can come out below the rule. Diagonal pixels touching at a
corner are the one case the grid cannot rule out (zero spacing, and both TM1.a
and TM1.b fire on it), so those are filled in the raster before any polygon
exists. `make logo` passes `--strict`, so it fails rather than writing art that
breaks the layer's rules.

The macro reproduces the picture as given — canvas, blank border and aspect
ratio — so `LOGO_WIDTH` is the width of the *picture*, not of the strokes.
`AION_Logo_CorrectSize.png` is 2640 x 840 with the strokes occupying 1041 x 364
of it, so at `LOGO_WIDTH=600` the strokes get 235 of the raster's 366 columns.
`LOGO_ARGS=--crop` sizes the macro from the strokes instead, which spends the
whole width on them and drops the border and the 3.14 aspect ratio with it.

### Getting it onto the die

`make pnr` and `make pnr_simple` finish by running `scripts/merge_logo.py`,
which puts the art into the hardened GDS at `LOGO_AT` and adds the footprint to
the LEF as an obstruction. It is a post-step rather than a `MACROS` entry
because a decorative macro cannot survive a VHDL front end: GHDL imports an
unbound component as an empty module, that module shadows the liberty blackbox
LibreLane reads for a `MACROS` entry, and `opt_clean` deletes the instance —
with no ports, with a connected input, and with `keep`, `syn_keep` and
`keep_hierarchy` on it, because GHDL propagates none of those. No instance, and
`Odb.ManualMacroPlacement` has nothing to place.

**The logo shares TopMetal1 with the PDN.** `PDN_VERTICAL_LAYER` is TopMetal1
for this PDK, so straps 2.2 µm wide on a 38.87 µm pitch cross the whole die,
and a floating island bridging a VPWR strap and a VGND one shorts the supply.
Moving the logo does not help — the straps span it — and there is nowhere to
move it to: `PDN_MULTILAYER` off keeps the PDN off TopMetal2, but TT's precheck
lists `TopMetal2.drawing` in `forbidden_layers`, so a project GDS carrying any
is rejected. Metal5 and below carry routing.

So the art is carved instead. `merge_logo.py` subtracts everything already on
the layer, grown by the layer's minimum **spacing**, then opens the result by
its minimum **width** so the cut cannot leave a sliver too thin to draw. The
straps cost about a sixth of the strokes and the logo still reads as itself.
`--no-carve` refuses on any overlap instead, if you would rather move it than
stripe it.

### Inverting it

`LOGO_ARGS="--invert --recog"` draws the **background** and cuts the letters out
of it, which is the white-background logo rather than the strokes. Two things
change with it, and both are checked:

* the art becomes a metal plate, and metal wider than 30 um has to carry slits
  (**Slt.c**) — which would put stripes through the logo. The rule derives its
  input as `TopMetal1.ext_not(Recog_or_MIM_or_dfpad_all)`, so `--recog` draws a
  **Recog (99/0)** rectangle over the macro and the rule stops applying.
  Verified against the deck: the same GDS fails `Slt.c.TM1` without the marker
  and passes with it. Without `--recog` this is a `--strict` failure, so
  `make logo LOGO_ARGS=--invert` refuses rather than writing art that cannot be
  built;
* the macro goes from ~5% metal to ~91%. The density rules (**TM1.c** min 25%,
  **TM1.d** max 70%) divide by the *chip* area, not the macro's, so 91% is not
  itself a verdict — on a 660 x 210 um die the same plate is about 35%, inside
  both limits, where the un-inverted logo's 6447 um² is about 5% and under
  TM1.c on its own. The script prints the macro's own figure and says which way
  to read it.

Checking the macro standalone reports `TM1.d` for the inverted version anyway,
because the deck takes the chip area from prBoundary **189/0** or EdgeSeal
39/4 — neither of which a macro carries — and falls back to the layout's
bounding box, i.e. the logo itself. Read that one off the die, not off this.

That is a measurement, not signoff. The deck itself agrees, and is one command:

```bash
scripts/docker_run.sh python3 \
    $PDK_ROOT/$PDK/libs.tech/klayout/tech/drc/run_drc.py \
    --path=implementation/macros/AION_logo.gds --no_density
```

`--no_density` because TM1.c wants 25% global TopMetal1 density and a logo on
its own is nowhere near that; run the density rules on the die, not on this.

The macro is `CLASS BLOCK`, which reserves its whole footprint — nothing is
placed under it. Pass `LOGO_ARGS=--lef-class=COVER` for a logo that sits on a
top metal over a populated core instead.

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
