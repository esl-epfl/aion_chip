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

## TinyTapeout: the chip is the tile

`make pnr_simple` and `make pnr` both harden `tt_um_aion` as a **4x2
ihp-sg13g2 TinyTapeout tile** — 854.40 x 313.74 um, TT's pins, TT's power
stripes — and that GDS is the submission, unchanged.

It is the tile and not a smaller macro because of how TT takes a design it did
not build. A project hardened outside TT's CI goes in through
`tt-gds-action/custom_gds`, which copies a GDS, a LEF and a gate-level netlist
into `tt_submission/` and runs the precheck on them. Nothing on TT's side
places, routes or connects anything, and the precheck compares the LEF's die
and every pin rectangle against the tile's template DEF: a macro smaller than
the tile fails on its size before a single pin is looked at. Routing a smaller
macro out to the tile's pins would mean hardening a wrapper here as well — a
second LibreLane run, a PDN that reaches into the macro, hierarchical LVS and
STA — to arrive at the same tile with less core in it.

What TT's precheck (`tt-support-tools/precheck/precheck.py`) checks, and what
makes a run here pass it:

| Precheck | Requirement | Set by |
| --- | --- | --- |
| Pin check, boundary check | die exactly 854.40 x 313.74 um; a top-level `prBoundary.boundary` (189/4) rectangle that size; nothing outside it | `DIE_AREA` in `config.json`, which `prepare_librelane_config.py` refuses to let differ from the template's `DIEAREA` |
| Pin check | all 43 signal pins, one Metal4 rectangle each, exactly the template's | `FP_DEF_TEMPLATE` — the Makefile's `LIBRELANE_DEF_TEMPLATE`, `def/tt_block_$(TT_TILES)_pgvdd.def` |
| Pin check | `VPWR` and `VGND` LEF pins on TopMetal1 only, each stripe >= 2.1 um wide and within 10 um of the top and bottom edges | `PDN_MULTILAYER: false`; the one-row `TOP_`/`BOTTOM_MARGIN_MULT`, since the stripes end with the core; `MAGIC_WRITE_LEF_PINONLY`, which keeps rails and vias out of the ports |
| KLayout checks | no TopMetal2 drawing, pin or label; the top cell is `tt_um_aion` | `PDN_MULTILAYER: false` and `RT_MAX_LAYER: TopMetal1` — both default to TopMetal2 for this PDK |
| KLayout SG13G2 DRC | the PDK's own deck, **recommended rules included** | LibreLane's Magic and KLayout DRC gate the run, but its KLayout pass sets `no_recommended`; only `make tt_precheck` and flow step 10 run the deck the way TT does |
| Layer, cell name, zero-area checks | only layers on TT's list, no `#` or `/` in a cell name, no zero-area shape | PDK cells and LibreLane's stream-out |
| Verilog syntax check | the netlist reads in Yosys | `nl/tt_um_aion.nl.v` |

Two TT rules are not precheck items but hold anyway: the port list
(`clk`, `ena`, `rst_n`, `ui_in[8]`, `uio_in[8]`, `uio_oe[8]`, `uio_out[8]`,
`uo_out[8]` — `Odb.ApplyDEFTemplate` stops the run on any pin the template and
the design do not share) and the
supply names `VPWR`/`VGND`. `ena` is high whenever the tile is selected and
powered; AION gates nothing on it, so `tt_um_aion.vhd` reads it into a signal
called `unused`.

The rest of `config.json` is this design's own — the 50 ns clock, the 45%
placement density, `GRT_ALLOW_CONGESTION` off, the KLayout DRC and XOR TT's
CI skips to save minutes. None of those is anything the precheck looks at;
the keys in the table are, and `make tt_precheck` is how to find out whether a
change to one still passes.

`pin_order.cfg` is only read when `LIBRELANE_DEF_TEMPLATE` is set empty, for a
free-standing macro. TT will not accept that GDS.

### Submitting

```bash
./flow.py 7..10                            # the AION chip   -> flow/10_tt_precheck/
make pnr_simple && make tt_precheck        # the PDK-only chip -> flow/pnr_simple/tt_submission/
```

Flow step 10 and `make tt_precheck` are the same check. Each copies a saved
run's `gds/`, `lef/` and `nl/` views as `tt_um_aion.gds`, `tt_um_aion.lef` and
`tt_um_aion.v` — the three files `custom_gds` takes — and then passes them
through two gates:

1. **the run's signoff**, from its `metrics.json`: Magic, KLayout and routing
   DRC, overlaps, XOR, LVS, power grid, critical disconnected pins, setup,
   hold, slew and cap all reported and zero — the things TT's precheck never
   looks at;
2. **TT's precheck**, in the container, with `tt-support-tools` fetched at
   `TT_TOOLS_VERSION` (Makefile).

TT's reports land in `precheck/` next to the three files, and
`tt_precheck.json` holds the verdict. Step 10 keeps the AION chip's package in
its own directory, and re-running step 7 marks it `STALE`; the baseline's sits
inside `flow/pnr_simple/`, and the next `make pnr_simple` wipes it along with
the GDS it came from.

The submission repository commits those three files, an `info.yaml` with
`tiles: "4x2"` and `top_module: "tt_um_aion"`, and a GDS workflow that swaps
TT's hardening action for `custom_gds`, tagged for the shuttle as TT's template
is. The `precheck` job downloads the same `tt_submission` artifact either
way. The `gl_test` job reads `PDK_SOURCE` and `PDK_VERSION` from
`tt_submission/pdk.json`, and the `pdk.json` custom_gds writes carries only
`PDK` — check that job against the shuttle's template before counting on it.

