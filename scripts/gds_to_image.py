#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Render a cell layout to a PNG, with its
#                             footprint and a layer legend.
#
#  Adapted from aion_flow/tools/aion_layout_kimi/scripts/gds_to_image.py.
#  What is added here is the header and the legend: a bare render of a
#  standard cell is a coloured rectangle nobody can read without knowing
#  which colour is which layer and how big the thing is.
#
#  klayout.db + Pillow, so it runs headless on the host and needs no Qt and
#  no container.
# ================================================================

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from klayout import db
from PIL import Image, ImageDraw, ImageFont

RGBA = Tuple[int, int, int, int]
Pair = Tuple[int, int]

# Layer colours, (layer, datatype) -> (R, G, B, A). Unknown layers fall back to
# a deterministic colour so a layer nobody expected still shows up.
DEFAULT_LAYER_COLOURS: Dict[Pair, RGBA] = {
    (1, 0): (255, 50, 50, 60),      # Activ
    (5, 0): (50, 255, 50, 60),      # GatPoly
    (6, 0): (200, 200, 200, 60),    # Cont
    (7, 0): (255, 120, 60, 15),     # NSD
    (8, 0): (50, 100, 255, 60),     # Metal1
    (8, 2): (255, 200, 50, 60),     # Metal1 pin
    (10, 0): (50, 220, 220, 60),    # Metal2
    (10, 2): (255, 140, 60, 60),    # Metal2 pin
    (14, 0): (255, 150, 200, 15),   # PSD
    (19, 0): (255, 255, 255, 90),   # Via1
    (31, 0): (150, 50, 255, 15),    # NWell
    (46, 0): (100, 255, 255, 15),   # PWell
    (67, 0): (120, 160, 255, 60),   # Metal5
    (126, 0): (255, 215, 90, 70),   # TopMetal1
    (134, 0): (255, 150, 90, 70),   # TopMetal2
    (9, 0): (230, 230, 230, 25),    # Passiv
    (99, 0): (255, 60, 140, 12),    # Recog
    (189, 4): (180, 180, 180, 15),  # prBoundary
}

# Names for the legend, from aion_layout.tech.sg13g2_tech.layers. Kept as a
# literal so this script stays standalone.
LAYER_NAMES: Dict[Pair, str] = {
    (1, 0): "Activ",
    (5, 0): "GatPoly",
    (6, 0): "Cont",
    (7, 0): "NSD",
    (8, 0): "Metal1",
    (8, 2): "Metal1 pin",
    (8, 25): "Metal1 label",
    (10, 0): "Metal2",
    (10, 2): "Metal2 pin",
    (10, 25): "Metal2 label",
    (14, 0): "PSD",
    (19, 0): "Via1",
    (31, 0): "NWell",
    (46, 0): "PWell",
    (30, 0): "Metal3",
    (50, 0): "Metal4",
    (67, 0): "Metal5",
    (126, 0): "TopMetal1",
    (134, 0): "TopMetal2",
    (9, 0): "Passiv",
    (99, 0): "Recog",
    (189, 4): "prBoundary",
}

# Bottom-to-top draw order. Wells, implants and the outline go underneath the
# devices and the routing, so their transparency tints the image instead of
# washing out what sits on top of them.
DRAW_ORDER: Dict[Pair, int] = {
    (189, 4): 0,
    (99, 0): 0,
    (31, 0): 1,
    (46, 0): 2,
    (14, 0): 3,
    (7, 0): 4,
    (1, 0): 5,
    (5, 0): 6,
    (6, 0): 7,
    (8, 0): 8,
    (8, 2): 9,
    (8, 25): 10,
    (19, 0): 11,
    (10, 0): 12,
    (10, 2): 13,
    (10, 25): 14,
    (30, 0): 15,
    (50, 0): 16,
    (67, 0): 17,
    (126, 0): 18,
    (134, 0): 19,
    (9, 0): 20,
}

BACKGROUND_COLOUR: RGBA = (0, 0, 0, 255)
PANEL_COLOUR: RGBA = (24, 24, 28, 255)
TEXT_COLOUR: RGBA = (235, 235, 235, 255)
MUTED_COLOUR: RGBA = (150, 150, 155, 255)

#: SG13G2 CoreSite, for reporting a width in placement sites.
SITE_WIDTH_UM = 0.48
ROW_HEIGHT_UM = 3.78

#: The prBoundary is the placement footprint; the shape bounding box is not,
#: because wells and implants overhang the boundary on purpose so that abutted
#: neighbours share them. Reported separately, never interchangeably.
PRBOUNDARY = (189, 4)


