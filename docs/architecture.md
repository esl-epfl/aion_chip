# AION — what is inside the chip, and how to drive it

AION is a Posit&lt;16,2&gt; arithmetic unit behind a byte-wide register interface.
Everything reachable from the outside goes through eight registers; everything
inside is combinational arithmetic plus one accumulator per MAC lane.

This document is the reference for the blocks and their programming model.
For the physical implementation see [`../implementation/README.md`](../implementation/README.md);
for the AI-cell flow see [`../scripts/flow/README.md`](../scripts/flow/README.md).

---

## 1. The whole chip

```
                        tt_um_aion  (350 x 200 um)
  ┌──────────────────────────────────────────────────────────────────────┐
  │                                                                      │
  │  clk ──┬───────────────────────────────────────────────────────┐     │
  │  rst_n ┼──┬────────────────────────────────────────────────┐   │     │
  │  ena ──╳  │  (read into `unused`; the design gates nothing on it) │  │
  │           │                                                │   │     │
  │        ┌──▼────────────────────┐        ┌──────────────────▼───▼──┐  │
  │ ui_in ─►                       │ opA[16]│                         │  │
  │  [8]   │    aion_interface     ├───────►│       posit_alu         │  │
  │        │                       │ opB[16]│                         │  │
  │ uio_in ►  8 x 8-bit registers  ├───────►│  ┌───────────────────┐  │  │
  │  [8]   │  address decode       │ opcode │  │  posit_mac_array  │  │  │
  │        │  start pulse          ├───────►│  │   N x mac_lane    │  │  │
  │ uo_out ◄  read mux             │ lane[3]│  │   (mult + adder)  │  │  │
  │  [8]   │                       ├───────►│  └───────────────────┘  │  │
  │        │                       │ start  │  ┌──────────────────┐   │  │
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
| `clk` | in | 1 | single clock, 40 ns signoff period |
| `rst_n` | in | 1 | asynchronous, active low |
| `ena` | in | 1 | required by the harness; **not used** — read into a signal called `unused` |
| `ui_in` | in | 8 | `[2:0]` register address, `[7]` direction (1 = write) |
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

Eight byte registers, addressed by `ui_in[2:0]`.

| Addr | Name | Access | Contents |
| --- | --- | --- | --- |
| `0x0` | `opA_lo` | R/W | operand A, bits 7:0 |
| `0x1` | `opA_hi` | R/W | operand A, bits 15:8 |
| `0x2` | `opB_lo` | R/W | operand B, bits 7:0 |
| `0x3` | `opB_hi` | R/W | operand B, bits 15:8 |
| `0x4` | `control` | R/W | command — see below |
| `0x5` | `result_lo` | R | result, bits 7:0 |
| `0x6` | `result_hi` | R | result, bits 15:8 |
| `0x7` | `status` | R | bit 0 = `done`, bits 7:1 = 0 |

### `control` (`0x4`)

```
   bit   7     6     5     4     3     2     1     0
       ┌─────┬───────────────────┬───────────────────────┐
       │START│    MAC lane sel   │        opcode         │
       └─────┴───────────────────┴───────────────────────┘
         [7]        [6:4]                  [3:0]
