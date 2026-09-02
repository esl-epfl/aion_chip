<p align="center">
  <img src="logo/AION_Logo_NoBG.png" alt="AION Logo" width="400"/>
</p>

<h1 align="center">AION</h1>
<p align="center"><strong><em>AI-Optimized Netlist-to-Layout</em></strong></p>

---

**AION** (_AI-Optimized Netlist-to-Layout_, module name: `tt_um_aion`) is a self-contained AI optimized testchip designed for the **Tiny Tapeout** platform on the **IHP SG13G2 130 nm SiGe BiCMOS** process.

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
