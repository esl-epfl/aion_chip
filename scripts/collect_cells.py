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
import bisect
import math
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

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

# Half the height of the pad each metal gets in the via stack the PDN drops
# onto both rails wherever a TopMetal1 strap crosses a row -- anywhere along a
# cell -- centred on the row line. Measured from step 7's placed GDS
# (VIA_via1_2_2200_440_1_5_410_410 .. VIA_via5_6_2200_440_1_2_840_840): 0.29 um
# tall on Metal2 and Metal4, 0.20 um on Metal3, 0.62 um on Metal5.
PDN_RAIL_PAD_HALF = {
    "Metal2": 0.145,
    "Metal3": 0.100,
    "Metal4": 0.145,
    "Metal5": 0.310,
}

# Half-height of a VDD/VSS rail: -0.22 .. 0.22 um about the row line.
RAIL_HALF = 0.22

# WIDTH of each routing metal in sg13g2_tech.lef: the narrowest wire the router
# draws, so the narrowest gap between other nets' metal it can pass.
METAL_WIDTH = {
    "Metal1": 0.16,
    "Metal2": 0.20,
    "Metal3": 0.20,
    "Metal4": 0.20,
    "Metal5": 0.20,
}

# Metal3 is HORIZONTAL on tracks y = 0.0 + 0.42k um (tracks.info).
TRACK_Y = (0.0, 0.42)

# The fewest Metal3 tracks a signal pin has to be able to put a Via2 on. One
# stalled detailed routing in step 7 and two routed clean: see check_pin_escape.
PIN_ESCAPE_TRACKS_MIN = 2

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


# The timing corners a cell's Liberty may be split into, spelled the way
# aion_layout's characterizer ends the file it writes for each
# (<CELL>_typ_1p20V_25C.lib, from characterize.Corner.tag) and the way
# ihp-sg13g2's CELL_LIBS wildcards end (*_typ_1p20V_25C). LibreLane times the
# design at nom_<corner> for each of them (STA_CORNERS).
LIB_CORNERS = ("typ_1p20V_25C", "slow_1p08V_125C", "fast_1p32V_m40C")


@dataclass
class Cell:
    name: str
    views: Dict[str, str] = field(default_factory=dict)
    # corner -> Liberty, for a cell characterized at every corner of
    # LIB_CORNERS (<CELL>_<corner>.lib) rather than once (<CELL>.lib, which
    # is views["lib"]). Kept apart from views because the two reach LibreLane
    # differently: see prepare_librelane_config.cell_library_config.
    corner_libs: Dict[str, str] = field(default_factory=dict)

    def missing(self, views) -> List[str]:
        return [v for v in views
                if v not in self.views and not (v == "lib" and self.corner_libs)]


def lib_corner(stem: str) -> Optional[str]:
    """The corner a Liberty file stem ends in, e.g. AION_x_typ_1p20V_25C -> typ_1p20V_25C."""
    return next((corner for corner in LIB_CORNERS
                 if stem.endswith(f"_{corner}") and len(stem) > len(corner) + 1), None)


def corner_lib_problems(cell: Cell):
    """(errors, warnings) about how a cell's Liberty covers the timing corners.

    A cell has either one Liberty read into every corner, or one per corner in
    LIB_CORNERS -- all of them, because a corner with no Liberty for a cell
    cannot link the netlist at all. Both at once is two timing models of the
    same cell in one corner.
    """
    errors, warnings = [], []
    if cell.corner_libs:
        absent = [corner for corner in LIB_CORNERS if corner not in cell.corner_libs]
        if absent:
            errors.append(
                f"{cell.name}: no Liberty for corner(s) {', '.join(absent)} "
                f"(has {', '.join(sorted(cell.corner_libs))}); STA times every "
                "corner in LIB_CORNERS, and a corner with no Liberty for this "
                "cell cannot link it. Re-export the cell with LAYOUT_CORNERS=all")
        if "lib" in cell.views:
            errors.append(
                f"{cell.name}: both {os.path.basename(cell.views['lib'])} and a "
                "Liberty per corner; that is two timing models of the cell in "
                "one corner. Remove the stale one, or re-publish the cell")
    elif "lib" in cell.views:
        warnings.append(
            f"{cell.name}: one Liberty for every corner, so STA times it with "
            "the same data at slow and fast. Export with LAYOUT_CORNERS=all "
            "for a Liberty per corner")
    return errors, warnings