```

* **`[3:0]` opcode** — which block drives `result` (table in §4).
* **`[6:4]` lane** — which MAC lane `MAC_LOAD` writes and `MAC_READ` reads.
  Ignored by every non-MAC opcode. Three bits, so eight lanes are addressable;
  how many exist is the `MAC_LANES` generic.
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
Everything except the MAC accumulators is **combinational**: `result` is valid
as soon as the operand and control registers have settled. `done` is not a
computation-complete signal — it is a fixed acknowledgement delay, so software
has one thing to poll regardless of opcode.

| Opcode | Name | Block | `result` | `done` after |
| --- | --- | --- | --- | --- |
| `0000` | ADD | MAC lane 0 adder (bypassed) | `opA + opB` | 2 cycles |
| `0001` | MULT | MAC lane 0 multiplier (bypassed) | `opA × opB` | 2 cycles |
| `0010` | EQ | `posit_compare` | `0x0001` if equal, else `0x0000` | 2 cycles |
| `0011` | LT | `posit_compare` | `0x0001` if `opA < opB`, else `0x0000` | 2 cycles |
| `0100` | AND | `posit_bitwise` | `opA & opB` | 2 cycles |
| `0101` | OR | `posit_bitwise` | `opA \| opB` | 2 cycles |
| `0110` | XOR | `posit_bitwise` | `opA ^ opB` | 2 cycles |
| `1000` | MAC_LOAD | `posit_mac_array` | accumulator of lane `sel` | 3 cycles |
| `1001` | MAC_RUN | `posit_mac_array` | accumulator of lane `sel` | 3 cycles |
| `1010` | MAC_CLEAR | `posit_mac_array` | accumulator of lane `sel` | 3 cycles |
| `1011` | MAC_READ | `posit_mac_array` | accumulator of lane `sel` | 3 cycles |
| others | — | — | `0x0000` | 2 cycles |

Add and multiply have no adder or multiplier of their own: they **bypass into
MAC lane 0**, which holds the only `PositAdder` and `PositMult` in the chip.
That is what makes one lane affordable — see §6.

### Why MAC commands take one cycle longer

`start` is combinational off `ui_in`/`uio_in`, so it is already high during the
very write that sets `control`. At that moment `opcode` still holds the
*previous* command, because `control` is a register. The combinational opcodes
do not care — they are only ever read after `done`. A MAC command does: it
decodes a pulse from the opcode, so it uses the **registered** start, one cycle
later, when the pulse and its own opcode finally agree.

```
edge      1              2               3
        write        prod_reg        acc_reg <= prod_reg + acc
        control      <= x * y                         done = 1
        start_d1=1   start_d2=1
```

Non-MAC opcodes raise `done` at edge 2, MAC opcodes at edge 3.

---

## 5. Blocks

### `aion_interface` — register file

Address decode, the eight registers, the start pulse and the read multiplexer.
Reset (`rst_n` low, asynchronous) clears `opA`, `opB` and `control`; the result
and status registers are wires, not state.

### `PositAdder` / `PositMult`

FloPoCo-generated Posit&lt;16,2&gt; units (`src/rtl/add.vhd`, `src/rtl/mult.vhd`),
**pipeline depth 0** — purely combinational despite carrying a `clk` port.
Together they are the longest path in the chip, which is why a MAC lane never
puts them in the same cycle and why `CLOCK_PERIOD` is 40 ns.

| Unit | Standard-cell area |
| --- | --- |
| `PositAdder` | 8,850 µm² |
| `PositMult` | 19,781 µm² |

### `posit_compare`

Posits use two's-complement signed ordering, so comparison is a signed integer
compare on the raw 16 bits. `result` is `0x0001` or `0x0000` — a flag in bit 0,
not a posit.

### `posit_bitwise`

Bitwise `and` / `or` / `xor` on the raw encodings. No posit semantics; useful
for masking and sign manipulation.

### `posit_mac_lane`

One lane is a self-contained multiply-accumulate unit: its own operand
registers, its own multiplier and adder, its own accumulator.

```
                 bypass                       bypass
  bus_x ─┬──────────┐                 bus_x ─┬───────────┐
         │        ┌─▼─┐                      │         ┌─▼─┐
      ┌──▼──┐     │mux├──┐                ┌──▼──────┐  │mux├──┐
      │x_reg│─────►   │  │                │prod_reg │──►   │  │
      └──▲──┘     └───┘  │   ┌─────────┐  └────▲────┘  └───┘  │  ┌──────────┐
        load             ├──►│PositMult├───┬───┘              ├─►│PositAdder│
                         │   └─────────┘   │                  │  └────┬─────┘
      ┌─────┐     ┌───┐  │      (comb)     │  ┌────────┐ ┌───┐│   (comb)
      │y_reg│─────►mux├──┘                 │  │acc_reg │─►mux├┘       │
      └──▲──┘     └─▲─┘                    │  └───▲────┘ └─▲─┘        │
        load        │                      │      │        │          │
  bus_y ─┴──────────┘                      │      └────────┴──────────┘
                                           │         run_d (stage 2)
                                     run (stage 1)
