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

# Routing layers, from sg13g2_tech.lef. A port has to have geometry on one of
# them for a wire to reach it at all.
ROUTING_LAYERS = ("Metal1", "Metal2", "Metal3", "Metal4", "Metal5")

# The two metal shapes of every DEFAULT ViaN (N = 1..4) in sg13g2_tech.lef, as
# ((enclosure on the metal below w, h), (pad on the metal above w, h)) in um:
# ViaN_XX, _XY, _YX and _YY. The _s variants and Via1_s only have bigger pads,
# so they reach no port these four cannot.
VIA_SHAPES = (
    ((0.29, 0.21), (0.29, 0.20)),
    ((0.29, 0.21), (0.20, 0.29)),
    ((0.21, 0.29), (0.29, 0.20)),
    ((0.21, 0.29), (0.20, 0.29)),
)

# The smallest entry of each routing metal's SPACINGTABLE in sg13g2_tech.lef.
# The smallest, so that a pin this rejects is one TritonRoute cannot reach --
# never one it merely might not.
METAL_SPACING = {
    "Metal1": 0.18,
    "Metal2": 0.21,
    "Metal3": 0.21,
    "Metal4": 0.21,
    "Metal5": 0.21,
}

# MANUFACTURINGGRID. Every via half-size above is a multiple of it, so a via
# centre off this grid would put the via's own edges off it.
MANUFACTURING_GRID = 0.005

# The layer a via off a port lands on.
LAYER_ABOVE = {
    "Metal1": "Metal2",
    "Metal2": "Metal3",
    "Metal3": "Metal4",
    "Metal4": "Metal5",
}

# Problems that mean the router has nothing to land on. LENIENT=1 does not
# downgrade these: DRT-0073 is a hard abort inside TritonRoute's pin access,
# not a checker LibreLane can be told to ignore.
UNROUTABLE_MARKERS = (
    "no geometry on a routing layer",
    "has no via access",
)


# The PDK-extension cells (implementation/pdk_extension/<CELL>/) are NOT
# discovered by stem, and the difference is not cosmetic. That directory holds
# the cell's sources beside the views the exporter published, and three of the
# names would group wrong:
#
#   <CELL>.v               the hand-written *structural* gold reference, built
#                          out of PDK cells. Correct for the post-synthesis
#                          simulation and wrong here: after PnR the cell is a
#                          LEAF with its own SDF entries, so the model PnR and
#                          the SDF agree on is <CELL>.layout.v -- the solved
#                          behavioural view, with the specify block an IOPATH
#                          record annotates.
#   <CELL>.layout.v        would group under the stem "<CELL>.layout" -- a
#                          phantom cell with a Verilog view and no LEF.
#   <CELL>.provisional.lib likewise "<CELL>.provisional", a phantom with only
#                          a Liberty. It is also a floorplan *estimate*, and a
#                          run timed against an estimate is not a result.
#
# So the views are named, not inferred. A cell that has not been drawn has no
# <CELL>.lef and is simply not offered to PnR, which is the truth about it.
PDK_EXT_VIEW_SUFFIX = {
    "lef": ".lef",
    "lib": ".lib",
    "gds": ".gds",
    "verilog": ".layout.v",
    "spice": ".spice",
    "cdl": ".cdl",
}

# Left over from when pdk_cell.py published into a shared directory instead of
# beside each cell's sources; a stale one is not a cell.
PDK_EXT_NOT_A_CELL = ("views",)


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


