# AION — what is inside the chip, and how to drive it

AION is a Posit&lt;32,2&gt; arithmetic unit behind a byte-wide register interface.
Everything reachable from the outside goes through fourteen registers;
everything inside is one adder, one multiplier, a comparator and a bitwise
unit.

This document is the reference for the blocks and their programming model.
For the physical implementation see [`../implementation/README.md`](../implementation/README.md);
for the AI-cell flow see [`../scripts/flow/README.md`](../scripts/flow/README.md).

---

## 1. The whole chip

```
                        tt_um_aion  (660 x 210 um)
  ┌──────────────────────────────────────────────────────────────────────┐
  │                                                                      │
  │  clk ──┬───────────────────────────────────────────────────────┐     │
  │  rst_n ┼──┬────────────────────────────────────────────────┐   │     │
  │  ena ──╳  │  (read into `unused`; the design gates nothing on it) │  │
  │           │                                                │   │     │
  │        ┌──▼────────────────────┐        ┌──────────────────▼───▼──┐  │
  │ ui_in ─►                       │ opA[32]│                         │  │
  │  [8]   │    aion_interface     ├───────►│       posit_alu         │  │
  │        │                       │ opB[32]│                         │  │
  │ uio_in ►  14 x 8-bit registers ├───────►│  ┌──────────────────┐   │  │
  │  [8]   │  address decode       │ opcode │  │   PositAdder     │   │  │
  │        │  start pulse          ├───────►│  ├──────────────────┤   │  │
  │ uo_out ◄  read mux             │        │  │   PositMult      │   │  │
  │  [8]   │                       │ start  │  ├──────────────────┤   │  │
  │        │                       ├───────►│  │  posit_compare   │   │  │
  │        │                       │        │  ├──────────────────┤   │  │
  │        │                       │◄───────┤  │  posit_bitwise   │   │  │
  │        │                       │ result │  └──────────────────┘   │  │
  │        │                       │◄───────┤        result mux       │  │
  │        └───────────────────────┘  done  └─────────────────────────┘  │
  │                                                                      │
  │  uio_out[8] = 0x00   uio_oe[8] = 0x00   (bidirectionals are inputs)   │
  └──────────────────────────────────────────────────────────────────────┘
```

`aion_soc` is the wrapper that holds the interface and the ALU together;
`tt_um_aion` adds nothing but the TinyTapeout port list.

### Pins

| Pin | Dir | Width | Use |
| --- | --- | --- | --- |
| `clk` | in | 1 | single clock, 50 ns signoff period |
| `rst_n` | in | 1 | asynchronous, active low |
| `ena` | in | 1 | required by the harness; **not used** — read into a signal called `unused` |
| `ui_in` | in | 8 | `[3:0]` register address, `[7]` direction (1 = write); `[6:4]` unread |
| `uio_in` | in | 8 | write data |
| `uo_out` | out | 8 | read data |
| `uio_out` | out | 8 | tied to `0x00` |
| `uio_oe` | out | 8 | tied to `0x00` — every bidirectional is an input |

`ena` is high whenever the tile is selected and powered. AION gates nothing on
it: the register file is reset by `rst_n` and driven by `ui_in`. It is read into
`unused` so that a port with no reader and a port whose only reader documents
the omission are distinguishable in the source.

---

## 2. Register map

Fourteen byte registers, addressed by `ui_in[3:0]`. A Posit&lt;32,2&gt; operand
is four bytes, so the map needs fourteen addresses where the Posit&lt;16,2&gt;
one needed eight — which is why the address field is four bits wide and not
three. Operands and results are little-endian: byte 0 is bits 7:0.

| Addr | Name | Access | Contents |
| --- | --- | --- | --- |
| `0x0` | `opA_b0` | R/W | operand A, bits 7:0 |
| `0x1` | `opA_b1` | R/W | operand A, bits 15:8 |
| `0x2` | `opA_b2` | R/W | operand A, bits 23:16 |
| `0x3` | `opA_b3` | R/W | operand A, bits 31:24 |
| `0x4` | `opB_b0` | R/W | operand B, bits 7:0 |
| `0x5` | `opB_b1` | R/W | operand B, bits 15:8 |
| `0x6` | `opB_b2` | R/W | operand B, bits 23:16 |
| `0x7` | `opB_b3` | R/W | operand B, bits 31:24 |
| `0x8` | `control` | R/W | command — see below |
| `0x9` | `result_b0` | R | result, bits 7:0 |
| `0xA` | `result_b1` | R | result, bits 15:8 |
| `0xB` | `result_b2` | R | result, bits 23:16 |
| `0xC` | `result_b3` | R | result, bits 31:24 |
| `0xD` | `status` | R | bit 0 = `done`, bits 7:1 = 0 |
| `0xE`–`0xF` | — | R | unmapped, read `0x00` |

### `control` (`0x8`)

