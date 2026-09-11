#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Keep a mined cell's name stable across runs, so
#                             a layout drawn once is never drawn twice
#
#  aion_opt names a generated module `<prefix><sorted cell types>_<id>`, and
#  the id is the pattern's *rank in that run's cover*
#  (`_assign_module_names`: `enumerate(rank_patterns(cover))`).  Neither half
#  of that name is an identity:
#
#    * the rank moves whenever the cover moves -- a different MIN_SELECTED, a
#      different ELITE_COUNT, an edit to the RTL -- so the same pattern comes
#      back as `AION_nand2_o21ai_1` where it left as `AION_nand2_o21ai_0`;
#    * the prefix is only the sorted *multiset of cell types*, so two patterns
#      that fuse the same gates with different wiring share it.
#
#  Everything downstream keys off the name.  Step 6 decides whether a cell
#  still needs drawing with `if not (LAYOUT_CELLS / f"{cell}.py").exists()`
#  and publishes to `implementation/cells/<CELL>/`.  So a rank shuffle costs
#  you the layout twice over: the drawn cell is not recognised under its new
#  name and gets redrawn, and the name it vacated may be picked up by a
#  different pattern whose generator -- possibly an abandoned scaffold from
#  an earlier run -- is silently accepted as a drawing.
#
#  The stable identity is the canonical key.  aion_opt already writes it above
#  every module (`// AION canonical_key:`) and reads names back out of the
#  library rather than re-deriving them (`cmd_rewrite` -> `load_key_map`), so
#  the missing piece is only a record of which key each drawn layout belongs
#  to.  This file is that record:
#
#      implementation/cells/registry.json     {canonical key: cell name}
#
#  Step 6 writes an entry as soon as a cell's layout verifies -- DRC clean and
#  LVS against its netlist -- and not when it publishes, because a verified
#  cell can still be withheld (a LOSS under DRAW_PUBLISH_ON_LOSS=0, an export
#  the cell was refused, a re-grade to FAIL) while its generator stays on
#  disk, and the generator is what step 6 looks for.  Step 2 reads the
#  entries back and renames its freshly mined modules so a known pattern
#  keeps the name its drawing is already filed under.  A pattern nobody has
#  drawn keeps whatever aion_opt called it, unless that name belongs to a
#  different key -- then it moves to a free index, because that collision is
#  the silent one.
#
#      scripts/cell_registry.py show
#      scripts/cell_registry.py adopt flow/3_rewrite/nl/aion_cells.v
#      scripts/cell_registry.py apply flow/2_pattern_extraction/aion_cells.v
# ================================================================

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AION_OPT = PROJECT_ROOT / "aion_flow" / "tools" / "aion_opt"
if str(AION_OPT) not in sys.path:
    sys.path.insert(0, str(AION_OPT))

# The library is parsed with aion_opt's own splitter on purpose: a second
# parser that disagreed with `read_keys` about where a marker binds would
# write a file the rewriter reads differently than we wrote it.
from aion_opt.io.cell_file import split_modules, write_library  # noqa: E402

REGISTRY = PROJECT_ROOT / "implementation" / "cells" / "registry.json"

#: `<anything>_<digits>` -- the trailing rank aion_opt appends.
_INDEXED = re.compile(r"^(?P<stem>.+)_(?P<index>\d+)$")

_DOC = [
    "Canonical key -> cell name, so a mined pattern keeps the name its",
    "hand-drawn layout is filed under.",
    "",
    "Written by step 6 once a cell's layout verifies (DRC + LVS), read by",
    "step 2, which renames its freshly mined modules to match. An entry",
    "means a generator exists for that pattern, not that the cell reached",
    "implementation/cells/ -- a verified cell can still be withheld.",
    "See scripts/cell_registry.py for why the name aion_opt generates",
    "cannot be trusted across runs.",
]


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------
def load(path: Path = REGISTRY) -> dict[str, str]:
    """Return ``{canonical key: cell name}``, empty when there is no file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cells = data.get("cells")
    return dict(cells) if isinstance(cells, dict) else {}


def save(entries: dict[str, str], path: Path = REGISTRY) -> None:
    """Write the registry, keys sorted so a diff is readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"//": _DOC, "cells": {k: entries[k] for k in sorted(entries)}}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def record(key: str, name: str, path: Path = REGISTRY) -> str:
    """Bind ``key`` to ``name``.

    Returns a one-line description of what changed, or "" when the registry
    already said exactly this.  Re-binding a key to a new name is allowed and
    reported: that is what happens when a cell is deliberately redrawn under
    a different name.  Two keys claiming one name is not -- the second would
    make `implementation/cells/<name>/` ambiguous -- so it raises.
    """
    entries = load(path)
    clash = [k for k, n in entries.items() if n == name and k != key]
    if clash:
        raise ValueError(
            f"{name} is already the name of a different pattern "
            f"({clash[0][:60]}...). Two patterns cannot share one layout "
            f"directory; rename one of them before recording.")
    previous = entries.get(key)
    if previous == name:
        return ""
    entries[key] = name
    save(entries, path)
    return (f"{name}: recorded" if previous is None
            else f"{name}: was {previous}")


# ---------------------------------------------------------------------------
# Applying it to a freshly mined library
# ---------------------------------------------------------------------------
def _free_name(taken: set[str], name: str) -> str:
    """The first `<stem>_<n>` not in ``taken``, counting up from ``name``."""
    match = _INDEXED.match(name)
    stem = match.group("stem") if match else name
    index = int(match.group("index")) if match else 0
    while f"{stem}_{index}" in taken:
        index += 1
    return f"{stem}_{index}"


