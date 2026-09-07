# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-04
#  Description:               Discover and sanity-check the AI-generated
#                             standard cells that feed the PnR flow.
#
#  A cell is a set of views sharing one file stem. Any directory layout
#  works, because discovery is a recursive walk grouped by stem:
#
#      cells/foo.lef  cells/foo.lib  ...          (flat)
#      cells/foo/foo.lef  cells/foo/foo.lib  ...  (one directory per cell)
#      cells/lef/foo.lef  cells/lib/foo.lib  ...  (one directory per view)
# ================================================================

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List

# view -> the LibreLane configuration variable that carries it
EXTRA_KEY_BY_VIEW = {
    "lef": "EXTRA_LEFS",
    "lib": "EXTRA_LIBS",
    "gds": "EXTRA_GDS",
    "verilog": "EXTRA_VERILOG_MODELS",
    "spice": "EXTRA_SPICE_MODELS",
    "cdl": "EXTRA_CDLS",
}

VIEW_BY_EXTENSION = {
    ".lef": "lef",
    ".lib": "lib",
    ".gds": "gds",
    ".gds.gz": "gds",
    ".gdsii": "gds",
    ".v": "verilog",
    ".sv": "verilog",
    ".spice": "spice",
    ".spi": "spice",
    ".sp": "spice",
    ".cir": "spice",
    ".cdl": "cdl",
}

REQUIRED_VIEWS = ("lef", "lib", "gds")
RECOMMENDED_VIEWS = ("verilog", "spice")

# ihp-sg13g2 sg13g2_stdcell placement site, from the technology LEF.
SITE_NAME = "CoreSite"
SITE_WIDTH = 0.48
SITE_HEIGHT = 3.78
GEOMETRY_TOLERANCE = 1e-6

# Routing tracks, from libs.tech/librelane/sg13g2_stdcell/tracks.info:
# every Metal has X lines at n * 0.48 um and Y lines at n * 0.42 um.
TRACK_PITCH = {"x": 0.48, "y": 0.42}
TRACK_OFFSET = {"x": 0.0, "y": 0.0}

# Routing layer -> the axis whose track lines a pin on it must cover, from
# DIRECTION in sg13g2_tech.lef. A wire runs along its layer's preferred
# direction, so it stops anywhere on that axis but is pinned to a track on the
# other: a HORIZONTAL layer routes along y = n * 0.42, a VERTICAL one along
# x = n * 0.48. A pin covering no such line has nothing for a wire to land on.
ROUTING_AXIS = {
    "Metal1": "y",      # HORIZONTAL
    "Metal2": "x",      # VERTICAL
    "Metal3": "y",      # HORIZONTAL
    "Metal4": "x",      # VERTICAL
    "Metal5": "y",      # HORIZONTAL
}
TRACK_TOLERANCE = 5e-4


@dataclass
class Cell:
    name: str
    views: Dict[str, str] = field(default_factory=dict)

    def missing(self, views) -> List[str]:
        return [v for v in views if v not in self.views]


def split_extension(filename: str):
    lowered = filename.lower()
    for ext in sorted(VIEW_BY_EXTENSION, key=len, reverse=True):
        if lowered.endswith(ext):
            return filename[: -len(ext)], ext
    return None, None


def collect_cells(cells_dir: str) -> List[Cell]:
    """Group every recognised view file under cells_dir into Cell records."""
    by_name: Dict[str, Cell] = {}

    for root, dirs, files in os.walk(cells_dir):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
        for filename in sorted(files):
            if filename.startswith("."):
                continue
            stem, ext = split_extension(filename)
            if stem is None:
                continue
            view = VIEW_BY_EXTENSION[ext]
            cell = by_name.setdefault(stem, Cell(name=stem))
            path = os.path.join(root, filename)
            if view in cell.views:
                print(
                    f"Warning: duplicate {view} view for cell '{stem}': "
                    f"keeping {cell.views[view]}, ignoring {path}",
                    file=sys.stderr,
                )
                continue
            cell.views[view] = path

    return [by_name[name] for name in sorted(by_name)]