def published_lib_problem(cell: str, names) -> Optional[str]:
    """Why the Liberty files exported for `cell` cannot be published, or None.

    Step 6 publishes either <cell>.lib or exactly one <cell>_<corner>.lib per
    corner in LIB_CORNERS; anything else is a set collect_cells() would group
    into a cell with a corner missing, or a cell with two models of a corner.
    """
    names = sorted(names)
    single = [f"{cell}.lib"]
    per_corner = sorted(f"{cell}_{corner}.lib" for corner in LIB_CORNERS)
    if names in (single, per_corner):
        return None
    return (f"{len(names)} Liberty file(s) exported ({', '.join(names) or 'none'}); "
            f"`make pnr` takes {single[0]} or exactly one per corner: "
            f"{', '.join(per_corner)}")


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
            # <CELL>_<corner>.lib is one corner of <CELL>, not a cell of its own.
            corner = lib_corner(stem) if view == "lib" else None
            if corner is not None:
                stem = stem[: -len(corner) - 1]
            cell = by_name.setdefault(stem, Cell(name=stem))
            path = os.path.join(root, filename)
            if corner is not None:
                if corner in cell.corner_libs:
                    print(
                        f"Warning: duplicate {corner} Liberty for cell '{stem}': "
                        f"keeping {cell.corner_libs[corner]}, ignoring {path}",
                        file=sys.stderr,
                    )
                else:
                    cell.corner_libs[corner] = path
                continue
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
        # pdk_cell.py --corners all publishes <CELL>_<corner>.lib instead of
        # <CELL>.lib; named exactly like the rest, never inferred.
        for corner in LIB_CORNERS:
            path = os.path.join(directory, f"{name}_{corner}.lib")
            if os.path.isfile(path):
                cell.corner_libs[corner] = path
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


def shown_rects(offenders, clearance) -> str:
    """Up to three offending (owner, x1, y1, x2, y2) shapes, nearest first."""
    offenders = sorted(offenders, key=lambda shape: clearance(*shape[1:]))
    shown = ", ".join(f"{owner} RECT {x1:.3f} {y1:.3f} {x2:.3f} {y2:.3f}"
                      for owner, x1, y1, x2, y2 in offenders[:3])
    more = f" and {len(offenders) - 3} more" if len(offenders) > 3 else ""
    return shown + more