def collect_pdk_extension_cells(ext_dir: str) -> List[Cell]:
    """One Cell per implementation/pdk_extension/<CELL>/, views named exactly.

    Same Cell shape as collect_cells(), so the LEF and Liberty checks below
    grade a hand-designed cell exactly as they grade a drawn one -- it stands
    in the same rows, next to the same neighbours, and gets the placer's
    treatment either way.
    """
    root = os.path.abspath(ext_dir)
    cells: List[Cell] = []
    for name in sorted(os.listdir(root)):
        directory = os.path.join(root, name)
        if (not os.path.isdir(directory) or name.startswith(".")
                or name in PDK_EXT_NOT_A_CELL):
            continue
        cell = Cell(name=name)
        for view, suffix in PDK_EXT_VIEW_SUFFIX.items():
            path = os.path.join(directory, f"{name}{suffix}")
            if os.path.isfile(path):
                cell.views[view] = path
        cells.append(cell)
    return cells


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
# The obstruction block runs to the first bare END; 'END <macro>' has a token
# after it and cannot close it by accident.
OBS_BLOCK_RE = re.compile(r"^[ \t]*OBS[ \t]*$(.*?)^[ \t]*END[ \t]*$", re.MULTILINE | re.DOTALL)


def layer_rects(block: str):
    """Every RECT in a PORT or OBS block, as (layer, x1, y1, x2, y2)."""
    layer = None
    for line in block.splitlines():
        layer_match = LAYER_RE.match(line)
        if layer_match is not None:
            layer = layer_match.group(1)
            continue
        rect = RECT_RE.match(line)
        if rect is None or layer is None:
            continue
        x1, y1, x2, y2 = (float(v) for v in rect.groups())
        yield layer, min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def grid_points(lo: float, hi: float) -> range:
    """Indices of the manufacturing-grid points strictly inside (lo, hi)."""
    return range(math.floor(lo / MANUFACTURING_GRID + 1e-6) + 1,
                 math.ceil(hi / MANUFACTURING_GRID - 1e-6))


def free_grid_point(lo: float, hi: float, spans):
    """The lowest grid point in (lo, hi) outside every open span, or None.

    A point exactly on the end of a span is outside it: that is a via at
    exactly the minimum spacing, which is legal.
    """
    points = grid_points(lo, hi)
    k = points.start
    while k < points.stop:
        at = k * MANUFACTURING_GRID
        covering = [end for start, end in spans if start + 1e-6 < at < end - 1e-6]
        if not covering:
            return at
        k = max(k + 1, math.ceil(max(covering) / MANUFACTURING_GRID - 1e-6))
    return None


def via_fits(port, others, below: bool = True, above: bool = True) -> bool:
    """Whether some ViaN can connect to one port rect with its metal clear.

    `port` is (layer, x1, y1, x2, y2) and `others` every shape of every *other*
    net in the macro -- its OBS and the other pins. The via's enclosure has to
    overlap the port, and nothing more: it may hang off the port onto free
    metal. What neither of its metal shapes may do is come within its layer's
    spacing of another net, measured corner to corner the way spacing is.

    That overhang is the whole difference from the rule this replaced, which
    wanted the via inside the port. TritonRoute does not: on AION_mux2_0/I0 it
    put a Via1_YY 60 nm left of the port, cut overlapping it by 35 nm, pad
    0.265 um from the cell's Metal2 strap, and routed all 126 instances clean --
    while that rule refused to publish the cell.

    Positions are searched over the manufacturing grid, and every legal one
    counts, including ones TritonRoute never tries (it tries tracks, half
    tracks, the pin centre and positions aligning the enclosure with the pin).
    So a pin this passes can still fail pin access when the only room is a few
    nanometres wide; a pin this rejects, TritonRoute cannot reach.

    below=False or above=False ignores the other nets on that metal, which is
    how a caller finds out which of the two closes the door.
    """
    layer, x1, y1, x2, y2 = port
    upper = LAYER_ABOVE.get(layer)
    if upper is None:                     # topmost routing layer, nothing above
        return True
    for (below_w, below_h), (above_w, above_h) in VIA_SHAPES:
        # Via centres at which the enclosure overlaps the port.
        box = (x1 - below_w / 2, y1 - below_h / 2, x2 + below_w / 2, y2 + below_h / 2)
        # Each shape of another net, grown by the via shape that has to clear
        # it, so that the question is how close the via *centre* may come.
        grown = []
        for other, ox1, oy1, ox2, oy2 in others:
            if other == layer and below:
                half_w, half_h, space = below_w / 2, below_h / 2, METAL_SPACING[layer]
            elif other == upper and above:
                half_w, half_h, space = above_w / 2, above_h / 2, METAL_SPACING[upper]
            else:
                continue
            grown.append((ox1 - half_w, oy1 - half_h, ox2 + half_w, oy2 + half_h, space))
        for kx in grid_points(box[0], box[2]):
            cx = kx * MANUFACTURING_GRID
            spans = []
            for gx1, gy1, gx2, gy2, space in grown:
                dx = max(gx1 - cx, cx - gx2, 0.0)
                if dx < space - 1e-6:
                    reach = math.sqrt(space * space - dx * dx)
                    spans.append((gy1 - reach, gy2 + reach))
            if free_grid_point(box[1], box[3], spans) is not None:
                return True
    return False


