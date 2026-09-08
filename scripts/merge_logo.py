#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Drop the logo macro into a hardened GDS,
#                             once it has proved nothing is in its way.
#
#  Why this is a post-step and not a MACROS entry
#  ----------------------------------------------
#  LibreLane places macros with Odb.ManualMacroPlacement, which works on
#  instances in the netlist.  A logo has no ports and no function, and such
#  an instance does not survive a VHDL front end: GHDL imports an unbound
#  component as an empty module, that module shadows the liberty blackbox
#  LibreLane reads for a MACROS entry, and yosys opt_clean deletes it.  With
#  no ports, with a connected input, and with keep / syn_keep /
#  keep_hierarchy on the instance -- removed every time, because GHDL
#  propagates none of those attributes.  So the art goes in here instead,
#  after the last tool that would optimise it away has run.
#
#  Why the art gets carved
#  -----------------------
#  There is no free layer inside a TinyTapeout tile.  TopMetal1 carries the
#  PDN's vertical straps -- PDN_VERTICAL_LAYER for ihp-sg13g2, 2.2 um wide on
#  a 38.87 um pitch, spanning the die.  TopMetal2 would be empty, because
#  TT sets PDN_MULTILAYER off, but tt-support-tools' precheck lists
#  TopMetal2.drawing among its forbidden_layers, so a project GDS containing
#  any is rejected.  Metal5 and below carry signal routing.
#
#  So the logo cannot avoid the straps by moving, and it must not touch
#  them: a floating island bridging a VPWR strap and a VGND strap shorts the
#  supply.  What it can do is get out of their way.  This subtracts every
#  shape already on the logo's layer, grown by the layer's minimum spacing,
#  from the art, and then opens the result by the minimum width so the
#  subtraction cannot leave a sliver thinner than the layer allows.  The
#  straps cost about 5.5 um out of every 38.87 -- the logo reads as itself
#  with hairlines through it, and every rule still holds.
#
#  --no-carve refuses on any overlap instead, which is the right setting if
#  you would rather move the logo than stripe it.
#
#  The GDS is the deliverable a shuttle consumes, so it is what gets the
#  logo.  The LEF is a separate view and describes the same die: --lef adds
#  the footprint to it as an obstruction, so a tool reading only the LEF
#  still knows that area of the layer is spoken for.
# ================================================================

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from klayout import db

sys.path.insert(0, str(Path(__file__).resolve().parent))
from logo_to_gds import ART_LAYERS, PRBOUNDARY  # noqa: E402  (sibling script)

Pair = Tuple[int, int]


class MergeError(RuntimeError):
    """Raised when the logo cannot be placed, or must not be."""


def parse_layer(text: str) -> Pair:
    """Parse 'L/D' into a (layer, datatype) pair."""
    parts = text.split("/")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected LAYER/DATATYPE, got {text!r}")
    return (int(parts[0]), int(parts[1]))


def _top_cell(layout: db.Layout, name: Optional[str], what: str) -> db.Cell:
    """The named top cell, or the only one there is."""
    if name is not None:
        cell = layout.cell(name)
        if cell is None:
            raise MergeError(f"{what}: no cell named {name!r}")
        return cell
    tops = layout.top_cells()
    if len(tops) != 1:
        names = ", ".join(c.name for c in tops)
        raise MergeError(
            f"{what}: {len(tops)} top cells ({names}); name the one to use"
        )
    return tops[0]


def occupied(
    layout: db.Layout,
    cell: db.Cell,
    layers: List[Pair],
    box: db.DBox,
) -> List[Tuple[Pair, db.DBox]]:
    """Shapes on ``layers`` that fall inside ``box``, flattened.

    Reported as bounding boxes rather than counted, because the useful thing
    when this fires is *where* the design went, so it can be looked at.
    """
    hits: List[Tuple[Pair, db.DBox]] = []
    region_box = db.Region(db.Box.from_dbox(box * (1.0 / layout.dbu)))
    for pair in layers:
        index = layout.find_layer(*pair)
        if index is None:                      # layer absent: nothing drawn on it
            continue
        region = db.Region(cell.begin_shapes_rec(index))
        region.merge()
        for polygon in region.interacting(region_box).each():
            hits.append((pair, polygon.bbox().to_dtype(layout.dbu)))
    return hits


def _region_of(layout: db.Layout, cell: db.Cell, pair: Pair) -> db.Region:
    """Every shape of one layer under ``cell``, flattened and merged."""
    index = layout.find_layer(*pair)
    if index is None:
        return db.Region()
    region = db.Region(cell.begin_shapes_rec(index))
    region.merge()
    return region