def check_abutment(macro: str, body: str, width: float, height: float) -> List[str]:
    """Metal must leave room for the PDN's rail vias and the abutted neighbour.

    Two rules, both invisible in a cell on its own -- DRC-clean, LVS-clean, pin
    access clean -- and both measured in step 7 on cells that were:

      * Metal2..Metal5 keep PDN_RAIL_PAD_HALF plus the layer's spacing from the
        rail lines at y = 0 and y = height. Seven AION cells drew Metal2 at
        y = 0.11 um, under the PDN's 0.145 um Metal2 pad: 130 nets shorted to
        VGND and 111 M2.b / 4 M2.a errors.
      * every routing metal keeps half its spacing from x = 0 and x = width,
        because the neighbour abutted there is held to only the other half.
        AION_xnor2_xor2_7 drew Metal1 10 nm from its right edge: 50 M1.b
        errors. The VDD/VSS rails are exempt; they are meant to join.

    Graded on every net, pins and OBS alike. All 84 PDK sg13g2_stdcell cells
    pass. aion_layout's metrics.lef_abutment_problems applies the same rules
    inside the drawing loop; the two must stay in step.
    """
    shapes = []
    for pin, pin_body in PIN_BLOCK_RE.findall(body):
        use = USE_RE.search(pin_body)
        power = (use.group(1).upper() in ("POWER", "GROUND") if use is not None
                 else pin.upper() in ("VDD", "VSS"))
        shapes.extend((f"PIN {pin}", power, rect) for rect in layer_rects(pin_body))
    obs_block = OBS_BLOCK_RE.search(body)
    if obs_block is not None:
        shapes.extend(("OBS", False, rect) for rect in layer_rects(obs_block.group(1)))

    tol = 5e-4
    problems = []
    for layer in ROUTING_LAYERS:
        rects = [(owner, power, rect) for owner, power, rect in shapes if rect[0] == layer]

        if layer in PDN_RAIL_PAD_HALF:
            keep = PDN_RAIL_PAD_HALF[layer] + METAL_SPACING[layer]
            near = [(owner, x1, y1, x2, y2) for owner, _, (_, x1, y1, x2, y2) in rects
                    if y1 < keep - tol or y2 > height - keep + tol]
            if near:
                shown = shown_rects(near, lambda x1, y1, x2, y2: min(y1, height - y2))
                problems.append(
                    f"{macro}: {layer} within {keep:.3f} um of a rail line: {shown}. "
                    f"Keep {layer} inside y = {keep:.3f} .. {height - keep:.3f}. In "
                    "PnR the power grid drops a via stack onto both rails, anywhere "
                    f"along the cell, with a {layer} pad reaching "
                    f"{PDN_RAIL_PAD_HALF[layer]:.3f} um from the rail line: nearer "
                    f"than {METAL_SPACING[layer]} um to it is a spacing error, "
                    "touching it shorts the net to VDD or VSS")

        half = METAL_SPACING[layer] / 2
        edge = [(owner, x1, y1, x2, y2) for owner, power, (_, x1, y1, x2, y2) in rects
                if not (power and any(y1 >= line - RAIL_HALF - tol
                                      and y2 <= line + RAIL_HALF + tol
                                      for line in (0.0, height)))
                and (x1 < half - tol or x2 > width - half + tol)]
        if edge:
            shown = shown_rects(edge, lambda x1, y1, x2, y2: min(x1, width - x2))
            problems.append(
                f"{macro}: {layer} within {half:.3f} um of the left or right cell "
                f"edge: {shown}. Keep {layer} inside x = {half:.3f} .. "
                f"{width - half:.3f} -- half the {METAL_SPACING[layer]} um spacing, "
                "because the cell abutted on that side is held to only the other "
                "half; nearer, the placed design has a spacing error at every such "
                "abutment. The VDD/VSS rails are exempt")
    return problems


def nm(um: float) -> int:
    return round(um * 1000.0)


def grown(shapes, layer, half_x, half_y):
    """The layer's (owner, layer, x1, y1, x2, y2) nm shapes grown, as open boxes.

    Grown by the layer's spacing plus half of the wire or via that has to clear
    them, other nets' shapes become the region that wire's or via's centre may
    not enter. The boundary itself is exactly minimum spacing, and free.
    """
    return [(x1 - half_x, y1 - half_y, x2 + half_x, y2 + half_y)
            for _, shape_layer, x1, y1, x2, y2 in shapes if shape_layer == layer]


def free_spans(lo, hi, blocked):
    """[lo, hi] minus the open blocked spans: the pieces of positive length."""
    free = []
    at = lo
    for start, end in sorted(blocked):
        if min(start, hi) > at:
            free.append((at, min(start, hi)))
        at = max(at, end)
        if at >= hi:
            break
    if at < hi:
        free.append((at, hi))
    return free