def no_via_access(macro: str, pin: str, ports, others) -> str:
    """Why no via reaches any port of `pin`, and what would make room."""
    layer = min((port[0] for port in ports), key=ROUTING_LAYERS.index)
    upper = LAYER_ABOVE[layer]
    lowest = [port for port in ports if port[0] == layer]
    space, upper_space = METAL_SPACING[layer], METAL_SPACING[upper]
    if not any(via_fits(port, others, below=False) for port in lowest):
        why = (f"every place for the via's {upper} pad is within {upper_space} um "
               f"of another net's {upper} -- OBS or another pin; metal written to "
               "OBS counts as another net even when it is this pin's own")
        fix = (f"move that {upper} {upper_space} um clear of where a via can sit, "
               f"or declare the port on {upper} where the metal already is")
    elif not any(via_fits(port, others, above=False) for port in lowest):
        why = (f"the via's {layer} enclosure cannot overlap the port without "
               f"coming within {space} um of another net's {layer}")
        fix = f"leave {layer} free beside the port for the enclosure to hang onto"
    else:
        why = (f"the positions whose {upper} pad clears the other nets put the "
               f"{layer} enclosure within {space} um of another net's {layer}, "
               "and the other way around")
        fix = f"make room on one side of the port, on {layer} and {upper} both"
    return (f"{macro}: PIN {pin} has no via access: no ViaN can overlap the port "
            f"with its {layer} enclosure and keep both of its metal shapes clear "
            f"of every other net -- {why}. Detailed routing aborts with 'DRT-0073 "
            f"No access point'; {fix}")


def check_pin_access(macro: str, body: str) -> List[str]:
    """Every signal pin must give the router a way in.

    Two things have to hold:

      * the port has geometry on a routing layer at all;
      * some via can overlap one of its routing-layer rects with both of its
        metal shapes clear of every other net (see via_fits).

    Covering a track and being 0.21 um across are *not* required, and this used
    to require both. TritonRoute pin access (OpenROAD 26Q3) reaches Metal1 ports
    10 and 40 nm off the track grid, a 0.18 um port, and AION_mux2_0's I0/I1/I3
    whenever the via has room to hang off the port. It finds no access point for
    AION_mux2i_1/I2 and AION_mux2i_2/I2, a port boxed in by other nets' Metal1,
    or a port with an obstruction strap across it -- and this rejects exactly
    those. aion_layout's metrics.lef_pin_access applies the same rule to the
    same LEF inside the drawing loop; the two must stay in step, or the loop
    converges on cells that are then refused here.

    This is the static half of TritonRoute's pin access: necessary, not
    sufficient, because it cannot see the neighbouring instances or the
    router's own choice of positions. It is worth a second here because
    DRT-0073 is a hard abort, not a DRC that LENIENT=1 can downgrade. Every
    signal pin of the PDK sg13g2_stdcell library passes.
    """
    obs_block = OBS_BLOCK_RE.search(body)
    base = list(layer_rects(obs_block.group(1))) if obs_block else []
    pins = PIN_BLOCK_RE.findall(body)
    # Every pin's shapes, so each is graded against the others: to the router a
    # neighbouring pin is another net exactly as an obstruction is.
    shapes = {pin: list(layer_rects(pin_body)) for pin, pin_body in pins}

    problems = []
    for pin, pin_body in pins:
        use = USE_RE.search(pin_body)
        if use is not None and use.group(1).upper() in ("POWER", "GROUND"):
            continue
        if use is None and pin.upper() in ("VDD", "VSS"):
            continue

        ports = [rect for rect in shapes[pin] if rect[0] in ROUTING_LAYERS]
        if not ports:
            problems.append(
                f"{macro}: PIN {pin} has no geometry on a routing layer, "
                "so the router cannot reach it"
            )
            continue

        others = base + [rect for name, rects in shapes.items()
                         if name != pin for rect in rects]
        if not any(via_fits(port, others) for port in ports):
            problems.append(no_via_access(macro, pin, ports, others))
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


