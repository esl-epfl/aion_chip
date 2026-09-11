# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The two files every step downstream of synthesis
#                             needs once PDK_EXT is on
#
#  `make synth` maps against the PDK's cells *plus* the hand-designed ones
#  under implementation/pdk_extension/ (see that directory's README).  From
#  that moment the netlist can instantiate a cell no stock file describes, and
#  every tool that reads the netlist afterwards needs to be told about it.
#  Two tools, two formats, both written by `make pdk_ext_lib`:
#
#    flow/pdk_extension/lib/sg13g2_stdcell_aion_<corner>.lib
#        Liberty.  ABC maps against it (CELL_LIBS) and kepler-formal links
#        both netlists against it in step 3's LEC.
#
#    flow/pdk_extension/tech_dict/sg13g2_stdcell_aion.json
#        aion_opt's technology dictionary, derived from that Liberty by
#        scripts/merge_tech_dict.py.
#
#  Neither is optional, and neither fails loudly on its own:
#
#    * kepler-formal refuses to load a netlist holding a cell its Liberty does
#      not define -- "AION_mux2i_1 cannot be found in SNL while constructing
#      instance _07163_" -- which reads like a broken rewrite and is a missing
#      library.
#    * aion_opt *drops* an instance whose cell type is not in its dictionary
#      (io/yosys_json.py: "Skip blackboxes / macros not present in the tech
#      dictionary").  Step 3 then writes the netlist back out with all 698
#      AION_mux2i_1 instances gone and their outputs undriven.  Nothing says
#      so; the design simply stops computing the right answer.
#
#  So the rule this module exists to keep: a step that hands a netlist to
#  aion_opt or kepler-formal asks here first, and gets a file that is on disk.
# ================================================================

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, Optional

from . import paths
from .runner import StepFailed
from .style import info, note


class Views(NamedTuple):
    """What the extension cells are, to the tools that read the netlist.

    Both are None when there is nothing to say -- PDK_EXT off, or no cells
    under implementation/pdk_extension/ -- which is also when every stock
    default is the right one.
    """

    library: Optional[Path]     # Liberty, for ABC and the LEC
    tech_dict: Optional[Path]   # technology dictionary, for aion_opt
    cells: tuple = ()

    @property
    def active(self) -> bool:
        return self.library is not None


NONE = Views(None, None, ())


def views(ctx) -> Views:
    """The extension views for this run, built if they are not on disk.

    `make synth` writes both, so step 1 leaves them behind for everything
    after it.  They are rebuilt here anyway when missing, because a single
    step is run on its own more often than the whole chain is, and `make
    clean` deletes flow/pdk_extension/ without touching step 1's netlist.
    Splicing them again is deterministic and costs about a second.
    """
    if ctx.pdk_ext is not None:
        # Once per run, not per call: 3_rewrite alone asks twice, and the
        # second ask must not splice the library again or re-announce the
        # cells. The answer cannot change while the flow is running.
        return ctx.pdk_ext

    cfg = ctx.cfg
    cells = paths.pdk_ext_cells()
    if not cfg.PDK_EXT or not cells:
        ctx.pdk_ext = NONE
        return NONE

    library = paths.pdk_ext_lib()
    tech_dict = paths.pdk_ext_dict()
    missing = [p for p in (library, tech_dict) if not p.exists()]
    if missing and not ctx.runner.dry_run:
        for path in missing:
            info(f"no {paths.rel_to_project(path)} — splicing it")
        ctx.runner.check(ctx.runner.host("pdk_ext_lib", name="pdk_ext_lib"),
                         "pdk_ext_lib")
        for path in (library, tech_dict):
            if not path.exists():
                raise StepFailed(
                    f"`make pdk_ext_lib` did not write "
                    f"{paths.rel_to_project(path)}", 2)

    info(f"PDK extension: {len(cells)} cell(s) — {', '.join(cells)}")
    note(f"  Liberty    {paths.rel_to_project(library)}")
    note(f"  tech dict  {paths.rel_to_project(tech_dict)}")
    ctx.pdk_ext = Views(library, tech_dict, tuple(cells))
    return ctx.pdk_ext