def plan(modules, entries: dict[str, str]) -> dict[str, str]:
    """Return ``{old name: new name}`` for the modules that must move.

    Two rules, in order:

    * a module whose key the registry knows takes the recorded name -- that
      is the whole point, and it is what makes a drawn layout findable;
    * a module whose key it does not know keeps aion_opt's name unless that
      name is spoken for, either by a recorded pattern or by another module
      in this same file, in which case it moves to the next free index.

    The second rule is the one that matters for correctness: without it a new
    pattern can land on the name of a drawn cell and inherit its layout.
    """
    # Names owned by a key the registry knows, minus the ones no module here
    # claims -- a recorded name whose pattern is absent from this library is
    # still reserved, because its layout is on disk under that name.
    reserved = set(entries.values())
    assigned: dict[str, str] = {}
    taken: set[str] = set()

    for module in modules:
        wanted = entries.get(module.canonical_key or "")
        if wanted is not None:
            assigned[module.name] = wanted
            taken.add(wanted)

    for module in modules:
        if module.name in assigned:
            continue
        wanted = module.name
        if wanted in taken or wanted in reserved:
            wanted = _free_name(taken | reserved, wanted)
        assigned[module.name] = wanted
        taken.add(wanted)

    return {old: new for old, new in assigned.items() if old != new}


def _rename_module(module, new_name: str):
    """A copy of ``module`` under ``new_name``.

    Only the `module <name> (...)` line carries the name -- a generated cell
    never refers to itself in its body -- so the rest of the text is passed
    through verbatim, which keeps the file byte-identical everywhere else.
    """
    lines = module.text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(r"^\s*module\s+" + re.escape(module.name) + r"\b", line):
            lines[i] = re.sub(r"\bmodule\s+" + re.escape(module.name) + r"\b",
                              f"module {new_name}", line, count=1)
            break
    module.name = new_name
    module.text = "".join(lines)
    return module


def _header(text: str) -> str:
    """Everything before the first module or its marker.

    `write_library` takes the header as a string and the modules separately,
    so a round-trip has to hand back the part `split_modules` dropped.
    """
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if (line.lstrip().startswith("// AION canonical_key:")
                or re.match(r"^\s*module\s+\w+", line)):
            return "".join(lines[:i])
    return text


def apply_to_library(path: Path, entries: dict[str, str] | None = None,
                     out: Path | None = None,
                     renames: dict[str, str] | None = None) -> dict[str, str]:
    """Rename the modules of a cell library in place. Returns the renames.

    ``renames`` lets a caller impose a plan computed elsewhere.  Step 2 needs
    that: it writes the full library and the elite cut of it, and a plan
    derived separately from each could hand an unregistered pattern a
    different free index in the two files -- which REWRITE_CELLS can point at
    either of.
    """
    entries = load() if entries is None else entries
    text = path.read_text(encoding="utf-8")
    modules = split_modules(text)
    if renames is None:
        renames = plan(modules, entries)
    renames = {old: new for old, new in renames.items()
               if old in {m.name for m in modules}}
    if not renames:
        return {}
    header = _header(text)
    for module in modules:
        new = renames.get(module.name)
        if new:
            _rename_module(module, new)
    write_library(out or path, modules, header)
    return renames


def keys_by_name(path: Path) -> dict[str, str]:
    """``{module name: canonical key}`` for a cell library."""
    return {m.name: m.canonical_key for m in split_modules(
        path.read_text(encoding="utf-8")) if m.canonical_key}


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Keep mined cell names stable across runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  scripts/cell_registry.py show\n"
               "  scripts/cell_registry.py adopt flow/3_rewrite/nl/aion_cells.v\n"
               "  scripts/cell_registry.py apply "
               "flow/2_pattern_extraction/aion_cells.v\n")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="print the registry")

    a = sub.add_parser("adopt", help="record the names a library already uses")
    a.add_argument("library", type=Path)
    a.add_argument("--only", nargs="*", metavar="NAME",
                   help="record just these modules (default: all of them)")

    p = sub.add_parser("apply", help="rename a library's modules to match")
    p.add_argument("library", type=Path)
    p.add_argument("--out", type=Path, default=None,
                   help="write here instead of in place")
    p.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "show":
        entries = load()
        if not entries:
            print(f"{REGISTRY.relative_to(PROJECT_ROOT)}: empty")
            return 0
        for key in sorted(entries, key=lambda k: entries[k]):
            print(f"{entries[key]:<34}{key}")
        return 0

    if not args.library.exists():
        parser.error(f"{args.library} does not exist")

    if args.command == "adopt":
        changed = 0
        for name, key in sorted(keys_by_name(args.library).items()):
            if args.only and name not in args.only:
                continue
            try:
                message = record(key, name)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            if message:
                print(message)
                changed += 1
        print(f"{changed} entr{'y' if changed == 1 else 'ies'} written to "
              f"{REGISTRY.relative_to(PROJECT_ROOT)}")
        return 0

    renames = (plan(split_modules(args.library.read_text(encoding="utf-8")),
                    load())
               if args.dry_run else
               apply_to_library(args.library, out=args.out))
    if not renames:
        print("nothing to rename")
        return 0
    for old, new in sorted(renames.items()):
        print(f"{old} -> {new}")
    if args.dry_run:
        print("(dry run, nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
