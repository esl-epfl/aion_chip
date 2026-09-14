#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Splice PDK-extension cells into the standard
#                             cell Liberty the technology mapper reads
#
#  A Liberty file is one `library (NAME) { ... }` block holding the unit
#  declarations, the lookup-table templates and then every cell.  Adding a
#  cell means putting its `cell (...) { ... }` block *inside* that brace --
#  concatenating two .lib files does not do it, that produces two library
#  blocks and yosys reads the first one and ignores the rest, silently.
#
#  Why this exists at all.  LibreLane already has a way to hand extra cells
#  to the flow -- EXTRA_LIBS, which is what `--cells-dir` fills in -- but it
#  reads them as
#
#      read_liberty -lib -ignore_miss_dir -setattr blackbox <lib>
#
#  (librelane/steps/yosys.py:148).  That registers the cell for STA and for
#  hierarchy resolution and hides it from the technology mapper, which is
#  right for a macro and wrong for a standard cell.  The list ABC actually
#  maps against is CELL_LIBS (librelane/steps/pyosys.py:296), so a cell that
#  is to be *synthesized* has to be in the file that variable points at.
#
#  The templates matter.  A cell's timing tables reference lu_table_template
#  names by string, and a template that is not in the same library is a
#  dangling reference, so any template the extension defines and the base
#  does not is copied across too.
# ================================================================

from __future__ import annotations

import argparse
import re
from pathlib import Path

from collect_cells import LIB_CORNERS

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_BASE = (PROJECT_ROOT / "aion_flow" / "tech" / "lib"
                / "sg13g2_stdcell_typ_1p20V_25C.lib")
DEFAULT_EXT_ROOT = PROJECT_ROOT / "implementation" / "pdk_extension"
DEFAULT_OUT = PROJECT_ROOT / "flow" / "pdk_extension" / "lib"

#: Group headers whose blocks live directly under `library (...)`.
_TEMPLATE_KINDS = ("lu_table_template", "power_lut_template", "operating_conditions")


