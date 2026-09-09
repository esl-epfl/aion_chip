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

## What is kept from TinyTapeout

The floorplan is **not** TT's any more. This design is hardened to be
instantiated twice in a top level that lives in another repository, so TT's
tile `DIE_AREA` and its DEF template — which pins all 43 pins to the north edge
for the multiplexer — would both be the wrong shape. `DIE_AREA` is a plain
**660 x 210 um** box and pin placement falls back to `pin_order.cfg`, which
spreads the pins over all four sides.

What is kept is everything that is a *rule* rather than a floorplan, because
the macro still has to be submittable:

| Rule | Setting | Why |
| --- | --- | --- |
| Port list | `clk`, `ena`, `rst_n`, `ui_in[8]`, `uio_in[8]`, `uio_oe[8]`, `uio_out[8]`, `uo_out[8]` | `check_ports()` rejects a `tt_um_*` module missing any of them |
| Supply names | `VDD_PIN` `VPWR`, `GND_PIN` `VGND` | what the harness declares |
| Routing ceiling | `RT_MAX_LAYER: TopMetal1` | `IHPTech.project_top_metal_layer` |
| PDN off TopMetal2 | `PDN_MULTILAYER: false` | `PDN_HORIZONTAL_LAYER` defaults to TopMetal2, and TT's precheck lists `TopMetal2.drawing` in `forbidden_layers` — a project GDS carrying any is rejected |

`ena` is high whenever the tile is selected and powered; AION gates nothing on
it, so `tt_um_aion.vhd` reads it into a signal called `unused`.

`implementation/def/tt_block_4x2_pgvdd.def` is kept in case the TT floorplan is
wanted again: set `LIBRELANE_DEF_TEMPLATE` to it and change `DIE_AREA` to that
tile's row of TT's `tile_sizes.yaml` — the two have to agree, and nothing checks
it for you.

## What the Posit<32,2> ALU costs

The design is one `PositAdder`, one `PositMult`, a comparator and a bitwise
unit — no MAC array and no size generic any more. What sizes this macro is the
posit width, and it is not a free parameter: it is what the arithmetic is.

Measured through `make synth` on the IHP SG13G2 typical corner, with each unit
also synthesised alone (`yosys … stat -liberty sg13g2_stdcell_typ_1p20V_25C`):

| | Posit<16,2> | Posit<32,2> | Factor |
| --- | ---: | ---: | ---: |
| `PositAdder` | 8,850 um² | 21,663 um² | 2.4x |
| `PositMult` | 19,781 um² | 61,864 um² | 3.1x |
| whole design | 36,631 um² | **89,140 um²** | 2.4x |
| instances | — | 7,744 | |

The multiplier is the whole story. A Posit<16,2> fraction multiply is one
DSP-shaped block; a Posit<32,2> one is a 29 x 29 partial-product array with a
compressor tree, which is why `mult.vhd` grew from 732 lines to 2,113 and why
it alone is 69% of the chip.

### It does not fit 660 x 210

**`make pnr_simple` fails on this floorplan.** The numbers, from that run:

| | |
| --- | --- |
| Cell area | 89,140 um² |
| Core area (`CORE_AREA` 9.6, 11.34 → 650.4, 200.34) | 121,111 um² |
| Utilisation OpenROAD reports | 82.5% |
| Minimum feasible density (`GPL-1004`) | 0.83 |
| `PL_TARGET_DENSITY_PCT` in `config.json` | 45 |

Global placement warns `GPL-0302: Target density 0.4500 is too low for the
available free area`, and detailed placement then fails outright —
`DPL-0036`, on 210 instances. Raising `PL_TARGET_DENSITY_PCT` to something
above 0.83 is not the fix: the design would be placed at a density where
routing this PDK is not realistic. The box has to grow.

Keeping the 50-row core height (189 um, the row pitch is 3.78 um) and the
19.2 um of left/right margin, the width needed is:

| Target core utilisation | Core area needed | Core width | `DIE_AREA` width |
| ---: | ---: | ---: | --- |
| 60% | 148,567 um² | 786.24 um | **805.44** |
| 55% | 162,073 um² | 857.76 um | **876.96** |
| 50% | 178,280 um² | 943.68 um | **962.88** |

Each width is a whole number of 0.48 um `CoreSite` steps (1678, 1827 and 2006
sites). `CORE_AREA` has to move with `DIE_AREA` — nothing checks that the two
agree, and global placement simply fails when they do not. Growing the height
instead is equally valid; it has to come in whole 3.78 um rows.

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
every knob. Nothing merges it into a hardened GDS automatically any more — see
*Getting it onto the die* below.

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

`scripts/merge_logo.py` puts the art into a hardened GDS and adds the footprint
to the LEF as an obstruction:

```bash
python3 scripts/merge_logo.py <die>.gds implementation/macros/aion_logo.gds \
    --at X,Y --design-top tt_um_aion --lef <die>.lef
```

The hardening targets no longer run it. This design is about to become one of
two macros in a parent, and a logo belongs to the parent's die, not to a macro
that will be instantiated twice. It is a post-step rather than a `MACROS` entry
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