```
   bit   7     6     5     4     3     2     1     0
       ┌─────┬───────────────────┬───────────────────────┐
       │START│      unused       │        opcode         │
       └─────┴───────────────────┴───────────────────────┘
         [7]        [6:4]                  [3:0]
```

* **`[3:0]` opcode** — which block drives `result` (table in §4).
* **`[6:4]`** — unused. This field held the MAC lane select before the array
  was removed; it is stored in `control` and read back, but nothing decodes it.
* **`[7]` start** — writing `control` with this bit set fires the command.
  It is a trigger, not a stored level: the pulse lasts one cycle whatever you
  leave on the bus.

---

## 3. Bus protocol

### Write

`ui_in` and `uio_in` must be stable across one rising edge; that edge writes
the register.

```
cycle   |      0      |      1      |      2      |
clk     /‾‾‾‾‾‾\______/‾‾‾‾‾‾\______/‾‾‾‾‾‾\______
ui_in   --------------< 1,000,addr >--------------
uio_in  --------------< write data >--------------
                                    ^
                                    this edge writes the register
```

Drive `ui_in = 0x80 | addr` and `uio_in = data`, hold across one rising edge.

### Read

Reads are **combinational** — no clock edge is required. `uo_out` follows
`ui_in` after propagation delay and is only valid while `ui_in` is held.

```
cycle   |      0      |      1      |      2      |
clk     /‾‾‾‾‾‾\______/‾‾‾‾‾‾\______/‾‾‾‾‾‾\______
ui_in   --------------<     0,000,addr      >-----
uo_out  ---------------<   register value   >-----
```

Drive `ui_in = addr` (bit 7 low) and sample `uo_out`.

---

## 4. `posit_alu` — the opcode map

`posit_alu` is a decoder, four arithmetic blocks and one output multiplexer.

| Opcode | Name | Block | `result` |
| --- | --- | --- | --- |
| `0000` | ADD | `PositAdder` | `opA + opB` |
| `0001` | MULT | `PositMult` | `opA × opB` |
| `0010` | EQ | `posit_compare` | `0x00000001` if equal, else `0x00000000` |
| `0011` | LT | `posit_compare` | `0x00000001` if `opA < opB`, else `0x00000000` |
| `0100` | AND | `posit_bitwise` | `opA & opB` |
| `0101` | OR | `posit_bitwise` | `opA \| opB` |
| `0110` | XOR | `posit_bitwise` | `opA ^ opB` |
| others | — | — | `0x00000000` |

### `done` — the completion flag

`done` reports completion, and it is a **level**, not a pulse. It falls on the
edge that accepts a command and rises on the edge where that command's result
is settled, then holds until the next command. Reset leaves it low: nothing
has been computed yet.

```
edge         E0                         E1
        write control              result settled
        done  1 -> 0               done  0 -> 1
        opcode is now the new
        command; the arithmetic
        pipeline stage fills
```

Both halves of that matter:

* **it is not a pulse**, so software that polls `status` a cycle late still
  sees it. A one-cycle pulse can be missed, and a poll loop that misses it
  never terminates;
* **it is cleared when the command is accepted**, so a poll issued straight
  after the write cannot be answered by the *previous* operation's flag.

The one-edge latency is the arithmetic's, and `ALU_LATENCY` in
`posit_alu.vhd` is where it is written down. The Posit&lt;32,2&gt; adder and
multiplier are one pipeline stage deep — FloPoCo puts a register in the
adder's fraction normalizer and in the multiplier's encoder — where the
Posit&lt;16,2&gt; pair they replace were purely combinational. That stage fills
on the `control` edge itself, because the operand registers cannot change on
that edge: only one register is written per edge, so they have been stable for
a full cycle by then. What is left is the combinational tail of the arithmetic
and the result multiplexer, which needs the rest of that cycle — hence the
answer is stable one edge later, and that is where `done` rises.

`posit_compare` and `posit_bitwise` are wholly combinational and settle
sooner; the same edge covers them. Re-pipelining either FloPoCo unit means
raising `ALU_LATENCY` to match.

---

## 5. Blocks

### `aion_interface` — register file

Address decode, the fourteen registers, the start pulse and the read
multiplexer. Reset (`rst_n` low, asynchronous) clears `opA`, `opB` and
`control`; the result and status addresses are wires into `posit_alu`, not
registers of their own.

Only `ui_in[3:0]` and `ui_in[7]` are read. An address in `0xE`–`0xF` reads
`0x00` rather than aliasing onto a real register.

### `PositAdder` / `PositMult`

FloPoCo-generated Posit&lt;32,2&gt; units (`src/rtl/add.vhd`,
`src/rtl/mult.vhd`), **pipeline depth 1**. Together they are the whole area
budget of the chip and its longest path, which is why `CLOCK_PERIOD` is 50 ns.

| Unit | Standard-cell area | Was, at Posit&lt;16,2&gt; |
| --- | --- | --- |
| `PositAdder` | 21,663 µm² | 8,850 µm² |
| `PositMult` | 61,864 µm² | 19,781 µm² |