class FreeSpace:
    """Where the centre of a wire may sit inside a cell, and which of it connects.

    The cell is cut into horizontal bands at every edge of every box. In a band
    the free centres are a few x spans: the cell width minus the blocked boxes
    covering the band, plus the pin's own metal, which its wire may always run
    along. Spans of adjacent bands that overlap by a positive length are one
    connected piece. A gap exactly one wire wide between two other nets' shapes
    is a span of length zero and connects nothing: a route that has to hold
    minimum spacing on both sides at once is not a way out to count on.
    """

    def __init__(self, width, height, blocked, own=()):
        own = list(own)
        cuts = {0, height}
        for box in blocked + own:
            cuts.update(y for y in (box[1], box[3]) if 0 < y < height)
        self.ys = sorted(cuts)
        self.bands = []
        for lo, hi in zip(self.ys, self.ys[1:]):
            spans = free_spans(0, width, [(x1, x2) for x1, y1, x2, y2 in blocked
                                          if y1 <= lo and y2 >= hi])
            spans += [(max(x1, 0), min(x2, width)) for x1, y1, x2, y2 in own
                      if y1 <= lo and y2 >= hi]
            merged = []
            for start, end in sorted(spans):
                if end <= start:
                    continue
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            self.bands.append(merged)

        first = [0]
        for spans in self.bands:
            first.append(first[-1] + len(spans))
        parent = list(range(first[-1]))

        def root(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        for band in range(len(self.bands) - 1):
            for i, (a1, a2) in enumerate(self.bands[band]):
                for j, (b1, b2) in enumerate(self.bands[band + 1]):
                    if a1 < b2 and b1 < a2:
                        parent[root(first[band] + i)] = root(first[band + 1] + j)
        self.pieces = [[root(first[band] + i) for i in range(len(spans))]
                       for band, spans in enumerate(self.bands)]

    def piece(self, x, y):
        """The connected piece the free centre (x, y) is in, or None."""
        band = bisect.bisect_right(self.ys, y) - 1
        for b in (band, band - 1):
            if 0 <= b < len(self.bands) and self.ys[b] <= y <= self.ys[b + 1]:
                for (x1, x2), piece in zip(self.bands[b], self.pieces[b]):
                    if x1 <= x <= x2:
                        return piece
        return None

    def centres(self):
        """One point inside every free span of every band."""
        for band, spans in enumerate(self.bands):
            y = (self.ys[band] + self.ys[band + 1]) / 2
            for x1, x2 in spans:
                yield (x1 + x2) / 2, y

    def extent(self, pieces):
        """The bounding box of pieces, or None when there are none."""
        wanted = set(pieces)
        boxes = [(x1, self.ys[band], x2, self.ys[band + 1])
                 for band, spans in enumerate(self.bands)
                 for (x1, x2), piece in zip(spans, self.pieces[band])
                 if piece in wanted]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))


def via_blocked(others, lower, upper, shape):
    """Where a via of shape may not be centred, for its metal below and above."""
    (below_w, below_h), (above_w, above_h) = shape
    below, above = nm(METAL_SPACING[lower]), nm(METAL_SPACING[upper])
    return (grown(others, lower, below + nm(below_w / 2), below + nm(below_h / 2))
            + grown(others, upper, above + nm(above_w / 2), above + nm(above_h / 2)))


