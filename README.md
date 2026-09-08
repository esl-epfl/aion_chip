<p align="center">
  <img src="logo/AION_Logo_NoBG.png" alt="AION Logo" width="400"/>
</p>

<h1 align="center">AION</h1>
<p align="center"><strong><em>AI-Optimized Netlist-to-Layout</em></strong></p>

---

**AION** (_AI-Optimized Netlist-to-Layout_, module name: `tt_um_aion`) is a self-contained AI optimized testchip designed for the **Tiny Tapeout** platform on the **IHP SG13G2 130 nm SiGe BiCMOS** process.

---

## Repository layout

```
flow.py             the flow handler's entry point  -- start here
scripts/flow/       the handler itself, one module per step
aion_flow/          submodule: the AI cell tools (aion_opt, aion_char,
                    aion_minimizer, aion_layout) the flow drives

src/rtl/            VHDL sources
src/tb/             cocotb testbenches
src/misc/           dump_waves.v, sdf_annotate.v — extra roots for the Icarus runs
tech/               IHP SG13G2 cell views and their simulation models
implementation/     flow inputs: config template, SDC, TT DEF template
implementation/cells/   AI cell views, published by flow step 6  (git-ignored)
implementation/macros/  the logo macro, drawn by `make logo`
flow/               every step's output, one directory per step  (git-ignored)
scripts/            flow helpers driven by the Makefile
```

## Flow

The chip is built by a nine-step flow. `./flow.py` runs it; everything it
produces lands in `flow/`.

```bash
./flow.py                 # the whole chain
./flow.py 3               # one step, on its own
./flow.py 2..5            # a range
./flow.py status          # did anything go stale?
./flow.py --list          # what the steps are
./flow.py --dry-run       # print every command, run nothing
```

| # | Step | What it does |
| - | ---- | ------------ |
| 1 | `1_synth` | VHDL → gate-level netlist + pre-PnR STA, then a post-synthesis simulation |
| 2 | `2_pattern_extraction` | mine recurring subgraphs out of the netlist and emit an AION cell library |
| 3 | `3_rewrite` | substitute the cells, **prove equivalence (LEC)**, re-simulate |
| 4 | `4_characterization` | exhaustive SystemVerilog + SPICE testbenches per cell |
| 5 | `5_gate_minimization` | re-implement each cell as one CMOS stack, proved in SPICE |
| 6 | `6_layout_drawing` | draw, verify (DRC/LVS), characterize and publish each cell's views |
| 7 | `7_pnr` | harden the netlist with those cells, then simulate with SDF delays |
| 8 | `8_render` | draw the hardened die and mark where the AION cells landed |
| 9 | `9_report` | compare the chip against the PDK-only baseline — Markdown, JSON and a standalone HTML page |

Every step is runnable and re-runnable on its own, and `flow/coherence.json`
records what ran when so a re-run of an early step shows up as `STALE`
downstream instead of quietly producing a chip that mixes two different
inputs. **Step 6 is the one with a model in the loop** — it draws the cell
layouts. See [`scripts/flow/README.md`](scripts/flow/README.md) for the full
reference: every configuration knob, the manual-vs-agent drawing modes, and
how to hand-pick which cells survive into the netlist.

`make flow`, `make flow STEP=3` and `make flow-status` are the same thing from
the Makefile. `make help` lists every target.

### Simulation

The flow runs these for you at the right moments; they are also usable alone.

| Target                                | Simulator | Netlist            | Delays |
| ------------------------------------- | --------- | ------------------ | ------ |
| `make sim`                            | GHDL      | VHDL RTL           | —      |
| `make post_synth_sim`                 | Verilator | `flow/1_synth`     | —      |
| `make post_synth_sim TOOL=icarus`     | Icarus    | `flow/1_synth`     | —      |
| `make post_synth_sim_ai`              | Verilator | `flow/3_rewrite`   | —      |
| `make post_pnr_sim`                   | Icarus    | `flow/pnr_simple`  | SDF    |
| `make post_pnr_sim TOOL=verilator`    | Verilator | `flow/pnr_simple`  | —      |
| `make post_pnr_sim_ai`                | Icarus    | `flow/7_pnr`       | SDF    |
| `make post_pnr_sim_ai TOOL=verilator` | Verilator | `flow/7_pnr`       | —      |
| `make sim_all`                        | all of the above, as a summary table   |        |

The `_ai` targets are the AION-cell half of each pair: same testbench, same
corner, but against a netlist built from the mined cells.

#### The posit reference model

