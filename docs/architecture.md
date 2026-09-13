# AION — what is inside the chip, and how to drive it

AION is a posit arithmetic unit behind a byte-wide register interface, working
at Posit&lt;32,2&gt; or Posit&lt;16,2&gt; a command at a time. Everything
reachable from the outside goes through fourteen registers; everything inside
is an adder and a multiplier per width, plus one comparator and one bitwise
unit shared between them.

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
  │  [8]   │  address decode       │ opcode │  │ PositAdder   x32 │   │  │
  │        │  start pulse          ├───────►│  ├──────────────────┤   │  │
  │ uo_out ◄  read mux             │ precis.│  │ PositMult    x32 │   │  │
  │  [8]   │                       ├───────►│  ├──────────────────┤   │  │
  │        │                       │ start  │  │ PositAdder16 x16 │   │  │
  │        │                       ├───────►│  ├──────────────────┤   │  │
  │        │                       │        │  │ PositMult16  x16 │   │  │
  │        │                       │        │  ├──────────────────┤   │  │
  │        │                       │        │  │  posit_compare   │   │  │
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
is four bytes, so the map needs fourteen addresses where a
Posit&lt;16,2&gt;-only design needed eight — which is why the address field is
four bits wide and not three. Operands and results are little-endian: byte 0 is
bits 7:0.

**The map does not change with the precision.** A Posit&lt;16,2&gt; operand goes
into the low two bytes of the same registers (`0x0`–`0x1`, `0x4`–`0x5`) and its
result comes back from the low two bytes of `result` (`0x9`–`0xA`); the upper
two of each are simply not part of a narrow command. Writing them is harmless
and reading them back is defined — `result[31:16]` reads `0x00` after a
Posit&lt;16,2&gt; command, never the leftovers of a wide one.

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
       ┌─────┬─────────────┬─────┬───────────────────────┐
       │START│   unused    │ PRE │        opcode         │
       └─────┴─────────────┴─────┴───────────────────────┘
         [7]      [6:5]      [4]            [3:0]