def pin_escape(width, height, own, others):
    """The Metal3 tracks a pin can put a Via2 on, and the Metal2 it reaches.

    A wire of the pin's net runs on Metal1 and Metal2 wherever its centre keeps
    spacing plus half the wire's width from every other net, and along the
    pin's own metal; it changes layer through a Via1 wherever both of the via's
    metal shapes clear the other nets. Every Metal2 piece reached that way is
    searched for a Via2 centred on a Metal3 track y = 0.42k with both of its
    metal shapes clear. Returns those tracks in nm, and the bounding box of the
    Metal2 centres the pin reaches.
    """
    wire = {}
    for layer in ("Metal1", "Metal2"):
        half = nm(METAL_SPACING[layer]) + nm(METAL_WIDTH[layer] / 2)
        wire[layer] = FreeSpace(
            width, height, grown(others, layer, half, half),
            [(x1, y1, x2, y2) for _, shape_layer, x1, y1, x2, y2 in own
             if shape_layer == layer])

    reached = set()
    for _, layer, x1, y1, x2, y2 in own:
        if layer in wire:
            piece = wire[layer].piece((x1 + x2) / 2, (y1 + y2) / 2)
            if piece is not None:
                reached.add((layer, piece))

    links = {}
    for shape in VIA_SHAPES:
        vias = FreeSpace(width, height, via_blocked(others, "Metal1", "Metal2", shape))
        for x, y in vias.centres():
            below, above = wire["Metal1"].piece(x, y), wire["Metal2"].piece(x, y)
            if below is not None and above is not None:
                links.setdefault(("Metal1", below), set()).add(("Metal2", above))
                links.setdefault(("Metal2", above), set()).add(("Metal1", below))
    todo = list(reached)
    while todo:
        for node in links.get(todo.pop(), ()):
            if node not in reached:
                reached.add(node)
                todo.append(node)

    offset, pitch = (nm(v) for v in TRACK_Y)
    lines = [offset + k * pitch for k in range(height // pitch + 1)
             if 0 < offset + k * pitch < height]
    tracks = set()
    for shape in VIA_SHAPES:
        blocked = via_blocked(others, "Metal2", "Metal3", shape)
        for y in lines:
            if y in tracks:
                continue
            spans = free_spans(0, width, [(x1, x2) for x1, y1, x2, y2 in blocked
                                          if y1 < y < y2])
            if any(("Metal2", wire["Metal2"].piece((x1 + x2) / 2, y)) in reached
                   for x1, x2 in spans):
                tracks.add(y)

    metal2 = wire["Metal2"].extent(piece for layer, piece in reached if layer == "Metal2")
    return tuple(sorted(tracks)), metal2


def pin_escape_problem(macro, pin, tracks, metal2, others, height) -> str:
    """Why pin has too few ways up, naming the metal that closes the rest.

    For each track within half a pitch of the Metal2 the pin reaches, the other
    net's shape that keeps a Via2 off the longest stretch of it is named: the
    bar across the box, not the riser that clips one end of it.
    """
    count = len(tracks)
    on = f" (y = {', '.join(f'{y / 1000:.3f}' for y in tracks)} um)" if tracks else ""
    text = (f"{macro}: PIN {pin} can put a Via2 on {count} Metal3 "
            f"track{'' if count == 1 else 's'}{on}; detailed routing needs at "
            f"least {PIN_ESCAPE_TRACKS_MIN}")
    closed = []
    if metal2 is None:
        text += (": no Via1 from the Metal1 its wire can reach lands on Metal2 "
                 "clear of other nets")
    else:
        offset, pitch = (nm(v) for v in TRACK_Y)
        narrow = {
            "Metal2": min(min(below) for below, _ in VIA_SHAPES),
            "Metal3": min(min(above) for _, above in VIA_SHAPES),
        }
        x1, y1, x2, y2 = metal2
        for k in range(math.ceil((y1 - pitch / 2 - offset) / pitch),
                       math.floor((y2 + pitch / 2 - offset) / pitch) + 1):
            y = offset + k * pitch
            if not 0 < y < height or y in tracks:
                continue
            # Grown for the narrower side of a Via2 on both axes, so a shape
            # named here keeps every one of the four shapes off that stretch.
            longest = None
            for owner, layer, ox1, oy1, ox2, oy2 in others:
                if layer not in narrow:
                    continue
                reach = nm(METAL_SPACING[layer]) + nm(narrow[layer] / 2)
                covered = min(ox2 + reach, x2) - max(ox1 - reach, x1)
                if oy1 - reach < y < oy2 + reach and covered > 0:
                    if longest is None or covered > longest[0]:
                        longest = (covered, owner, layer, ox1, oy1, ox2, oy2)
            if longest is not None:
                _, owner, layer, ox1, oy1, ox2, oy2 = longest
                closed.append(
                    f"y = {y / 1000:.3f} by {layer} of {owner} (RECT {ox1 / 1000:.3f} "
                    f"{oy1 / 1000:.3f} {ox2 / 1000:.3f} {oy2 / 1000:.3f})")
        text += (f": its wire can reach Metal2 only with its centre in x = "
                 f"{x1 / 1000:.3f} .. {x2 / 1000:.3f}, y = {y1 / 1000:.3f} .. "
                 f"{y2 / 1000:.3f} um")
        if closed:
            text += f", and a Via2 is kept off track {'; '.join(closed)}"
    fix = ("Move the metal named above so that a Via2 (Metal2 enclosure 0.29 x "
           "0.21 um, 0.21 um clear of other nets' Metal2) fits on a second track"
           if closed else
           "Clear Metal1 and Metal2 around the pin so that a Via2 (Metal2 "
           "enclosure 0.29 x 0.21 um, 0.21 um clear of other nets' Metal2) fits "
           "on two tracks")
    return (f"{text}. Walled in like that, every route out of the pin goes up "
            "through those few Via2 sites, and the router has nothing to trade "
            "when a neighbour's wire needs one: step 7 stalled at ~400 Metal2 "
            "shorts, at every die size, on AION_xor2_5, AION_xor2_8 and "
            "AION_xnor2_xor2_9, whose inner pins each had one such track, and "
            f"routed clean when the same pins had two. {fix}, y = 0.42k um, or "
            "run the pin's own Metal2 out of the enclosure. DRC, LVS and pin "
            "access do not see this")


def check_pin_escape(macro: str, body: str, width: float, height: float) -> List[str]:
    """Every signal pin must have at least two ways up out of the cell.

    check_pin_access asks whether a via reaches a pin at all. This asks whether
    the router can get the pin's wire out: from the pin, along free Metal1 and
    Metal2 and through Via1 wherever they clear the other nets, to a Via2 on a
    Metal3 track. A pin has to reach PIN_ESCAPE_TRACKS_MIN distinct tracks.

    Calibrated on one step-7 placement, 2026-09-14. AION_xor2_5 (I0, I2),
    AION_xor2_8 (I0) and AION_xnor2_xor2_9 (I1) each had an inner pin boxed in
    by a neighbour pin's U-shaped Metal2 below and an obstruction bar above,
    with a Via2 fitting on one track only. Detailed routing plateaued at ~415
    violations for 60+ iterations at every die size, 433 of the 498 markers at
    iteration 10 on those three masters; the same instances swapped to the
    abstracts with a second track routed to 0 by iteration 8. This rule gives
    exactly those four pins one track, the old abstracts two, every other pin
    of the ten mined cells four or more, and all 283 signal pins of the PDK
    library eight -- the same with up to 30 nm more room than minimum spacing
    required. Metal1 counts as a route of its own because TritonRoute takes
    planar Metal1 access out of a crowded spot; pins on Metal3 or above are not
    graded. aion_layout's metrics.lef_pin_escape_problems applies the same rule
    inside the drawing loop; the two must stay in step.
    """
    def shapes(owner, block):
        return [(owner, layer, nm(x1), nm(y1), nm(x2), nm(y2))
                for layer, x1, y1, x2, y2 in layer_rects(block)]

    obs_block = OBS_BLOCK_RE.search(body)
    obs = shapes("OBS", obs_block.group(1)) if obs_block else []
    pin_blocks = PIN_BLOCK_RE.findall(body)
    pins = {pin: shapes(f"PIN {pin}", pin_body) for pin, pin_body in pin_blocks}

    problems = []
    for pin, pin_body in pin_blocks:
        use = USE_RE.search(pin_body)
        if use is not None and use.group(1).upper() in ("POWER", "GROUND"):
            continue
        if use is None and pin.upper() in ("VDD", "VSS"):
            continue
        layers = {shape[1] for shape in pins[pin]}
        # No routing geometry is check_pin_access's to report; a pin already on
        # Metal3 or above is past the Via2 counted here.
        if (not layers & {"Metal1", "Metal2"}
                or layers & {"Metal3", "Metal4", "Metal5"}):
            continue
        others = obs + [shape for name, rects in pins.items() if name != pin
                        for shape in rects]
        tracks, metal2 = pin_escape(nm(width), nm(height), pins[pin], others)
        if len(tracks) < PIN_ESCAPE_TRACKS_MIN:
            problems.append(pin_escape_problem(macro, pin, tracks, metal2, others,
                                               nm(height)))
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
            problems.extend(check_abutment(macro, body, width, height))
            problems.extend(check_pin_escape(macro, body, width, height))

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
            have = sorted(cell.views)
            if cell.corner_libs:
                have.append(f"lib ({', '.join(sorted(cell.corner_libs))})")
            print(f"  {cell.name}: {', '.join(have) or 'nothing'}")

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
            libs = ([cell.views["lib"]] if "lib" in cell.views else []) + [
                cell.corner_libs[corner] for corner in sorted(cell.corner_libs)]
            for lib in libs:
                for problem in check_lib(lib, cell.name):
                    print(f"    ERROR   {problem}")
                    errors += 1
            corner_errors, corner_warnings = corner_lib_problems(cell)
            for problem in corner_errors:
                print(f"    ERROR   {problem}")
                errors += 1
            for problem in corner_warnings:
                print(f"    warning {problem}")
                warnings += 1

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