def carve(
    art: db.Region,
    blocked: db.Region,
    clearance_dbu: int,
    min_width_dbu: int,
) -> Tuple[db.Region, float]:
    """Cut ``blocked`` out of ``art``, and return what is left plus the loss.

    Two sizes, and both are the design rules rather than taste.  ``blocked``
    grows by the layer's minimum *spacing* before the subtraction, so what is
    removed is not the strap but the strap plus the gap the strap is owed.
    What remains is then opened by the minimum *width* -- eroded and regrown --
    which deletes any sliver the cut left too thin to be drawn and restores
    everything else exactly, because the art is rectilinear.
    """
    before = art.area()
    kept = art - blocked.sized(clearance_dbu)
    if min_width_dbu > 0:
        # Erode by just under half the rule so a feature exactly at the minimum
        # survives, the same tolerance logo_to_gds.py measures with.
        half = (min_width_dbu - 1) // 2
        kept = kept.sized(-half).sized(half)
    kept.merge()
    after = kept.area()
    return kept, (0.0 if before == 0 else 1.0 - after / before)


def merge(
    design_gds: Path,
    logo_gds: Path,
    output: Path,
    location: Tuple[float, float],
    design_top: Optional[str],
    logo_top: Optional[str],
    art_layers: List[Pair],
    do_carve: bool,
    clearance_um: float,
    min_width_um: float,
) -> Tuple[str, db.DBox]:
    """Place the logo in the design and write the result. Returns (cell, box)."""
    layout = db.Layout()
    layout.read(str(design_gds))
    top = _top_cell(layout, design_top, str(design_gds))

    logo_layout = db.Layout()
    logo_layout.read(str(logo_gds))
    logo_cell = _top_cell(logo_layout, logo_top, str(logo_gds))
    logo_box = logo_cell.dbbox()

    # The logo's own coordinates start at its prBoundary corner, so the
    # placement is a plain translation to the requested die coordinate.
    footprint = db.DBox(
        location[0], location[1],
        location[0] + logo_box.width(), location[1] + logo_box.height(),
    )
    die = top.dbbox()
    if not die.contains(footprint.p1) or not die.contains(footprint.p2):
        raise MergeError(
            f"the logo at {location[0]}, {location[1]} spans {footprint} "
            f"which does not fit inside the die {die}"
        )

    if layout.cell(logo_cell.name) is not None:
        raise MergeError(
            f"{design_gds} already contains a cell named {logo_cell.name!r} -- "
            "the logo looks merged already"
        )

    if not do_carve:
        hits = occupied(layout, top, art_layers, footprint)
        if hits:
            where = "\n".join(f"    {p[0]}/{p[1]} at {b}" for p, b in hits[:10])
            raise MergeError(
                f"{len(hits)} shape(s) already occupy the logo's footprint on "
                f"its own layer(s):\n{where}\n"
                "  drop --no-carve to cut the art around them instead, or move "
                "the logo."
            )

    # Shift the art to where it is going, then take the design's own geometry
    # out of it. The logo is flattened on the way in: it is a few polygons, and
    # a carved cell is no longer the cell the macro file describes.
    shift = db.DTrans(db.DVector(location[0] - logo_box.left,
                                 location[1] - logo_box.bottom)).to_itype(layout.dbu)
    target = layout.create_cell(logo_cell.name)
    clearance = int(round(clearance_um / layout.dbu))
    min_width = int(round(min_width_um / layout.dbu))

    for info in logo_layout.layer_infos():
        pair = (info.layer, info.datatype)
        if pair == PRBOUNDARY:
            # A macro's placement footprint means nothing once it is merged into
            # a die that has its own, and TT's precheck reads prBoundary to
            # decide what the project area is.
            continue
        art = _region_of(logo_layout, logo_cell, pair).transformed(shift)
        if pair in art_layers:
            blocked = _region_of(layout, top, pair) & db.Region(
                db.Box.from_dbox(footprint * (1.0 / layout.dbu)).enlarged(clearance)
            )
            art, lost = carve(art, blocked, clearance, min_width)
            if art.is_empty():
                raise MergeError(
                    f"carving {pair[0]}/{pair[1]} around the design left nothing "
                    "of the logo -- move it, or make it bigger than what is in "
                    "the way"
                )
            if lost:
                print(f"carved {pair[0]}/{pair[1]}: {lost:.1%} of the art removed "
                      f"to clear {clearance_um:.3f} um around what was there")
        target.shapes(layout.layer(*pair)).insert(art)

    top.insert(db.CellInstArray(target.cell_index(), db.Trans()))

    output.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(output))
    return top.name, footprint


_MACRO_END_RE = re.compile(r"^([ \t]*)END[ \t]+(\S+)[ \t]*$", re.MULTILINE)
_OBS_RE = re.compile(r"^[ \t]*OBS[ \t]*$", re.MULTILINE)