```

* **`[3:0]` opcode** — which block drives `result` (table in §4).
* **`[4]` precision** — the operand width this command means: `0` =
  Posit&lt;32,2&gt;, `1` = Posit&lt;16,2&gt;. It is orthogonal to the opcode —
  every opcode means the same operation at either width — which is why it is a
  bit of its own rather than a second half of the opcode table.
* **`[6:5]`** — unused. This field held the MAC lane select before the array
  was removed; it is stored in `control` and read back, but nothing decodes it.
* **`[7]` start** — writing `control` with this bit set fires the command.
  It is a trigger, not a stored level: the pulse lasts one cycle whatever you
  leave on the bus.

`precision` is not latched anywhere: it runs from `control[4]` straight into
the ALU's output multiplexer, so a width change takes effect with the command
that carries it and needs no settling command in front of it.

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

`posit_alu` is a decoder, six arithmetic blocks and one output multiplexer.

| Opcode | Name | Block (`control[4]`=0 / =1) | `result` |
| --- | --- | --- | --- |
| `0000` | ADD | `PositAdder` / `PositAdder16` | `opA + opB` |
| `0001` | MULT | `PositMult` / `PositMult16` | `opA × opB` |
| `0010` | EQ | `posit_compare` | `1` if equal, else `0` |
| `0011` | LT | `posit_compare` | `1` if `opA < opB`, else `0` |
| `0100` | AND | `posit_bitwise` | `opA & opB` |
| `0101` | OR | `posit_bitwise` | `opA \| opB` |
| `0110` | XOR | `posit_bitwise` | `opA ^ opB` |
| others | — | — | `0x00000000` |

`control[4]` picks the operand width, not the operation: the table above is the
same at both precisions. What it changes is which pair of arithmetic units
drives `result`, and what the other three blocks are handed.

### What each block sees at Posit&lt;16,2&gt;

The operand registers are 32 bits wide whatever the command means, and nothing
clears bits 31:16 when the precision changes — after any Posit&lt;32,2&gt;
command they still hold that command's operands. So each block conditions the
low half itself, and the three of them want different things:

| Block | At Posit&lt;16,2&gt; it is given |
| --- | --- |
| `PositAdder16` / `PositMult16` | `opA[15:0]`, `opB[15:0]` — wired straight, 16 bits wide |
| `posit_compare` | the low half **sign-extended** to 32 bits |
| `posit_bitwise` | the low half **zero-masked** to 32 bits |

Compare and bitwise stay one 32-bit unit each — neither is duplicated — and
the two extensions are what lets them serve both widths:

* **compare sign-extends** because it is a signed compare on the raw encoding.
  `0xFFFF` is Posit&lt;16,2&gt; -minpos and has to order *below* zero;
  zero-extending it to `0x0000FFFF` would make it the largest value in the
  word instead. Sign extension preserves the whole ordering, so one comparator
  answers for both widths;
* **bitwise zero-masks** because an unmasked `AND` would return whatever a
  previous Posit&lt;32,2&gt; command left above bit 15. Masking first also
  makes the answer the same shape as every other narrow result.

`result[31:16]` is zero after every Posit&lt;16,2&gt; command — the add and
multiply outputs go through a width multiplexer that clears it, and the
comparator and the masked bitwise unit produce nothing up there to begin with.

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
Posit&lt;16,2&gt; pair beside them is purely combinational. That stage fills
on the `control` edge itself, because the operand registers cannot change on
that edge: only one register is written per edge, so they have been stable for
a full cycle by then. What is left is the combinational tail of the arithmetic
and the result multiplexer, which needs the rest of that cycle — hence the
answer is stable one edge later, and that is where `done` rises.

`PositAdder16`, `PositMult16`, `posit_compare` and `posit_bitwise` are wholly
combinational and settle sooner; the same edge covers them. **One handshake
serves both precisions** — a Posit&lt;16,2&gt; command is answered on the same
edge a Posit&lt;32,2&gt; one is, and no narrow command is charged a cycle it
does not need, because the wide pipeline stage was already hidden inside the
acknowledgement. Re-pipelining any of the four FloPoCo units means raising
`ALU_LATENCY` to match the deepest one.

---

## 5. Blocks

### `aion_interface` — register file

Address decode, the fourteen registers, the start pulse and the read
multiplexer. Reset (`rst_n` low, asynchronous) clears `opA`, `opB` and
`control`; the result and status addresses are wires into `posit_alu`, not
registers of their own.

Only `ui_in[3:0]` and `ui_in[7]` are read. An address in `0xE`–`0xF` reads
`0x00` rather than aliasing onto a real register.

### `PositAdder` / `PositMult` / `PositAdder16` / `PositMult16`

Four FloPoCo-generated units: the Posit&lt;32,2&gt; pair (`src/rtl/add.vhd`,
`src/rtl/mult.vhd`), **pipeline depth 1**, and the Posit&lt;16,2&gt; pair
(`src/rtl/add_16.vhd`, `src/rtl/mult_16.vhd`), **pipeline depth 0**. Together
they are the whole area budget of the chip, and the wide pair is its longest
path, which is why `CLOCK_PERIOD` is 50 ns.

All four run unconditionally on every command and the answer is picked
afterwards. Gating the idle pair off would buy nothing: the operand registers
hold still between commands, so a pair that is not selected does not toggle
anyway, and an enable would sit on the critical path of the one that is.

| Unit | Standard-cell area |
| --- | ---: |
| `PositAdder` | 21,558 µm² |
| `PositMult` | 61,774 µm² |
| `PositAdder16` | 9,204 µm² |
| `PositMult16` | 16,684 µm² |

Measured with `yosys … stat -liberty sg13g2_stdcell_typ_1p20V_25C` on each unit
alone, flattened. Doubling the word width costs the multiplier a factor of 3.7:
the Posit&lt;16,2&gt; multiplier fits one DSP-shaped block (`DSPBlock_13x13_*`
in `mult_16.vhd`), while the Posit&lt;32,2&gt; one is a 29 × 29 partial-product
array with a compressor tree (`Compressor_*`, `DSPBlock_*`, `IntAdder_38_*` in
`mult.vhd`).

#### Why the sub-entities carry `_add`, `_mult`, `_add16` and `_mult16` suffixes

FloPoCo numbers its operators per generated file, so every one of the four
files contains a `PositFastDecoder_*_F50_uid4` and a `Normalizer_ZO_*_F50_uid6`
— different entities under identical names, and the narrow pair collides with
each other exactly as the wide pair does. All four are analysed into the same
`work` library, where the second definition would silently replace the first.
Every sub-entity is therefore suffixed with its file's tag after generation;
only the four top levels keep readable names, and those were disambiguated by
hand — FloPoCo emits both adders as `PositAdder` and both multipliers as
`PositMult`, so the narrow pair was renamed to `PositAdder16` / `PositMult16`.
Regenerating any file from FloPoCo means redoing the rename.

### `posit_compare`

Posits use two's-complement signed ordering, so comparison is a signed integer
compare on the raw bits. `result` is `1` or `0` — a flag in bit 0, not a posit.

One 32-bit unit serves both precisions: a Posit&lt;16,2&gt; operand reaches it
sign-extended, which preserves the ordering exactly (see §4). It is not
duplicated for the narrow mode.

### `posit_bitwise`

Bitwise `and` / `or` / `xor` on the raw encodings. No posit semantics; useful
for masking and sign manipulation.

Also one 32-bit unit for both precisions, but fed the *masked* low half at
Posit&lt;16,2&gt; rather than the sign-extended one — see §4 for why the two
units need opposite extensions.

---

## 6. How to drive it

Every command is the same four steps. `write(a, d)` means drive
`ui_in = 0x80 | a`, `uio_in = d` across one rising edge; `read(a)` means drive
`ui_in = a` and sample `uo_out`.

**Posit&lt;32,2&gt;** — four operand bytes, four result bytes:

```
  write(0x0..0x3, opA byte 0..3)      -- low byte first
  write(0x4..0x7, opB byte 0..3)
  write(0x8, 0x80 | opcode)
  poll  read(0xD) until bit 0 is set          -- done; cleared by the write
  read(0x9..0xC)                              -- result, low byte first