```

* **`load`** latches `bus_x`/`bus_y` into this lane's operand registers.
* **`run`** registers the product (stage 1); the cycle after, the sum lands in
  the accumulator (stage 2). Splitting them is what keeps the multiplier and
  adder out of the same cycle.
* **`clear`** zeroes the accumulator, and **beats `run`** — a clear issued
  during an accumulate leaves the lane at zero, not at half a result.
* **`bypass`** routes the bus straight through both units, which is how ADD and
  MULT are served without a second adder and multiplier existing anywhere.

### `posit_mac_array`

`MAC_LANES` lanes **in parallel**. There is no signal from lane *i* to lane
*i+1*: no cascade, no adder tree. The only per-lane signal is `load`.

```
bus_x, bus_y ──┬──────────────┬──────────────┬───────  broadcast
run, clear ────┼──────────────┼──────────────┼───────  broadcast
bypass ────────┼──────────────┼──────────────┼───────  broadcast
               │              │              │
   load&(sel=0)│  load&(sel=1)│  load&(sel=2)│
          ┌────▼────┐    ┌────▼────┐    ┌────▼────┐
          │ lane 0  │    │ lane 1  │    │ lane 2  │   ...
          └──┬───┬──┘    └────┬────┘    └────┬────┘
   prod0,sum0│   │ acc0       │ acc1         │ acc2
      to ALU─┘   └────────────┴──────────────┴──► read mux ──► result
                                  (sel)
```

The only cascade is **in time, inside a lane**: `acc_reg` feeds back into its
own adder, so a lane accumulates its own stream across successive `RUN`s.

`RUN` fires **every** lane at once, so N lanes are N independent running sums —
not one N-element dot product. A length-N dot product is either N lanes plus a
final sum in software, or one lane and N successive `RUN`s (which needs no
final sum at all).

---

## 6. `MAC_LANES` — the size knob

A VHDL generic on `tt_um_aion`, defaulting to **1**:

```vhdl
-- src/rtl/tt_um_aion.vhd
MAC_LANES : positive := 1
```

`MAC_LANES=1` is the design as it was before the array existed: lane 0 holds
the only adder and multiplier, and ADD/MULT bypass into it. Every further lane
is a whole `PositMult` + `PositAdder`.

| Lanes | Cell area | Utilisation in 350 × 200 | Box needed |
| ---: | ---: | ---: | --- |
| 1 | 36,631 µm² | 55% | **350 × 200** ✓ |
| 2 | 71,066 µm² | 107% | ~450 × 265 |
| 3 | 105,501 µm² | 159% | ~545 × 325 |
| 4 | 139,936 µm² | 211% | ~630 × 370 |

**A 350 × 200 box holds one lane.** More lanes need `DIE_AREA` in
`implementation/config.json` to grow with them; nothing checks that they agree,
and global placement will simply fail.

Changing it:

* **simulation** — edit the default in `tt_um_aion.vhd`. FuseSoC cannot pass it
  (its GHDL backend rejects `paramtype: generic`), and the testbench does not
  need telling: `test_mac_lanes_are_independent` discovers how many lanes exist,
  because a lane that was never built reads back zero;
* **hardening** — add the generic to `GHDL_ARGUMENTS` in `config.json`:
  `"--std=08 -fsynopsys -fexplicit -gMAC_LANES=2"`.

Why lanes exist at all: they are structurally identical, so N lanes multiply the
number of sites each mined AI cell covers without adding one new cell type to
draw. For the arithmetic they buy throughput and nothing more.

---

## 7. How to drive it

Every command is the same five steps. `write(a, d)` means drive
`ui_in = 0x80 | a`, `uio_in = d` across one rising edge; `read(a)` means drive
`ui_in = a` and sample `uo_out`.

```
  write(0x0, opA & 0xFF)      write(0x2, opB & 0xFF)
  write(0x1, opA >> 8)        write(0x3, opB >> 8)
  write(0x4, 0x80 | (lane << 4) | opcode)
  poll  read(0x7) until bit 0 is set          -- done
  read(0x5), read(0x6)                        -- result, low then high