def add_lef_obstruction(
    lef: Path, macro: str, layer_name: str, box: db.DBox
) -> None:
    """Record the logo's footprint in the design's LEF as an obstruction.

    The GDS and the LEF are two views of one die, and only the GDS has been
    told about the logo.  Anything that reads the LEF -- the chip-level
    assembly, above all -- would otherwise believe that area of the layer is
    free.  An OBS rectangle is the smallest true statement that fixes it.
    """
    text = lef.read_text()
    rect = (f"      RECT {box.left:.3f} {box.bottom:.3f} "
            f"{box.right:.3f} {box.top:.3f} ;\n")
    entry = f"    LAYER {layer_name} ;\n{rect}"

    end = None
    for match in _MACRO_END_RE.finditer(text):
        if match.group(2) == macro:
            end = match
            break
    if end is None:
        raise MergeError(f"{lef}: no 'END {macro}' to insert an OBS before")

    body, tail = text[: end.start()], text[end.start() :]
    if _OBS_RE.search(body):
        # Extend the macro's existing OBS rather than opening a second one:
        # LEF allows only one per macro.
        insert = body.rindex("  END\n")
        body = body[:insert] + entry + body[insert:]
    else:
        body = body + f"  OBS\n{entry}  END\n"
    lef.write_text(
        f"# merge_logo.py: added an OBS on {layer_name} over the logo's "
        f"footprint; the art itself is in the GDS\n" + body + tail
        if not text.startswith("# merge_logo.py") else body + tail
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge the logo macro into a hardened GDS, after checking "
                    "that the design left its layer alone.",
    )
    parser.add_argument("design", help="the hardened GDS, e.g. flow/pnr_simple/gds/tt_um_aion.gds")
    parser.add_argument("logo", help="the logo GDS from `make logo`")
    parser.add_argument("-o", "--output", help="where to write (default: in place)")
    parser.add_argument("--at", required=True, metavar="X,Y",
                        help="lower-left corner of the logo, in microns")
    parser.add_argument("--design-top", help="top cell of the design GDS")
    parser.add_argument("--logo-top", help="top cell of the logo GDS")
    parser.add_argument("--layer", action="append", type=parse_layer,
                        metavar="L/D", default=None,
                        help="the logo's art layer, repeatable "
                             "(default: 126/0, TopMetal1)")
    parser.add_argument("--layer-name", default="TopMetal1",
                        help="LEF name of that layer, and the row of the "
                             "SG13G2 rule table the clearances come from "
                             "(default: TopMetal1)")
    parser.add_argument("--clearance", type=float, metavar="UM",
                        help="how far to keep the art off the design's own "
                             "geometry (default: the layer's min spacing)")
    parser.add_argument("--min-width", type=float, metavar="UM",
                        help="drop art thinner than this after carving "
                             "(default: the layer's min width)")
    parser.add_argument("--lef", help="also add the footprint to this LEF as an OBS")
    parser.add_argument("--lef-macro", help="macro to edit in --lef "
                                            "(default: the design top cell)")
    parser.add_argument("--no-carve", dest="carve", action="store_false",
                        help="refuse if the footprint is occupied, instead of "
                             "cutting the art around what is there")

    args = parser.parse_args()
    try:
        x, _, y = args.at.partition(",")
        location = (float(x), float(y))
    except ValueError:
        print(f"error: --at expects X,Y in microns, got {args.at!r}", file=sys.stderr)
        return 1

    design = Path(args.design)
    output = Path(args.output) if args.output else design
    rule = ART_LAYERS.get(args.layer_name)
    layers = args.layer if args.layer else (
        [(rule.gds_layer, rule.gds_datatype)] if rule else [(126, 0)]
    )
    if args.clearance is None and rule is None:
        print(f"error: --layer-name {args.layer_name!r} is not in the SG13G2 "
              "rule table, so --clearance and --min-width have no default",
              file=sys.stderr)
        return 1
    clearance = args.clearance if args.clearance is not None else rule.min_spacing_um
    min_width = args.min_width if args.min_width is not None else (
        rule.min_width_um if rule else 0.0
    )

    try:
        if output == design:
            backup = design.with_suffix(design.suffix + ".prelogo")
            if not backup.exists():
                shutil.copyfile(design, backup)
            source = backup
        else:
            source = design
        top, footprint = merge(
            design_gds=source,
            logo_gds=Path(args.logo),
            output=output,
            location=location,
            design_top=args.design_top,
            logo_top=args.logo_top,
            art_layers=layers,
            do_carve=args.carve,
            clearance_um=clearance,
            min_width_um=min_width,
        )
        art = ", ".join(f"{layer}/{datatype}" for layer, datatype in layers)
        how = (f"carved to {clearance:.3f} um clearance, min width "
               f"{min_width:.3f} um") if args.carve else "no overlap"
        print(f"merged {args.logo} into {top} at {footprint} "
              f"(art on {art}; {how})")
        print(f"wrote {output}")
        if args.lef:
            add_lef_obstruction(
                Path(args.lef), args.lef_macro or top, args.layer_name, footprint
            )
            print(f"wrote {args.lef} (OBS on {args.layer_name})")
    except (MergeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