```

**Posit&lt;16,2&gt;** — the same sequence with two bytes each and `0x10` in
`control`:

```
  write(0x0..0x1, opA byte 0..1)      -- low byte first
  write(0x4..0x5, opB byte 0..1)
  write(0x8, 0x80 | 0x10 | opcode)            -- bit 4 selects Posit<16,2>
  poll  read(0xD) until bit 0 is set
  read(0x9..0xA)                              -- result, low byte first
```

Bytes 2 and 3 of each operand are not written and not read: the ALU takes only
bits 15:0, whatever a previous wide command left above them. Reading `0xB`–`0xC`
anyway is defined and returns `0x00`.

### Arithmetic, compare, bitwise

```
  add:        write(0x8, 0x80 | P | 0x0)
  multiply:   write(0x8, 0x80 | P | 0x1)
  equal:      write(0x8, 0x80 | P | 0x2)     -> result bit 0
  less:       write(0x8, 0x80 | P | 0x3)     -> result bit 0
  and/or/xor: write(0x8, 0x80 | P | 0x4 / 0x5 / 0x6)

  where P = 0x00 for Posit<32,2>, 0x10 for Posit<16,2>
```

Operands and results are raw posit encodings at the width the command names.
Compare returns a flag in bit 0, not a posit, at either width.

Commands at the two widths can follow each other directly — `control[4]` is
not a mode the ALU latches, so there is nothing to switch and nothing to drain
between them.

### Reset

`rst_n` is asynchronous and active low. It clears the operand and control
registers — so the precision select comes out of reset at `0`, Posit&lt;32,2&gt;.
The wide adder's and multiplier's pipeline registers are not reset — they are
refilled from the operand registers on the first edge after reset, which is
many edges before any result is read; the narrow pair has no registers to
refill. Hold `rst_n` low for a few
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
| Cell area | 89,140 µm² over 7,744 instances (Posit&lt;32,2&gt; only) |

The design is hardened to be instantiated in a parent, not as a TinyTapeout
tile, but it keeps every TT *rule*: the `tt_um_*` port list including `ena`, the
supply names, `RT_MAX_LAYER: TopMetal1`, and `PDN_MULTILAYER: false` — which
keeps the power grid off TopMetal2, a layer TT's precheck forbids in a project
GDS. See [`../implementation/README.md`](../implementation/README.md).

Every number in this section is from the last hardening run, which was made
before the Posit&lt;16,2&gt; pair was added; that pair takes the cell area to
106,452 µm² over 9,622 instances (RTL synthesis, same corner). The floorplan
and the timing below have not been re-run against it.

The critical path moved when the arithmetic did. In a Posit&lt;16,2&gt;-only
design it ran from an operand register through the whole combinational ALU to
`uo_out`. Now the worst setup path is **register to register** — an operand
register in `aion_interface` (`opa[30]`) into a pipeline register inside the
adder — and it closes with 14.55 ns of slack at 50 ns in `nom_slow_1p08V_125C`.
One structural change is worth naming before that run: the narrow pair is
combinational, so it reintroduces an operand-register-to-`uo_out` path that the
wide pair does not have — the whole 16-bit adder, then the result and read
multiplexers. Whether it beats the wide adder's register-to-register path is an
STA question, not one to guess at; a `make pnr_simple` with both precisions is
what answers it. Setup is
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
src/rtl/posit_alu.vhd       opcode + precision decode, result mux, done
src/rtl/posit_compare.vhd   EQ / LT        (32-bit, both precisions)
src/rtl/posit_bitwise.vhd   AND / OR / XOR (32-bit, both precisions)
src/rtl/add.vhd             PositAdder    Posit<32,2> (FloPoCo, 1 pipeline stage)
src/rtl/mult.vhd            PositMult     Posit<32,2> (FloPoCo, 1 pipeline stage)
src/rtl/add_16.vhd          PositAdder16  Posit<16,2> (FloPoCo, combinational)
src/rtl/mult_16.vhd         PositMult16   Posit<16,2> (FloPoCo, combinational)
src/tb/aion_test.py         cocotb testbench, checked against Stillwater Universal
                            (every test parametrized over both precisions)
```