# ------------------------------------------------------------------
# LEF sanity checks
#
# A cell that is not row-legalizable will not fail until detailed
# placement, an hour into the flow. These checks are cheap and catch the
# mistakes an LLM-generated abstract actually makes.
# ------------------------------------------------------------------
MACRO_RE = re.compile(r"^\s*MACRO\s+(\S+)", re.MULTILINE)
PIN_BLOCK_RE = re.compile(
    r"^[ \t]*PIN[ \t]+(\S+)[ \t]*$(.*?)^[ \t]*END[ \t]+\1[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
LAYER_RE = re.compile(r"^\s*LAYER\s+(\S+)\s*;")
RECT_RE = re.compile(
    r"^\s*RECT\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+"
    r"([0-9.eE+-]+)\s+([0-9.eE+-]+)\s*;"
)
USE_RE = re.compile(r"^\s*USE\s+(\S+)\s*;", re.MULTILINE)


def covers_track(lo: float, hi: float, axis: str) -> bool:
    """True when the span [lo, hi] contains a routing track line on axis."""
    offset, pitch = TRACK_OFFSET[axis], TRACK_PITCH[axis]
    n = math.ceil((lo - offset) / pitch - TRACK_TOLERANCE / pitch)
    return offset + n * pitch <= hi + TRACK_TOLERANCE


def check_pin_access(macro: str, body: str) -> List[str]:
    """Every signal pin must give the router a track line to land on.

    This is the static half of TritonRoute's pin access: necessary, not
    sufficient. It is the half an abstract drawn without the track grid in
    mind fails, and it is worth a second here because DRT-0073 is a hard abort
    in detailed routing an hour into the flow, not a DRC that LENIENT=1 can
    downgrade. All 283 signal pins of the PDK sg13g2_stdcell library pass it.
    """
    problems = []
    for pin, pin_body in PIN_BLOCK_RE.findall(body):
        use = USE_RE.search(pin_body)
        if use is not None and use.group(1).upper() in ("POWER", "GROUND"):
            continue
        if use is None and pin.upper() in ("VDD", "VSS"):
            continue

        layers, hit = [], False
        layer = None
        for line in pin_body.splitlines():
            layer_match = LAYER_RE.match(line)
            if layer_match is not None:
                layer = layer_match.group(1)
                if layer in ROUTING_AXIS and layer not in layers:
                    layers.append(layer)
                continue
            rect = RECT_RE.match(line)
            if rect is None or layer not in ROUTING_AXIS:
                continue
            x1, y1, x2, y2 = (float(v) for v in rect.groups())
            axis = ROUTING_AXIS[layer]
            lo, hi = (y1, y2) if axis == "y" else (x1, x2)
            if covers_track(min(lo, hi), max(lo, hi), axis):
                hit = True
                break
        if hit:
            continue

        if not layers:
            problems.append(
                f"{macro}: PIN {pin} has no geometry on a routing layer, "
                "so the router cannot reach it"
            )
        else:
            where = ", ".join(
                f"{lay} routes along {ROUTING_AXIS[lay]} = n * "
                f"{TRACK_PITCH[ROUTING_AXIS[lay]]}um"
                for lay in layers
            )
            problems.append(
                f"{macro}: PIN {pin} covers no routing track ({where}). "
                "Detailed routing aborts with 'DRT-0073 No access point'"
            )
    return problems


def check_lef(path: str) -> List[str]:
    problems = []
    with open(path, "r", errors="replace") as f:
        text = f.read()

    macros = MACRO_RE.findall(text)
    if not macros:
        return [f"{path}: no MACRO definition found"]

    for macro in macros:
        body_match = re.search(
            rf"^\s*MACRO\s+{re.escape(macro)}\b(.*?)^\s*END\s+{re.escape(macro)}\b",
            text,
            re.MULTILINE | re.DOTALL,
        )
        if body_match is None:
            problems.append(f"{macro}: MACRO block is not closed by 'END {macro}'")
            continue
        body = body_match.group(1)

        cls = re.search(r"^\s*CLASS\s+([^;]+);", body, re.MULTILINE)
        if cls is None or cls.group(1).split()[0].upper() != "CORE":
            found = cls.group(1).strip() if cls else "none"
            problems.append(
                f"{macro}: CLASS is '{found}', must be CORE so the placer treats "
                "it as a standard cell rather than a macro"
            )

        site = re.search(r"^\s*SITE\s+(\S+)\s*;", body, re.MULTILINE)
        if site is None or site.group(1) != SITE_NAME:
            found = site.group(1) if site else "none"
            problems.append(f"{macro}: SITE is '{found}', must be {SITE_NAME}")

        size = re.search(
            r"^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;", body, re.MULTILINE
        )
        if size is None:
            problems.append(f"{macro}: no SIZE statement")
        else:
            width, height = float(size.group(1)), float(size.group(2))
            if abs(height - SITE_HEIGHT) > GEOMETRY_TOLERANCE:
                problems.append(
                    f"{macro}: height is {height}, must be exactly "
                    f"{SITE_HEIGHT} (one {SITE_NAME} row)"
                )
            sites = width / SITE_WIDTH
            if abs(sites - round(sites)) > 1e-3:
                problems.append(
                    f"{macro}: width is {width}, must be a multiple of "
                    f"{SITE_WIDTH} ({sites:.3f} sites)"
                )

        for pin in ("VDD", "VSS"):
            if not re.search(rf"^\s*PIN\s+{pin}\s*$", body, re.MULTILINE):
                problems.append(f"{macro}: no {pin} PIN — the PDN cannot connect it")

        problems.extend(check_pin_access(macro, body))

    return problems


def check_lib(path: str, cell_name: str) -> List[str]:
    with open(path, "r", errors="replace") as f:
        text = f.read()
    if not re.search(r"^\s*cell\s*\(", text, re.MULTILINE):
        return [f"{path}: no cell() group found"]
    return []


def check_cells(cells_dir: str, strict: bool) -> int:
    cells = collect_cells(cells_dir)
    if not cells:
        print(f"No custom cells found under {cells_dir}/ — running with the PDK "
              "standard cell library only.")
        return 0

    errors = 0
    warnings = 0
    all_problems: List[str] = []

    print(f"Custom cells under {cells_dir}/:")
    for cell in cells:
        have = ", ".join(sorted(cell.views)) or "nothing"
        print(f"  {cell.name}: {have}")

        for view in cell.missing(REQUIRED_VIEWS):
            print(f"    ERROR   missing {view} view ({EXTRA_KEY_BY_VIEW[view]})")
            errors += 1
        for view in cell.missing(RECOMMENDED_VIEWS):
            print(f"    warning missing {view} view ({EXTRA_KEY_BY_VIEW[view]})")
            warnings += 1

        if "lef" in cell.views:
            for problem in check_lef(cell.views["lef"]):
                print(f"    ERROR   {problem}")
                all_problems.append(problem)
                errors += 1
        if "lib" in cell.views:
            for problem in check_lib(cell.views["lib"], cell.name):
                print(f"    ERROR   {problem}")
                errors += 1

    print(f"{len(cells)} cell(s), {errors} error(s), {warnings} warning(s)")

    # LENIENT=1 downgrades LibreLane's checkers. It does not downgrade this
    # one: DRT-0073 is a hard abort inside TritonRoute's pin access, so a cell
    # that fails the track rule takes the run down whatever this gate does.
    # Saying "rerun with LENIENT=1" to someone holding one would be a lie.
    unroutable = errors and any(
        "covers no routing track" in problem or "no geometry on a routing layer" in problem
        for problem in all_problems
    )

    if errors and strict:
        if unroutable:
            print(
                "Refusing to run PnR: a pin above has nothing for the router "
                "to land on, and detailed routing will abort with DRT-0073. "
                "LENIENT=1 does not downgrade that — redraw the cell.",
                file=sys.stderr,
            )
        else:
            print(
                "Refusing to run PnR with malformed cells. Fix them, or rerun "
                "with LENIENT=1 to push through.",
                file=sys.stderr,
            )
        return 1
    if errors:
        print("LENIENT=1: continuing despite the errors above.")
        if unroutable:
            print(
                "  note: the pin access errors are not downgradable. Detailed "
                "routing will still abort with DRT-0073."
            )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Discover and sanity-check AI-generated standard cells."
    )
    parser.add_argument("cells_dir", help="Directory to scan.")
    parser.add_argument(
        "--lenient",
        action="store_true",
        help="Report problems but exit 0.",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.cells_dir):
        print(f"No custom cells directory at {args.cells_dir}/ — nothing to do.")
        return 0
    return check_cells(args.cells_dir, strict=not args.lenient)


if __name__ == "__main__":
    sys.exit(main())
