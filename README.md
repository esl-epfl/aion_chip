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
src/rtl/            VHDL sources
src/tb/             cocotb testbenches
src/misc/           dump_waves.v, sdf_annotate.v — extra roots for the Icarus runs
tech/               IHP SG13G2 cell views and their simulation models
implementation/     LibreLane inputs: config template, SDC, pin order, AI cells
flow/               LibreLane outputs, one directory per target  (git-ignored)
scripts/            flow helpers driven by the Makefile
```

## Flow

`make help` lists everything. The two halves:

### Simulation

| Target                             | Simulator          | Netlist              | Delays |
| ---------------------------------- | ------------------ | -------------------- | ------ |
| `make sim`                         | GHDL               | VHDL RTL             | —      |
| `make post_synth_sim`              | Verilator          | `flow/synth`         | —      |
| `make post_synth_sim TOOL=icarus`  | Icarus             | `flow/synth`         | —      |
| `make post_pnr_sim`                | Icarus             | `flow/pnr_simple`    | SDF    |
| `make post_pnr_sim TOOL=verilator` | Verilator          | `flow/pnr_simple`    | —      |
| `make post_pnr_sim_ai`             | Icarus             | `flow/pnr`           | SDF    |
| `make sim_all`                     | all of the above, as a summary table       |        |

GHDL is the only simulator that reads the VHDL, so RTL simulation has exactly
one backend; Verilator and Icarus only ever see gate-level Verilog.

Only the post-PnR Icarus run has delays. Verilator ignores `$sdf_annotate`
entirely, so its post-PnR run re-checks function and nothing else. Pick the
corner with `SDF_CORNER=` — `nom_slow_1p08V_125C` (default),
`nom_typ_1p20V_25C`, `nom_fast_1p32V_m40C`. Icarus applies the SDF's cell and
interconnect delays but not its `TIMINGCHECK` records, so setup and hold remain
STA's business, not the simulation's: a violation will not fail this run in any
corner, it will latch a clean but wrong value in silence. Read the slacks off
`flow/pnr_simple/reports/*-openroad-stapostpnr/` instead — and `make pnr_simple`
itself fails on a setup or hold violation in any corner.

### Physical implementation

| Target            | In                        | Out                 |
| ----------------- | ------------------------- | ------------------- |
| `make synth`      | VHDL via FuseSoC          | `flow/synth/`       |
| `make pnr`        | `NETLIST=` + `CELLS_DIR=` | `flow/pnr/`         |
| `make pnr_simple` | VHDL via FuseSoC          | `flow/pnr_simple/`  |

`LENIENT=1` downgrades the hard checkers to warnings. `make openroad` and
`make klayout` open the last run in a GUI. See `implementation/README.md` and
`flow/README.md`.

---

## Technical Overview

### Quick Facts

| Spec                      | Value                                        |
| ------------------------- | -------------------------------------------- |
| **Chip Name**             | AION                                         |
| **Module Name**           | `tt_um_aion`                                 |
| **Process / Platform**    | IHP SG13G2 130 nm SiGe BiCMOS (Tiny Tapeout) |
| **Tile Dimensions**       | 668 µm × 216 µm                              |
| **Reference Clock Input** | 1 MHz – 50 MHz (`clk`)                       |

---

## Register Interface

The `tt_um_aion` wrapper exposes the AION posit arithmetic unit through a simple byte-wide register file. The register logic is implemented in `aion_interface.vhd` and is connected to the `aion_soc` compute core.

### Pin Usage

| Pin group | Direction | Purpose                              |
| --------- | --------- | ------------------------------------ |
| `ui_in`   | Input     | `ui_in[2:0]` = register address, `ui_in[7]` = R/W direction (`1` = write, `0` = read) |
| `uio_in`  | Input     | Write data byte                      |
| `uo_out`  | Output    | Read data byte                       |

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

### Write Transaction

```text
clk     ____/‾‾‾‾\____/‾‾‾‾\____/‾‾‾‾\____/‾‾‾‾\____
ui_in   ----< addr+wr >-------------------------------
uio_in  ----< wr_data >-------------------------------
start   ____________________/‾‾‾‾\__________________   (only for addr=0x4, bit[1]=1)
```

1. Drive `ui_in` with `{1'b1, 4'b0, address}`.
2. Drive `uio_in` with the byte to write.
3. On the next rising clock edge the register is updated.
4. To start a computation, write `0x04` with `{6'b0, start=1, opcode}`.

### Read Transaction

```text
clk     ____/‾‾‾‾\____/‾‾‾‾\____/‾‾‾‾\____
ui_in   ----< addr+rd >---------------------
uo_out  --------------------< rd_data >----
```

1. Drive `ui_in` with `{1'b0, 4'b0, address}`.
2. The selected register value appears on `uo_out` combinationally.

### Typical Operation Flow

1. Write operand A to `0x0` and `0x1`.
2. Write operand B to `0x2` and `0x3`.
3. Write `control` (`0x4`) with the desired opcode and `bit[1] = 1` to start.
4. Poll `status` (`0x7`) until `done` is high.
5. Read result from `0x5` and `0x6`.