Every testbench checks the DUT against
[Stillwater Universal](https://github.com/stillwater-sc/universal), the
reference posit implementation — not against a model written here. Universal
is header-only C++20 with no Python bindings, so `src/tb/universal_posit.cpp`
is a small `extern "C"` shim and `src/tb/posit.py` is `ctypes` over it; the
testbenches gain no Python dependency.

```bash
make universal          # fetch the headers and build the shim (the sim targets do this)
make universal-update   # re-fetch after changing UNIVERSAL_VERSION
```

Only `include/` is fetched, sparse and blobless and pinned to a release tag:
~12 MB, against ~790 MB for the full tree, which is almost entirely docs. Set
`UNIVERSAL_ROOT=` to build against a copy you already have installed.

Arithmetic happens **in** the posit type. Decoding to `float`, operating
there and re-encoding agrees at Posit<16,2> — a double has enough mantissa to
make one rounding step exact — but that is a property of this width, not a
rule.

An exact reference makes the ends of the range testable, so the testbenches
sweep them: `POSIT_EDGE_CASES` crosses zero, ±one, ±minpos, ±maxpos, NaR and
the four truncated-exponent patterns with each other (144 pairs per operation,
44 of which saturate), NaR propagation is asserted directly on both operand
positions, and the random tests draw **encodings** rather than floats from a
narrow range. The old model decoded four of those patterns to half their true
value, so nothing could be checked against them.

GHDL is the only simulator that reads the VHDL, so RTL simulation has exactly
one backend; Verilator and Icarus only ever see gate-level Verilog.

Only the Icarus post-PnR runs have delays. Verilator ignores `$sdf_annotate`
entirely, so its post-PnR run re-checks function and nothing else. Pick the
corner with `SDF_CORNER=` — `nom_slow_1p08V_125C` (default),
`nom_typ_1p20V_25C`, `nom_fast_1p32V_m40C`. Icarus applies the SDF's cell and
interconnect delays but not its `TIMINGCHECK` records, so setup and hold remain
STA's business, not the simulation's: a violation will not fail this run in any
corner, it will latch a clean but wrong value in silence. Read the slacks off
`flow/7_pnr/reports/*-openroad-stapostpnr/` instead — and the PnR targets
themselves fail on a setup or hold violation in any corner.

Every simulation target ends by reading the cocotb results and failing if any
test did. That check is not optional decoration: the simulators exit 0 whether
the tests passed or not, so without it a green `make` means nothing.

### Waveforms

Every gate-level run writes a trace into its own build directory, so stages do
not overwrite each other's:

```bash
make waves                            # the last RTL run
make waves TARGET=post_synth_sim      # a specific stage
make waves TARGET=post_pnr_sim_ai
```

The trace is `.build/epfl_aion_aion_1.0.0/<target>/aion.fst`. `WAVEFORM_VIEWER=`
picks the viewer (`surfer` by default, `gtkwave` also works).

Dumping every net of a ~12k-instance post-PnR netlist costs more than the
simulation does, so the gate-level Icarus targets default to `GATE_DUMPDEPTH=1`
— the DUT's own ports, which is what you want almost every time. Raise it when
you need to see inside:

```bash
make post_pnr_sim GATE_DUMPDEPTH=0    # everything, slow and enormous
```

Verilator has no `dumpdepth` plusarg; its traces always carry what the
testbench asks for.

### Physical implementation

| Target            | In                        | Out                     |
| ----------------- | ------------------------- | ----------------------- |
| `make synth`      | VHDL via FuseSoC          | `flow/1_synth/`         |
| `make pnr`        | `NETLIST=` + `CELLS_DIR=` | `flow/7_pnr/`           |
| `make pnr_simple` | VHDL via FuseSoC          | `flow/pnr_simple/`      |
| `make logo`       | `logo/*.png`              | `implementation/macros/`|

`make synth` is flow step 1 and `make pnr` is flow step 7; the flow calls them
with the right arguments, and they still work by hand. `make pnr_simple` is
the PDK-only baseline the AI flow is measured against — it belongs to no step.

The floorplan is TinyTapeout's: a **4x2 ihp-sg13g2 tile**, 854.40 × 313.74 µm,
with TT's own DEF template fixing all 43 pins on Metal4 along the north edge.
`implementation/config.json` reproduces what `tt-support-tools` generates for
such a project and says, key by key, where each value comes from — see
[`implementation/README.md`](implementation/README.md).

`make logo` belongs to no step either: it rasterises the chip's logo onto
**TopMetal1 (126/0)** inside a **prBoundary (189/4)** and writes a GDS and a LEF.
The raster pixel is the layer's minimum width, so the art is DRC-clean by
construction, and the IHP deck confirms it. `make pnr` and `make pnr_simple`
merge it into the hardened GDS once they have checked that the router left that
layer alone.

`LENIENT=1` downgrades the hard checkers to warnings; never sign off a run made
that way. `make openroad` and `make klayout` open the last run in a GUI. See
[`implementation/README.md`](implementation/README.md) and
[`flow/README.md`](flow/README.md).

### Prerequisites

Both halves of the flow need the IIC-OSIC-TOOLS container running, because
`ngspice`, `magic`, `netgen`, `klayout`, `librelane` and `kepler-formal` live
in it and nowhere else:

```bash
docker start iic-osic-tools_shell_uid_1000
```

The flow probes for it and says so up front rather than failing halfway. On the
host you need the conda environment (`source env.sh`) for `yosys`, `ghdl`,
`iverilog`, `verilator`, `fusesoc` and cocotb, plus a C++20 compiler and `git`
for the posit reference model (see above).

---

## Technical Overview

### Quick Facts

| Spec                      | Value                                        |
| ------------------------- | -------------------------------------------- |
| **Chip Name**             | AION                                         |
| **Module Name**           | `tt_um_aion`                                 |
| **Process / Platform**    | IHP SG13G2 130 nm SiGe BiCMOS (Tiny Tapeout) |
| **Tile Dimensions**       | 4x2 tiles, 854.40 µm × 313.74 µm             |
| **Reference Clock Input** | 1 MHz – 25 MHz (`clk`, 40 ns signoff period) |

---

## Register Interface

The `tt_um_aion` wrapper exposes the AION posit arithmetic unit through a simple byte-wide register file. The register logic is implemented in `aion_interface.vhd` and is connected to the `aion_soc` compute core.

### Pin Usage

| Pin group | Direction | Purpose                              |
| --------- | --------- | ------------------------------------ |
| `ui_in`   | Input     | `ui_in[2:0]` = register address, `ui_in[7]` = R/W direction (`1` = write, `0` = read) |
| `uio_in`  | Input     | Write data byte                      |
| `uo_out`  | Output    | Read data byte                       |
| `ena`     | Input     | Required by the TinyTapeout harness; AION does not use it |

### Register Map

| Address | Name         | Access | Description                                      |
| ------- | ------------ | ------ | ------------------------------------------------ |
| `0x0`   | `opA_lo`     | R/W    | Operand A, low byte                              |
| `0x1`   | `opA_hi`     | R/W    | Operand A, high byte                             |
| `0x2`   | `opB_lo`     | R/W    | Operand B, low byte                              |
| `0x3`   | `opB_hi`     | R/W    | Operand B, high byte                             |
| `0x4`   | `control`    | R/W    | Control register (`bit[0]` = opcode, `bit[1]` = start trigger) |
| `0x5`   | `result_lo`  | R      | Result, low byte                                 |
| `0x6`   | `result_hi`  | R      | Result, high byte                                |
| `0x7`   | `status`     | R      | `status[0]` = `done` flag                        |

- Opcode: `0` = posit add, `1` = posit multiply.
- Writing to `control` with `bit[1] = 1` generates a one-cycle `start` pulse to the compute core.

### Reading the diagrams

```text
/‾‾‾\___   a clock cycle; the rising edge is the sampling edge
<  x  >     the bus is driven and stable with value x
------      don't care -- nothing reads this bus in that cycle
```

### Write transaction

`ui_in` and `uio_in` must be stable across one rising edge. That edge writes
the register; nothing is latched before it.

```text
cycle   |      0      |      1      |      2      |      3      
clk     /‾‾‾‾‾‾\______/‾‾‾‾‾‾\______/‾‾‾‾‾‾\______/‾‾‾‾‾‾\______
ui_in   --------------< 1,000,addr >----------------------------
uio_in  --------------< write data >----------------------------
start   ____________________________/‾‾‾‾‾‾‾‾‾‾‾‾‾\_____________
                                    ^
                                    this edge writes the register
```

1. Drive `ui_in` with `{1'b1, 4'b0, address}` — bit 7 high selects a write.
2. Drive `uio_in` with the byte to write.
3. Hold both across one rising edge. The register updates on that edge.
4. A write to `control` (`0x4`) with `bit[1] = 1` also raises `start` for
   exactly one cycle, which is what kicks off a computation. `start` is a
   pulse, not a level: it falls again whether or not you keep driving `ui_in`.

### Read transaction

Reads are **combinational**. There is no clock edge in this diagram's
critical path: `uo_out` follows `ui_in` after propagation delay, and holds
only while `ui_in` holds.

```text
cycle   |      0      |      1      |      2      |      3      
clk     /‾‾‾‾‾‾\______/‾‾‾‾‾‾\______/‾‾‾‾‾‾\______/‾‾‾‾‾‾\______
ui_in   --------------<        0,000,addr        >--------------
uo_out  ---------------<      register value     >--------------
                       ^
                       combinational: no edge required, but `uo_out`
                       is only valid while `ui_in` is held
```

1. Drive `ui_in` with `{1'b0, 4'b0, address}` — bit 7 low selects a read.
2. The selected register appears on `uo_out`. Sample it while `ui_in` is
   still driven; drop `ui_in` and `uo_out` stops being meaningful.

### Typical Operation Flow

1. Write operand A to `0x0` and `0x1`.
2. Write operand B to `0x2` and `0x3`.
3. Write `control` (`0x4`) with the desired opcode and `bit[1] = 1` to start.
4. Poll `status` (`0x7`) until `done` is high.
5. Read result from `0x5` and `0x6`.