Measured with `yosys … stat -liberty sg13g2_stdcell_typ_1p20V_25C` on each
unit alone. Doubling the word width cost the multiplier a factor of 3.1: the
Posit&lt;16,2&gt; multiplier fits one DSP-shaped block, while the
Posit&lt;32,2&gt; one is a 29 × 29 partial-product array with a compressor
tree (`Compressor_*`, `DSPBlock_*`, `IntAdder_38_*` in `mult.vhd`).

#### Why the sub-entities carry `_add` and `_mult` suffixes

FloPoCo numbers its operators per generated file, so `add.vhd` and `mult.vhd`
both contain a `PositFastDecoder_32_2_F50_uid4` and a
`Normalizer_ZO_30_30_30_F50_uid6` — different entities under identical names.
Both files are analysed into the same `work` library, where the second
definition would silently replace the first. Every sub-entity is therefore
suffixed `_add` or `_mult` after generation; only the two top levels,
`PositAdder` and `PositMult`, keep their bare names. Regenerating either file
from FloPoCo means redoing the rename.

### `posit_compare`

Posits use two's-complement signed ordering, so comparison is a signed integer
compare on the raw 32 bits. `result` is `0x00000001` or `0x00000000` — a flag
in bit 0, not a posit.

### `posit_bitwise`

Bitwise `and` / `or` / `xor` on the raw encodings. No posit semantics; useful
for masking and sign manipulation.

---

## 6. How to drive it

Every command is the same four steps. `write(a, d)` means drive
`ui_in = 0x80 | a`, `uio_in = d` across one rising edge; `read(a)` means drive
`ui_in = a` and sample `uo_out`.

```
  write(0x0..0x3, opA byte 0..3)      -- low byte first
  write(0x4..0x7, opB byte 0..3)
  write(0x8, 0x80 | opcode)
  poll  read(0xD) until bit 0 is set          -- done; cleared by the write
  read(0x9..0xC)                              -- result, low byte first
```

### Arithmetic, compare, bitwise

```
  add:        write(0x8, 0x80 | 0x0)
  multiply:   write(0x8, 0x80 | 0x1)
  equal:      write(0x8, 0x80 | 0x2)     -> result bit 0
  less:       write(0x8, 0x80 | 0x3)     -> result bit 0
  and/or/xor: write(0x8, 0x80 | 0x4 / 0x5 / 0x6)
```

Operands and results are raw Posit&lt;32,2&gt; encodings. Compare returns a
flag in bit 0, not a posit.

### Reset

`rst_n` is asynchronous and active low. It clears the operand and control
registers. The adder's and multiplier's pipeline registers are not reset —
they are refilled from the operand registers on the first edge after reset,
which is many edges before any result is read. Hold `rst_n` low for a few
cycles at power-up; the testbench uses five.

---

## 7. Physical

| | |
| --- | --- |
| Process | IHP SG13G2, 130 nm |
| Die | 660 × 210 µm, hardened as a macro |
| Clock | 50 ns signoff period (20 MHz) |
| Routing ceiling | TopMetal1 |
| Supplies | `VPWR` / `VGND` |
| Cell area | 89,140 µm² over 7,744 instances |

The design is hardened to be instantiated in a parent, not as a TinyTapeout
tile, but it keeps every TT *rule*: the `tt_um_*` port list including `ena`, the
supply names, `RT_MAX_LAYER: TopMetal1`, and `PDN_MULTILAYER: false` — which
keeps the power grid off TopMetal2, a layer TT's precheck forbids in a project
GDS. See [`../implementation/README.md`](../implementation/README.md).

The critical path moved when the arithmetic did. At Posit&lt;16,2&gt; it ran
from an operand register through the whole combinational ALU to `uo_out`.
Now the worst setup path is **register to register** — an operand register in
`aion_interface` (`opa[30]`) into a pipeline register inside the adder — and
it closes with 14.55 ns of slack at 50 ns in `nom_slow_1p08V_125C`. Setup is
met in all three corners; the pre-PnR hold violations (33 register-to-register
in `nom_fast_1p32V_m40C`) are what PnR's hold fixing is for.

`aion_interface` still drives `uo_out` combinationally from `result`, so the
output path carries the result multiplexer; it is no longer the binding one.
Registering `result` before the read multiplexer remains the change that would
shorten it.

---

## 8. Where things live

```
src/rtl/tt_um_aion.vhd      top level, TinyTapeout port list
src/rtl/aion_soc.vhd        interface + ALU
src/rtl/aion_interface.vhd  register file, address decode, start pulse
src/rtl/posit_alu.vhd       opcode decode, result mux, done
src/rtl/posit_compare.vhd   EQ / LT
src/rtl/posit_bitwise.vhd   AND / OR / XOR
src/rtl/add.vhd             PositAdder   (FloPoCo, 1 pipeline stage)
src/rtl/mult.vhd            PositMult    (FloPoCo, 1 pipeline stage)
src/tb/aion_test.py         cocotb testbench, checked against Stillwater Universal
```