def deterministic_colour(layer: int, datatype: int) -> RGBA:
    """A stable colour for a (layer, datatype) pair nobody gave one to."""
    hue = ((layer * 47 + datatype * 13) % 360) / 360.0
    saturation = 0.6 + ((layer + datatype) % 5) * 0.08
    value = 0.75 + ((layer * 7 + datatype) % 4) * 0.05
    return (*hsv_to_rgb(hue, saturation, value), 110)


def hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """Convert HSV in [0,1] to RGB in [0,255]."""
    i = int(h * 6.0)
    f = (h * 6.0) - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i %= 6
    rgb = {
        0: (v, t, p),
        1: (q, v, p),
        2: (p, v, t),
        3: (p, q, v),
        4: (t, p, v),
        5: (v, p, q),
    }[i]
    return tuple(int(c * 255) for c in rgb)


def _points_from_polygon(poly: db.Polygon) -> List[Tuple[int, int]]:
    """The (x, y) hull of a klayout Polygon."""
    points: List[Tuple[int, int]] = []
    hull_iter = iter(poly.each_point_hull())
    for item in hull_iter:
        if isinstance(item, db.Point):
            points.append((item.x, item.y))
        else:
            # Some klayout versions hand back flat x/y coordinates instead.
            points.append((int(item), int(next(hull_iter))))
    return points


def _box_points(box) -> List[Tuple[int, int]]:
    return [
        (box.left, box.bottom),
        (box.right, box.bottom),
        (box.right, box.top),
        (box.left, box.top),
    ]


def collect_polygons(
    layout: db.Layout,
    cell: db.Cell,
    layer_idx: int,
    trans: db.ICplxTrans = db.ICplxTrans(),
) -> List[List[Tuple[int, int]]]:
    """Flattened polygon points for one layer, recursing into instances."""
    polygons: List[List[Tuple[int, int]]] = []
    for shape in cell.shapes(layer_idx).each():
        if shape.is_polygon():
            polygons.append(_points_from_polygon(shape.polygon.transformed(trans)))
        elif shape.is_box():
            polygons.append(_box_points(shape.box.transformed(trans)))
        elif shape.is_path():
            polygons.append(_box_points(shape.path.bbox().transformed(trans)))

    for instance in cell.each_inst():
        polygons.extend(
            collect_polygons(layout, instance.cell, layer_idx, trans * instance.trans)
        )
    return polygons


def _draw_order(key: Pair) -> int:
    return DRAW_ORDER.get(key, 100 + key[0])


#: Faces to look for, best first.  Bold falls through to the regular list
#: rather than to Pillow's bitmap default, because that default ignores the
#: requested size -- a title asking for 35 px would come out at 11.
_BOLD_FACES = (
    "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "NotoSans-Bold.ttf",
    "DroidSans-Bold.ttf", "AdwaitaSans-Bold.ttf", "Cantarell-Bold.otf",
)
_REGULAR_FACES = (
    "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "NotoSans-Regular.ttf",
    "DroidSans.ttf", "AdwaitaSans-Regular.ttf", "Cantarell-Regular.otf",
)
_FONT_ROOTS = ("/usr/share/fonts", "/usr/local/share/fonts", "~/.fonts", "~/.local/share/fonts")


def _font(size: int, bold: bool = False):
    """A scalable face at ``size``, or Pillow's bitmap default as a last resort."""
    names = (_BOLD_FACES + _REGULAR_FACES) if bold else _REGULAR_FACES
    for name in names:
        for root in _FONT_ROOTS:
            base = Path(root).expanduser()
            if not base.is_dir():
                continue
            for path in base.rglob(name):
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    continue
    # Nothing scalable on this machine: at least ask for the right size.
    try:
        return ImageFont.load_default(size)
    except TypeError:               # Pillow < 10.1 has no size argument
        return ImageFont.load_default()


def _flatten(colour: RGBA, background: RGBA) -> Tuple[int, int, int]:
    """A layer's colour as it actually appears over the background."""
    alpha = colour[3] / 255.0
    return tuple(
        int(round(colour[i] * alpha + background[i] * (1.0 - alpha))) for i in range(3)
    )


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def measure(layout: db.Layout, top: db.Cell) -> Tuple[Optional[Tuple[float, float]], Tuple[float, float]]:
    """``(placement footprint or None, shape bounding box)`` in um.

    The footprint is the prBoundary, which is what the LEF ``SIZE`` and the
    Liberty ``area`` carry.  The bounding box is every shape drawn, wells and
    implants included, and is a different and larger number -- so it is
    returned separately rather than substituted.
    """
    dbu = layout.dbu
    bbox = top.bbox()
    shape_box = (bbox.width() * dbu, bbox.height() * dbu)

    index = layout.find_layer(*PRBOUNDARY)
    if index is None:
        return None, shape_box
    region = db.Region(top.begin_shapes_rec(index))
    if region.is_empty():
        return None, shape_box
    pr = region.bbox()
    return (pr.width() * dbu, pr.height() * dbu), shape_box


