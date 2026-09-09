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
        cells = sorted(p.name for p in DEFAULT_EXT_ROOT.glob("*")
                       if p.is_dir() and p.name != "views")
    for cell in cells:
        # The characterized Liberty step 3 of pdk_cell.py writes wins; the
        # provisional one is only used when asked for, so a stale estimate
        # can never quietly outrank a measurement.
        measured = DEFAULT_EXT_ROOT / "views" / cell / f"{cell}.lib"
        provisional = DEFAULT_EXT_ROOT / cell / f"{cell}.provisional.lib"
        if measured.exists():
            sources.append((str(measured), measured.read_text()))
            print(f"  {cell}: characterized  "
                  f"{measured.relative_to(PROJECT_ROOT)}")
        elif args.provisional and provisional.exists():
            sources.append((str(provisional), provisional.read_text()))
            print(f"  {cell}: PROVISIONAL    "
                  f"{provisional.relative_to(PROJECT_ROOT)}  "
                  f"(area is an estimate, not a measurement)")
        else:
            raise SystemExit(
                f"error: {cell} has no characterized Liberty at "
                f"{measured.relative_to(PROJECT_ROOT)}.\n"
                f"       Draw it (scripts/pdk_cell.py {cell}), or pass "
                f"--provisional to use the pre-layout estimate.")

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
