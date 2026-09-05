# Physical implementation output

Everything the LibreLane targets produce. One directory per target, each wiped
and rewritten in full by the target that owns it:

```
synth.core        \
pnr.core           >  fixed FuseSoC handles on the directories below
pnr_simple.core   /
synth/            output of `make synth`       (git-ignored)
pnr/              output of `make pnr`         (git-ignored)
pnr_simple/       output of `make pnr_simple`  (git-ignored)
```

The inputs live in `../implementation/`: the config template, the SDC, the pin
order and the AI-generated cells.

## Why the .core files

The simulation targets in `aion.core` need to name a netlist, and the netlist
only exists after the flow has run. Each `.core` here is a one-fileset FuseSoC
core pointing at a fixed path inside its directory, so `aion.core` can depend on
`epfl:aion:flow_pnr_simple:1.0.0` instead of hard-coding a path that may not
exist yet. Depend on one before its target has run and FuseSoC reports the
missing file; the Makefile checks for it first and says which target to run.

| Core                            | File it points at                       |
| ------------------------------- | --------------------------------------- |
| `epfl:aion:flow_synth:1.0.0`      | `synth/nl/tt_um_aion.nl.v`            |
| `epfl:aion:flow_pnr:1.0.0`        | `pnr/nl/tt_um_aion.nl.v`              |
| `epfl:aion:flow_pnr_simple:1.0.0` | `pnr_simple/nl/tt_um_aion.nl.v`       |

`nl/` is the logical netlist and the one to simulate. `pnl/` next to it is the
same netlist with the power pins connected (`VPWR`/`VGND`), which the PDK
simulation models do not declare — it is for LVS, not for simulation.

## What each directory holds

`make` copies the `final/` views of the last run, plus its logs and every
report, so the layout follows LibreLane's:

```
nl/        logical gate-level netlist
pnl/       powered netlist (LVS)
def/ odb/  placed-and-routed database
gds/       streamed-out layout
sdf/       one directory per corner, consumed by `make post_pnr_sim`
spef/      parasitics
lib/       timing model of the block itself
reports/   every .rpt of the run, flattened one level
logs/      flow.log and resolved.json
metrics.*  the run's metrics, csv and json
```

Only `pnr/` and `pnr_simple/` carry `sdf/`; `synth/` stops before placement and
has nothing to annotate.