def render_gds(
    input_path: str,
    output_path: str,
    width: int = 1400,
    height: int = 1000,
    margin: int = 24,
    colours: Optional[Dict[Pair, RGBA]] = None,
    background: RGBA = BACKGROUND_COLOUR,
    include_layers: Optional[Sequence[Pair]] = None,
    exclude_layers: Optional[Sequence[Pair]] = None,
    no_insts: bool = False,
    title: Optional[str] = None,
    header: bool = True,
    legend: bool = True,
) -> str:
    """Render a GDS to ``output_path``; return the title used.

    ``width`` and ``height`` are the whole image.  The header and the legend
    are carved out of them and the layout gets what is left, so a caller asking
    for 1400x1000 gets a 1400x1000 file whatever the annotations cost.
    """
    colours = dict(DEFAULT_LAYER_COLOURS if colours is None else colours)

    layout = db.Layout()
    layout.read(input_path)
    top = layout.top_cell()
    if top is None:
        raise RuntimeError(f"{input_path}: no top cell")
    bbox = top.bbox()
    if bbox.empty():
        raise RuntimeError(f"{input_path}: top cell {top.name!r} draws nothing")

    name = title or top.name
    footprint, shape_box = measure(layout, top)

    # ---- what to draw, bottom to top -----------------------------------
    entries: List[Tuple[Pair, int]] = []
    for idx in layout.layer_indices():
        info = layout.get_info(idx)
        entries.append(((info.layer, info.datatype), idx))
    entries.sort(key=lambda e: (_draw_order(e[0]), e[0]))

    drawn: List[Tuple[Pair, List[List[Tuple[int, int]]]]] = []
    for key, idx in entries:
        if include_layers is not None and key not in include_layers:
            continue
        if exclude_layers is not None and key in exclude_layers:
            continue
        if no_insts:
            shapes = []
            for shape in top.shapes(idx).each():
                if shape.is_polygon():
                    shapes.append(_points_from_polygon(shape.polygon))
                elif shape.is_box():
                    shapes.append(_box_points(shape.box))
        else:
            shapes = collect_polygons(layout, top, idx)
        if shapes:
            drawn.append((key, shapes))

    # ---- panel geometry -------------------------------------------------
    canvas = Image.new("RGBA", (width, height), PANEL_COLOUR)
    scratch = ImageDraw.Draw(canvas)

    title_px = max(15, height // 28)
    body_px = max(11, height // 46)
    title_font = _font(title_px, bold=True)
    body_font = _font(body_px)

    pad = max(8, body_px // 2)
    header_h = 0
    if header:
        header_h = pad * 2 + title_px + body_px + pad // 2

    legend_w = 0
    swatch = body_px + 2
    if legend and drawn:
        labels = [
            f"{LAYER_NAMES.get(key, 'layer')}  {key[0]}/{key[1]}" for key, _ in drawn
        ]
        longest = max(_text_size(scratch, t, body_font)[0] for t in labels)
        legend_w = pad * 3 + swatch + longest

    view_w = max(1, width - legend_w)
    view_h = max(1, height - header_h)

    # ---- the layout itself ---------------------------------------------
    available_w = max(1, view_w - 2 * margin)
    available_h = max(1, view_h - 2 * margin)
    scale = min(
        available_w / bbox.width() if bbox.width() > 0 else 1.0,
        available_h / bbox.height() if bbox.height() > 0 else 1.0,
    )
    offset_x = margin + (available_w - bbox.width() * scale) / 2.0
    offset_y = header_h + margin + (available_h - bbox.height() * scale) / 2.0

    def to_img(x: int, y: int) -> Tuple[float, float]:
        return (offset_x + (x - bbox.left) * scale, offset_y + (bbox.top - y) * scale)

    view = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    view.paste(background, (0, header_h, view_w, height))
    for key, shapes in drawn:
        colour = colours.get(key) or deterministic_colour(*key)
        layer_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer_img)
        for poly in shapes:
            layer_draw.polygon([to_img(x, y) for x, y in poly], fill=colour)
        view = Image.alpha_composite(view, layer_img)
    canvas = Image.alpha_composite(canvas, view)

    draw = ImageDraw.Draw(canvas)

    # ---- header: what this is, and how big ------------------------------
    if header:
        draw.text((pad, pad), name, font=title_font, fill=TEXT_COLOUR)
        draw.text(
            (pad, pad + title_px + pad // 2),
            size_line(footprint, shape_box),
            font=body_font,
            fill=MUTED_COLOUR,
        )
        draw.line([(0, header_h - 1), (width, header_h - 1)], fill=(60, 60, 66, 255))

    # ---- legend ---------------------------------------------------------
    if legend_w:
        x0 = width - legend_w
        draw.line([(x0, header_h), (x0, height)], fill=(60, 60, 66, 255))
        y = header_h + pad
        draw.text((x0 + pad, y), "LAYERS", font=body_font, fill=MUTED_COLOUR)
        y += body_px + pad
        for key, _ in reversed(drawn):        # topmost layer first, as read
            colour = colours.get(key) or deterministic_colour(*key)
            box = [x0 + pad, y, x0 + pad + swatch, y + swatch]
            draw.rectangle(box, fill=_flatten(colour, background), outline=(90, 90, 96))
            draw.text(
                (x0 + pad * 2 + swatch, y + (swatch - body_px) // 2),
                f"{LAYER_NAMES.get(key, 'layer')}  {key[0]}/{key[1]}",
                font=body_font,
                fill=TEXT_COLOUR,
            )
            y += swatch + pad // 2

    canvas.convert("RGB").save(output_path)
    return name


def size_line(
    footprint: Optional[Tuple[float, float]],
    shape_box: Tuple[float, float],
) -> str:
    """The one-line size caption under the title."""
    if footprint is None:
        w, h = shape_box
        return (
            f"{w:.3f} x {h:.3f} um shape bounding box "
            "(no prBoundary drawn, so this is not the placement footprint)"
        )
    w, h = footprint
    sites = w / SITE_WIDTH_UM
    rows = h / ROW_HEIGHT_UM
    site_text = f"{sites:.0f}" if abs(sites - round(sites)) < 1e-3 else f"{sites:.2f}"
    row_text = "1 row" if abs(rows - 1.0) < 1e-3 else f"{rows:.2f} rows"
    return (
        f"{w:.3f} x {h:.3f} um  =  {w * h:.4f} um^2   "
        f"{site_text} sites x {row_text}   (prBoundary)"
    )


def parse_layer_list(value: str) -> List[Pair]:
    """Parse '1/0,5/0' into [(1, 0), (5, 0)]."""
    result: List[Pair] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split("/")
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                f"Invalid layer/datatype pair: {item!r} (expected L/D)"
            )
        result.append((int(parts[0]), int(parts[1])))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a GDS layout to a PNG with its size and a layer legend.",
    )
    parser.add_argument("input", help="Input GDS file")
    parser.add_argument("output", help="Output image file (PNG/JPEG/...)")
    parser.add_argument("--width", type=int, default=1400,
                        help="Total image width in pixels (default: 1400)")
    parser.add_argument("--height", type=int, default=1000,
                        help="Total image height in pixels (default: 1000)")
    parser.add_argument("--margin", type=int, default=24,
                        help="Margin around the layout in pixels (default: 24)")
    parser.add_argument("--title", help="Header title (default: the top cell name)")
    parser.add_argument("--no-header", action="store_true",
                        help="Omit the title and size header")
    parser.add_argument("--no-legend", action="store_true",
                        help="Omit the layer legend")
    parser.add_argument("--include-layers", type=parse_layer_list, metavar="L/D,L/D",
                        help="Only render these layer/datatype pairs")
    parser.add_argument("--exclude-layers", type=parse_layer_list, metavar="L/D,L/D",
                        help="Do not render these layer/datatype pairs")
    parser.add_argument("--no-insts", action="store_true",
                        help="Only draw shapes in the top cell, do not recurse")

    args = parser.parse_args()
    try:
        render_gds(
            input_path=args.input,
            output_path=args.output,
            width=args.width,
            height=args.height,
            margin=args.margin,
            include_layers=args.include_layers,
            exclude_layers=args.exclude_layers,
            no_insts=args.no_insts,
            title=args.title,
            header=not args.no_header,
            legend=not args.no_legend,
        )
        print(f"Saved image to {args.output}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