def check_cells(groups, strict: bool) -> int:
    """Grade every cell PnR is about to be handed, whoever drew it.

    `groups` is a list of (label, cells). The mined cells and the
    hand-designed ones arrive by different routes -- step 6 publishes the
    first into implementation/cells/, the second are authored under
    implementation/pdk_extension/ -- but the placer and the router make no
    such distinction, so neither does this.
    """
    cells = [cell for _, group in groups for cell in group]
    if not cells:
        labels = " or ".join(label for label, _ in groups) or "the cell directory"
        print(f"No custom cells found under {labels} — running with the PDK "
              "standard cell library only.")
        return 0

    errors = 0
    warnings = 0
    all_problems: List[str] = []

    for label, group in groups:
        if not group:
            continue
        print(f"Custom cells under {label}/:")
        for cell in group:
            have = ", ".join(sorted(cell.views)) or "nothing"
            print(f"  {cell.name}: {have}")

            for view in cell.missing(REQUIRED_VIEWS):
                print(f"    ERROR   missing {view} view "
                      f"({EXTRA_KEY_BY_VIEW[view]})")
                errors += 1
            for view in cell.missing(RECOMMENDED_VIEWS):
                print(f"    warning missing {view} view "
                      f"({EXTRA_KEY_BY_VIEW[view]})")
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

    # LENIENT=1 downgrades LibreLane's checkers. It does not downgrade these:
    # DRT-0073 is a hard abort inside TritonRoute's pin access, so a cell that
    # fails a pin access rule takes the run down whatever this gate does.
    # Saying "rerun with LENIENT=1" to someone holding one would be a lie.
    unroutable = errors and any(
        marker in problem
        for problem in all_problems
        for marker in UNROUTABLE_MARKERS
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
        "--pdk-ext-dir",
        default=None,
        help="implementation/pdk_extension/, whose cells are named views "
        "rather than grouped by stem. Its cells are checked and offered to "
        "PnR exactly like the mined ones.",
    )
    parser.add_argument(
        "--lenient",
        action="store_true",
        help="Report problems but exit 0.",
    )
    args = parser.parse_args()

    groups = []
    if os.path.isdir(args.cells_dir):
        groups.append((args.cells_dir, collect_cells(args.cells_dir)))
    else:
        print(f"No custom cells directory at {args.cells_dir}/ — nothing to do.")
    if args.pdk_ext_dir is not None and os.path.isdir(args.pdk_ext_dir):
        groups.append((args.pdk_ext_dir,
                       collect_pdk_extension_cells(args.pdk_ext_dir)))
    if not groups:
        return 0
    return check_cells(groups, strict=not args.lenient)


if __name__ == "__main__":
    sys.exit(main())