def _blocks(text: str, kind: str) -> "list[tuple[str, str]]":
    """Every `<kind> (name) { ... }` block, brace-matched, as (name, text).

    Liberty has no nesting rules a regex can be trusted with -- timing groups
    contain values with braces in them -- so the opening line is found by
    pattern and the extent by counting braces.
    """
    found = []
    for match in re.finditer(rf"^([ \t]*){re.escape(kind)}\s*\(\s*([^)]*?)\s*\)\s*\{{",
                             text, re.MULTILINE):
        start = match.start()
        depth = 0
        for i in range(text.index("{", start), len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    found.append((match.group(2), text[start:i + 1]))
                    break
    return found


def cell_blocks(text: str) -> "list[tuple[str, str]]":
    """Every `cell (NAME) { ... }` block in a Liberty, as (name, text).

    Public because scripts/merge_tech_dict.py reads the *merged* library this
    script writes: the technology dictionary aion_opt mines and rewrites with
    has to describe the same cells the mapper chose from, and there is exactly
    one file that says what those are.
    """
    return _blocks(text, "cell")


def pin_blocks(text: str) -> "list[tuple[str, str]]":
    """Every `pin (NAME) { ... }` block, brace-matched, as (name, text)."""
    return _blocks(text, "pin")


def _library_close(text: str) -> int:
    """Index of the `}` that closes the outer `library (...)` block."""
    open_at = text.index("{", re.search(r"\blibrary\s*\(", text).start())
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced braces: no closing brace for `library`")


def merge(base_text: str, extensions: "list[tuple[str, str]]",
          replace: bool = False) -> "tuple[str, list[str]]":
    """Return the merged Liberty and the names of the cells added."""
    have_cells = {name for name, _ in _blocks(base_text, "cell")}
    have_templates = {name for kind in _TEMPLATE_KINDS
                      for name, _ in _blocks(base_text, kind)}

    added: list[str] = []
    additions: list[str] = []
    for source, text in extensions:
        for kind in _TEMPLATE_KINDS:
            for name, block in _blocks(text, kind):
                if name not in have_templates:
                    have_templates.add(name)
                    additions.append(block)
        for name, block in _blocks(text, "cell"):
            if name in have_cells:
                if not replace:
                    raise SystemExit(
                        f"error: {source} defines cell {name!r}, which the base "
                        f"library already has.\n"
                        f"       Two cells of the same name make the mapper's "
                        f"choice depend on read order.\n"
                        f"       Rename the extension cell, or pass --replace "
                        f"to drop the base one.")
                base_text = base_text.replace(
                    dict(_blocks(base_text, "cell"))[name], "", 1)
            have_cells.add(name)
            added.append(name)
            additions.append(block)

    if not additions:
        return base_text, []
    close = _library_close(base_text)
    banner = ("\n  /* ---- PDK extension cells, spliced in by "
              "scripts/merge_lib.py ---- */\n")
    return (base_text[:close] + banner + "\n".join(additions) + "\n"
            + base_text[close:], added)


def extension_liberty(cell: str, corner: str, provisional: bool = False,
                      root: Path = DEFAULT_EXT_ROOT) -> "tuple[Path, str]":
    """The Liberty to splice for one extension cell at one corner, and why that one.

    A cell published per corner (scripts/pdk_cell.py --corners all) is spliced
    with that corner's own <CELL>_<corner>.lib, so the slow library the mapper
    and the pre-PnR STA read carries slow tables for it. A cell published once
    (--corners typ) is spliced with its <CELL>.lib, which then puts typ tables
    into every corner. A per-corner set missing this corner is an error, not a
    quiet fall-back to another corner's data. The characterized Liberty always
    wins over the provisional estimate, which is only used when asked for, so
    a stale estimate can never quietly outrank a measurement.
    """
    directory = root / cell
    own = directory / f"{cell}_{corner}.lib"
    if own.exists():
        return own, "characterized"
    corner_set = [c for c in LIB_CORNERS if (directory / f"{cell}_{c}.lib").exists()]
    if corner_set:
        raise SystemExit(
            f"error: {cell} is published per corner ({', '.join(corner_set)}) "
            f"but has no Liberty for {corner}.\n"
            f"       Re-run scripts/pdk_cell.py {cell} --from post-layout.")
    single = directory / f"{cell}.lib"
    if single.exists():
        return single, ("characterized" if corner == "typ_1p20V_25C"
                        else "characterized at typ only")
    estimate = directory / f"{cell}.provisional.lib"
    if provisional and estimate.exists():
        return estimate, "PROVISIONAL"
    raise SystemExit(
        f"error: {cell} has no characterized Liberty for {corner} in {directory}.\n"
        f"       Draw it (make pdk_cell_generation CELL_NAME={cell}), or pass "
        f"--provisional to use the pre-layout estimate.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Splice PDK-extension cells into the standard-cell "
                    "Liberty so the technology mapper can use them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  scripts/merge_lib.py                     # every drawn cell, typ corner\n"
               "  scripts/merge_lib.py --cell AION_mux2i_1\n"
               "  scripts/merge_lib.py --corner slow_1p08V_125C\n")
    parser.add_argument("--base", type=Path, default=None,
                        help=f"standard-cell Liberty to extend "
                             f"(default: {DEFAULT_BASE.name})")
    parser.add_argument("--corner", default="typ_1p20V_25C",
                        help="PDK corner, used to pick both the base library "
                             "and each cell's characterized .lib")
    parser.add_argument("--cell", action="append", default=[],
                        help="cell to splice in; repeat. Default: every cell "
                             "under implementation/pdk_extension/")
    parser.add_argument("--lib", type=Path, action="append", default=[],
                        help="an explicit Liberty to take cells from; repeat")
    parser.add_argument("--out", type=Path, default=None,
                        help=f"output path (default: "
                             f"{DEFAULT_OUT.relative_to(PROJECT_ROOT)}/<name>)")
    parser.add_argument("--replace", action="store_true",
                        help="let an extension cell override a base cell of "
                             "the same name")
    parser.add_argument("--provisional", action="store_true",
                        help="fall back to <CELL>.provisional.lib when the "
                             "cell has not been characterized yet")
    args = parser.parse_args(argv)

    base = args.base or (DEFAULT_BASE.parent /
                         f"sg13g2_stdcell_{args.corner}.lib")
    if not base.exists():
        raise SystemExit(f"error: no base library at {base}")

    sources: list[tuple[str, str]] = []
    for lib in args.lib:
        sources.append((str(lib), lib.read_text()))

    cells = args.cell
    if not cells and not args.lib:
        # "views" was where pdk_cell.py published before the views moved in
        # beside the sources; a leftover one is not a cell.
        cells = sorted(p.name for p in DEFAULT_EXT_ROOT.glob("*")
                       if p.is_dir() and p.name != "views")
    for cell in cells:
        path, how = extension_liberty(cell, args.corner, args.provisional)
        sources.append((str(path), path.read_text()))
        note = {"PROVISIONAL": "  (area is an estimate, not a measurement)",
                "characterized at typ only": "  (typ tables at this corner)"}.get(how, "")
        print(f"  {cell}: {how}  {path.relative_to(PROJECT_ROOT)}{note}")

    if not sources:
        raise SystemExit("error: nothing to merge")

    merged, added = merge(base.read_text(), sources, replace=args.replace)
    out = args.out or (DEFAULT_OUT / f"sg13g2_stdcell_aion_{args.corner}.lib")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(merged)

    print(f"\n  base   {base.relative_to(PROJECT_ROOT)}")
    print(f"  added  {', '.join(added)}")
    print(f"  wrote  {out.relative_to(PROJECT_ROOT)}")
    print("\n  point the mapper at it:")
    print(f"    yosys ... abc -liberty {out.relative_to(PROJECT_ROOT)}")
    print(f"    LibreLane: CELL_LIBS = [\"{out.relative_to(PROJECT_ROOT)}\"]"
          f"   (NOT EXTRA_LIBS -- that blackboxes it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