```yaml
- uses: TinyTapeout/tt-gds-action/custom_gds@<shuttle tag>
  with:
    pdk: ihp-sg13g2
    top_module: tt_um_aion
    gds_path: gds/tt_um_aion.gds
    lef_path: lef/tt_um_aion.lef
    verilog_path: verilog/tt_um_aion.v
```

Three things to know before trusting a local pass:

* **The DRC deck is the container's PDK**, not the IHP-Open-PDK commit TT's
  precheck action pins. A rule the two versions disagree on is only settled by
  TT's own run.
* **TT's GL test only knows PDK cells.** It compiles `test/` against
  `sg13g2_stdcell.v` and the netlist. `flow/pnr_simple` needs nothing more;
  the `flow/7_pnr` netlist instantiates AION and PDK-extension cells, so the
  submission's `test/Makefile` has to add their Verilog models.
* **A clean baseline says little about the AION chip.** Step 6 checks each
  cell on its own; step 10 checks the tile, where the cells abut PDK cells and
  each other, with the recommended rules LibreLane's KLayout pass leaves out.
  A cell that is clean in step 6 can still fail there.

A `LENIENT=1` run is one whose checkers were told not to stop, so nothing in
LibreLane vouches for it. The signoff gate reads its metrics back anyway: it
passes only if the run came out clean regardless.

## What the ALU costs

The design is an adder and a multiplier per posit width — `PositAdder` /
`PositMult` at Posit<32,2>, `PositAdder16` / `PositMult16` at Posit<16,2> —
plus one comparator and one bitwise unit shared between them. No MAC array and
no size generic: the two widths are both built, and `control[4]` picks which
pair's answer reaches `result`.

Measured through `make synth` on the IHP SG13G2 typical corner, with each unit
also synthesised alone (`yosys … stat -liberty sg13g2_stdcell_typ_1p20V_25C`):

| | Posit<16,2> | Posit<32,2> | Factor |
| --- | ---: | ---: | ---: |
| adder | 9,204 um² | 21,558 um² | 2.3x |
| multiplier | 16,684 um² | 61,774 um² | 3.7x |

The multiplier is the whole story. A Posit<16,2> fraction multiply is one
DSP-shaped block; a Posit<32,2> one is a 29 x 29 partial-product array with a
compressor tree, which is why `mult.vhd` is 2,113 lines against `mult_16.vhd`'s
612, and why it alone is well over half the chip.

Carrying both widths rather than one:

| | Posit<32,2> only | both widths |
| --- | ---: | ---: |
| cell area | 85,878 um² | **106,452 um²** |
| instances | 7,633 | 9,622 |

+24% area for the second precision — less than the narrow pair's 25,888 um²
standalone, since synthesis shares some of it. The comparator and the bitwise
unit contribute nothing to that delta: they are not duplicated, only fed a
sign-extended or masked low half (see
[`../docs/architecture.md`](../docs/architecture.md#4-posit_alu--the-opcode-map)).

The earlier figures in this section were for a Posit<16,2>-only design at
36,631 um²; that configuration no longer exists, and the numbers above replace
it.

### On the 4x2 tile

Both precisions fit the tile. `make pnr_simple`, 2026-09-13, PDK cells only:

| | |
| --- | --- |
| Core area (81 rows x 1768 sites) | 259,837 um² |
| Utilisation at global placement | 48.2% (`PL_TARGET_DENSITY_PCT` 45 is raised to 0.49, with `GPL-0302`) |
| Standard-cell area after PnR | 152,293 um² — 58.6% |
| … of which antenna diodes | 30,569 um², 5,616 `sg13g2_antennanp` from `RUN_HEURISTIC_DIODE_INSERTION` |
| Setup / hold worst slack | +9.46 ns (`nom_slow_1p08V_125C`) / +0.175 ns (`nom_fast_1p32V_m40C`) |
| Detailed-routing DRC, antenna violations | 0 / 0 |
| Magic DRC, KLayout DRC, LVS, XOR | 0 / 0 / 0 / 0 |
| `make tt_precheck` | 10 of 10 checks pass, 2 min |
| LibreLane run time | 17 min 40 s |

The diodes are a fifth of the placed cell area: without them the cells take
46.8% of the core.

### It does not fit 660 x 210

The record of why the design left its earlier box, kept for the numbers.

**`make pnr_simple` fails on this floorplan.** The numbers, from that run —
which predates the Posit<16,2> pair, so it is the *smaller* of the two designs
failing. The width targets at the bottom of this section are the ones to redo
once a run with both precisions exists; at 106,452 um² of cells they all move
out:

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
| `make tt_precheck` | `TT_RUN_DIR=` (default `flow/pnr_simple`) | `<run>/tt_submission/` — the three files TT takes, and TT's precheck reports |

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

**Nothing reads it by default.** The TT DEF template places the pins, and
prepare_librelane_config.py drops `--pin-order` whenever a template is given.
It is used only with `LIBRELANE_DEF_TEMPLATE=` (empty), for a free-standing
macro that TT would not accept, and it was written for the old 660 x 210 um
box:

- **west** (210 um edge): `clk`, `rst_n`, kept away from the data buses
- **north** (660 um edge): `ui_in`, `uio_in` — everything the tile consumes
- **south** (660 um edge): `uo_out`, then `uio_out`/`uio_oe` interleaved per bit
  so a bidirectional pad sees output and enable next to each other

## Grid constraints

`sg13g2_stdcell` places on `CoreSite`: 0.48 um wide, 3.78 um tall. The tile is
854.40 x 313.74 um — 1780 sites by 83 rows — and there is no `CORE_AREA`: the
margins (one row top and bottom, six sites left and right) put 81 rows of 1768
sites at (2.88, 3.78), which is exactly where the template DEF's `ROW`
statements have them.
