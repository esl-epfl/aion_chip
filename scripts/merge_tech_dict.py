#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Splice PDK-extension cells into the technology
#                             dictionary aion_opt reads
#
#  merge_lib.py does this for the *mapper*: the PDK Liberty plus the cells
#  under implementation/pdk_extension/, in one library block, so ABC may pick
#  one.  This does the same for *aion_opt*, which does not read Liberty -- it
#  reads tech/tech_dict/sg13g2_stdcell.json, a flat
#
#      {"cells": {<name>: {"area": float,
#                          "pins": {<pin>: "input"|"output"},
#                          "function": "<liberty expression>"}}}
#
#  and that file is load-bearing in a way its name does not suggest.
#  `load_yosys_json()` drops any instance whose cell type is not in it:
#
#      if cell_lib is not None and cell_type not in cell_lib:
#          # Skip blackboxes / macros not present in the tech dictionary.
#          continue
#
#  (aion_flow/tools/aion_opt/aion_opt/io/yosys_json.py).  That is right for a
#  macro and catastrophic for a standard cell: once `make synth` maps against
#  the extended Liberty, the netlist instantiates AION_mux2i_1 -- a cell the
#  base dictionary has never heard of -- and step 3 writes it back out with
#  every one of those instances silently gone and their output nets left
#  undriven.  The netlist still elaborates, and every gate-level simulation of
#  it fails on values that look like logic bugs.
#
#  There is a second reason beyond survival.  The Yosys JSON of a netlist
#  carries no `port_directions` for a module it has no definition of, so
#  aion_opt takes a blackbox's pin directions from this dictionary too -- it
#  cannot tell the cell's output from its inputs without it.
#
#  So the dictionary is derived from the merged Liberty rather than from the
#  cells' own files: the one file that says what the mapper actually chose
#  from is the one the miner and the rewriter have to agree with.
#
#      make pdk_ext_lib                    writes both, in that order
#      scripts/merge_tech_dict.py          just this one, from the typ library
# ================================================================

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from merge_lib import cell_blocks, pin_blocks

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_BASE = (PROJECT_ROOT / "aion_flow" / "tech" / "tech_dict"
                / "sg13g2_stdcell.json")
DEFAULT_LIB = (PROJECT_ROOT / "flow" / "pdk_extension" / "lib"
               / "sg13g2_stdcell_aion_typ_1p20V_25C.lib")
DEFAULT_OUT = (PROJECT_ROOT / "flow" / "pdk_extension" / "tech_dict"
               / "sg13g2_stdcell_aion.json")

_AREA = re.compile(r"^\s*area\s*:\s*([-\d.eE+]+)\s*;", re.MULTILINE)
_DIRECTION = re.compile(r'^\s*direction\s*:\s*"?(\w+)"?\s*;', re.MULTILINE)
_FUNCTION = re.compile(r'^\s*function\s*:\s*"(.*?)"\s*;', re.MULTILINE | re.DOTALL)


def entry(cell_text: str) -> dict:
    """The dictionary entry for one Liberty `cell (...)` block.

    `function` is copied across verbatim: the dictionary and Liberty use the
    same expression syntax (`!I2*!I0+I2*!I1`), which is what makes this a
    transcription rather than a translation.  A cell with several outputs
    keeps the first one's function, because the dictionary has room for one --
    the same limitation the base file already lives with.
    """
    area = _AREA.search(cell_text)
    pins: dict = {}
    function = None
    for name, block in pin_blocks(cell_text):
        direction = _DIRECTION.search(block)
        pins[name] = direction.group(1) if direction else "input"
        expression = _FUNCTION.search(block)
        if expression and function is None and pins[name] == "output":
            function = " ".join(expression.group(1).split())

    info = {"area": float(area.group(1)) if area else 0.0, "pins": pins}
    if function is not None:
        info["function"] = function
    return info


def merge(base: dict, lib_text: str) -> "tuple[dict, list[str]]":
    """Add every cell the Liberty has and the dictionary does not.

    Which is exactly the extension cells: the Liberty this reads is the PDK's
    own with them spliced in, so anything missing from the dictionary is
    something merge_lib.py added.  Existing entries are never touched -- the
    base dictionary is the PDK's own description of its cells and a Liberty
    re-read is not an improvement on it.
    """
    merged = json.loads(json.dumps(base))
    cells = merged.setdefault("cells", {})
    added: list = []
    for name, text in cell_blocks(lib_text):
        if name in cells:
            continue
        cells[name] = entry(text)
        added.append(name)
    return merged, added


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Splice the PDK-extension cells into the technology "
                    "dictionary aion_opt mines and rewrites with.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  scripts/merge_tech_dict.py\n"
               "  scripts/merge_tech_dict.py --lib flow/pdk_extension/lib/"
               "sg13g2_stdcell_aion_slow_1p08V_125C.lib\n")
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE,
                        help="technology dictionary to extend "
                             f"(default: {DEFAULT_BASE.name})")
    parser.add_argument("--lib", type=Path, default=DEFAULT_LIB,
                        help="merged Liberty to read the new cells from "
                             "(default: the typ one merge_lib.py writes)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"output path (default: "
                             f"{DEFAULT_OUT.relative_to(PROJECT_ROOT)})")
    args = parser.parse_args(argv)

    if not args.base.exists():
        raise SystemExit(f"error: no technology dictionary at {args.base}")
    if not args.lib.exists():
        raise SystemExit(
            f"error: no merged Liberty at {args.lib}\n"
            f"       run `make pdk_ext_lib` first — this file is derived from "
            f"it, so that the miner and the mapper cannot disagree about what "
            f"the technology holds.")

    base = json.loads(args.base.read_text())
    merged, added = merge(base, args.lib.read_text())
    if not added:
        print(f"  {args.lib.name} adds no cell the dictionary does not have")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2) + "\n")

    for name in added:
        info = merged["cells"][name]
        print(f"  {name}: area {info['area']}  "
              f"{', '.join(f'{p}={d[0]}' for p, d in info['pins'].items())}  "
              f"{info.get('function', 'NO FUNCTION')}")
    print(f"\n  base   {args.base.relative_to(PROJECT_ROOT)}"
          f"  ({len(base.get('cells', base))} cells)")
    print(f"  from   {args.lib.relative_to(PROJECT_ROOT)}")
    print(f"  added  {', '.join(added) if added else '—'}")
    print(f"  wrote  {args.out.relative_to(PROJECT_ROOT)}")
    print("\n  point aion_opt at it:")
    print(f"    make aion-opt-rewrite CELL_LIB="
          f"{args.out.relative_to(PROJECT_ROOT)} ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
