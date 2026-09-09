# PDK extension cells

Cells that are **designed**, not mined. The nine-step flow finds recurring
subgraphs in a synthesized netlist and merges them; the cells here exist for
the opposite reason — the PDK's own implementation of a function is not the
best one available, and a hand design beats it.

`AION_mux2i_1` is the first: IHP builds `mux2` the only way complementary
CMOS can (select inverter, AOI22 core, output inverter — 12 devices), and a
pair of transmission gates does the same selection in 4. XOR, XNOR and MUX
are **39% of `tt_um_aion`'s standard-cell area**, and they are exactly the
functions static CMOS is worst at.

```
implementation/pdk_extension/
  <CELL>/
    <CELL>.v                 gold gate-level reference -- what it must compute
    <CELL>.spice             the hand-designed transistor implementation
    <CELL>.provisional.lib   Liberty for the mapper, before the cell is drawn
    FLOORPLAN.md             the placement/routing plan for the layout stage
  views/<CELL>/              published by the flow script  (git-ignored)
```

## Building one

```bash
docker start iic-osic-tools_shell_uid_1000
scripts/pdk_cell.py AION_mux2i_1                 # all three stages
scripts/pdk_cell.py AION_mux2i_1 --from layout   # resume after drawing
scripts/pdk_cell.py AION_mux2i_1 --dry-run       # print commands, run none
```

| stage | what it proves |
|---|---|
| `characterize` | ngspice runs **gold, reference and custom** over every input vector and they agree. Gold is the truth table read out of the PDK Liberty, reference is the PDK cell's own transistors, custom is `<CELL>.spice`. A 2-way pass would only say the PDK agrees with itself. |
| `layout` | the GDS built from the cell generator is Magic- and KLayout-DRC clean and LVS-clean against that same SPICE. No model is invoked — the generator is a source file in this repository. |
| `post-layout` | Magic PEX, the abutted PDK baseline, Liberty characterized from the *extracted* netlist, the area/delay comparison and the view export. Every number is measured from the drawn cell. |

The layout stage never calls an agent. If the generator does not exist it
scaffolds one and stops; you finish it by hand and re-run. That is the same
contract as the main flow's `DRAW_MODE=manual`.

## Getting the mapper to use it

**`EXTRA_LIBS` will not do it.** LibreLane reads those with
`read_liberty -lib -ignore_miss_dir -setattr blackbox`
(`librelane/steps/yosys.py:148`), which registers the cell for STA and
hierarchy and hides it from the technology mapper. `--cells-dir`, which is
how `make pnr` wires the mined AION cells in, goes down that same path.

The list ABC actually maps against is `CELL_LIBS` (deprecated alias `LIB`),
read at `librelane/steps/pyosys.py:296`. So the cell has to be **merged into
the standard-cell Liberty that variable points at** — the PDK's cells plus
this one, in a single `library (...) { ... }` block. Concatenating two `.lib`
files does *not* do that: it produces two library blocks and yosys reads the
first and ignores the rest, without saying so.

```bash
scripts/merge_lib.py --provisional     # -> flow/pdk_extension/lib/
```

It splices each extension cell's `cell (...) { ... }` block inside the base
library's closing brace, carries across any lookup-table template the base
does not already define, refuses on a name collision, and prefers a
characterized `.lib` over a provisional one so a stale estimate cannot
quietly outrank a measurement.

Verified on a 32-bit 2:1 mux:

```
PDK-only:   32  580.608   sg13g2_mux2_1
PDK+AION:   32  522.547   AION_mux2i_1     (-10.0%)
```

Nothing was done to force that. No `SYNTH_EXCLUDED_CELL_FILE` entry banning
the PDK mux2, no custom ABC script, no `dont_use` list — ABC compared
`area : 16.3296` against `area : 18.1440` and took the smaller one.

Which makes the Liberty's `area` the load-bearing number in this whole
exercise. It is currently a floorplan estimate, and if the drawn cell comes
out bigger then ABC will have made that choice on a number that was wrong.
Re-run the merge after `post-layout` and the measured `.lib` takes over.