```

### Arithmetic, compare, bitwise

```
  add:      write(0x4, 0x80 | 0x0)
  multiply: write(0x4, 0x80 | 0x1)
  equal:    write(0x4, 0x80 | 0x2)     -> result bit 0
  less:     write(0x4, 0x80 | 0x3)     -> result bit 0
  and/or/xor: 0x4 / 0x5 / 0x6
```

Operands and results are raw Posit&lt;16,2&gt; encodings. Compare returns a flag
in bit 0, not a posit.

### A dot product on one lane

Serial, needs no final sum — the accumulator holds the whole result:

```
  write(0x4, 0x80 | 0xA)                 -- MAC_CLEAR
  for each (x, y):
      write opA = x, opB = y
      write(0x4, 0x80 | 0x8)             -- MAC_LOAD  into lane 0
      write(0x4, 0x80 | 0x9)             -- MAC_RUN
  read(0x5), read(0x6)                   -- the dot product
```

### A dot product across N lanes

Parallel, one `RUN` for all terms, but you sum the partials yourself:

```
  write(0x4, 0x80 | 0xA)                        -- MAC_CLEAR (all lanes)
  for lane i, term (x_i, y_i):
      write opA = x_i, opB = y_i
      write(0x4, 0x80 | (i << 4) | 0x8)         -- MAC_LOAD into lane i
  write(0x4, 0x80 | 0x9)                        -- MAC_RUN: every lane at once
  for lane i:
      write(0x4, 0x80 | (i << 4) | 0xB)         -- MAC_READ lane i
      read(0x5), read(0x6)
  sum the N partials in software
```

Every MAC opcode leaves the selected accumulator on `result`, so `MAC_LOAD`,
`MAC_RUN` and `MAC_CLEAR` are each also a read; `MAC_READ` exists for when you
want to look without disturbing anything.

### Reset

`rst_n` is asynchronous and active low. It clears the operand and control
registers and every lane's operand, product and accumulator registers. Hold it
low for a few cycles at power-up; the testbench uses five.

---

## 8. Physical

| | |
| --- | --- |
| Process | IHP SG13G2, 130 nm |
| Die | 350 × 200 µm, hardened as a macro |
| Clock | 40 ns signoff period (25 MHz) |
| Routing ceiling | TopMetal1 |
| Supplies | `VPWR` / `VGND` |

The design is hardened to be instantiated in a parent, not as a TinyTapeout
tile, but it keeps every TT *rule*: the `tt_um_*` port list including `ena`, the
supply names, `RT_MAX_LAYER: TopMetal1`, and `PDN_MULTILAYER: false` — which
keeps the power grid off TopMetal2, a layer TT's precheck forbids in a project
GDS. See [`../implementation/README.md`](../implementation/README.md).

The 40 ns period is set by one path: an operand register, through the whole
posit ALU, to `uo_out` — 121 cells with no register in it, because
`aion_interface` drives `uo_out` combinationally from `result`. Registering
`result` before the read multiplexer would take the ALU out of the output path
and buy most of that back.

---

## 9. Where things live

```
src/rtl/tt_um_aion.vhd      top level, TinyTapeout port list, MAC_LANES generic
src/rtl/aion_soc.vhd        interface + ALU
src/rtl/aion_interface.vhd  register file, address decode, start pulse
src/rtl/posit_alu.vhd       opcode decode, result mux, done
src/rtl/posit_mac.vhd       posit_mac_lane, posit_mac_array
src/rtl/posit_compare.vhd   EQ / LT
src/rtl/posit_bitwise.vhd   AND / OR / XOR
src/rtl/add.vhd             PositAdder   (FloPoCo, combinational)
src/rtl/mult.vhd            PositMult    (FloPoCo, combinational)
src/tb/aion_test.py         cocotb testbench, checked against Stillwater Universal
```
