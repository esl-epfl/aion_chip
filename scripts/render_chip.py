#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Render a hardened chip GDS: the whole die, a
#                             map of where the AION cells landed, and one
#                             zoom per AION cell type.
#
#  gds_to_image.py draws a single standard cell by flattening its polygons
#  in Python.  That does not scale to a die -- a hardened tt_um_aion carries
#  ~600k shapes across 31 layers, and at 2400 px for a 660 um die every one
#  of them is a sub-pixel smear anyway.  So the layout itself is drawn by
#  klayout's own C++ renderer (klayout.lay, offscreen: no Qt display, no
#  container), and Pillow is used only for what that renderer has no opinion
#  about -- the header, the legend and the AION overlay.
#
#  The chrome deliberately borrows gds_to_image's fonts, panel colours and
#  layer names, so a cell PNG from step 6 and a chip PNG from step 8 read as
#  the same family of pictures.
#
#  Three pictures, because they answer three different questions:
#
#    <top>.chip.png    what the die looks like, every layer, real colours
#    <top>.aion.png    where the AION cells landed, everything else dimmed
#    <top>.<CELL>.png  what one AION cell looks like abutted to its
#                      neighbours, at a zoom where you can actually read it
# ================================================================

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from klayout import db
from PIL import Image, ImageDraw

# The chrome is shared with the cell renderer on purpose: same faces, same
# panel, same layer names, so the two sets of pictures sit together. The
# underscored helpers are private to that module only in the sense that no
# command line reaches them; this is its sibling, not a third party.
from gds_to_image import (BACKGROUND_COLOUR, LAYER_NAMES as CELL_LAYER_NAMES,
                          MUTED_COLOUR, PANEL_COLOUR, ROW_HEIGHT_UM,
                          SITE_WIDTH_UM, TEXT_COLOUR, _flatten, _font,
                          _text_size, deterministic_colour)

RGB = Tuple[int, int, int]
RGBA = Tuple[int, int, int, int]
Pair = Tuple[int, int]

#: The prBoundary is the placement footprint. Same layer, same reason, as in
#: gds_to_image: a cell's shape bounding box overhangs it by design.
PRBOUNDARY = (189, 4)

# ---------------------------------------------------------------------
# Layer colours for a die-sized view.
#
# gds_to_image's palette is tuned for one cell seen from 2 um away: low alpha,
# so wells tint rather than cover. At die scale nothing is ever seen through
# anything, and what you want to read instead is the routing *stack* -- so
# these are opaque and ordered as a spectrum by metal level, blue at Metal1
# through to violet at TopMetal2. Front end is deliberately dark: at this zoom
# diffusion and poly are texture, not information.
CHIP_LAYER_COLOURS: Dict[Pair, RGB] = {
    (31, 0):  (0x2a, 0x20, 0x3c),   # NWell
    (46, 0):  (0x1c, 0x2c, 0x30),   # PWell
    (14, 0):  (0x3a, 0x1e, 0x2c),   # PSD
    (7, 0):   (0x3a, 0x26, 0x1c),   # NSD
    (1, 0):   (0x5a, 0x2e, 0x2e),   # Activ
    (5, 0):   (0x2e, 0x5a, 0x2e),   # GatPoly
    (6, 0):   (0x50, 0x50, 0x50),   # Cont
    (8, 0):   (0x40, 0x60, 0xff),   # Metal1
    (19, 0):  (0x90, 0xa0, 0xff),   # Via1
    (10, 0):  (0x30, 0xd0, 0xd0),   # Metal2
    (29, 0):  (0x88, 0xe8, 0xe8),   # Via2
    (30, 0):  (0x40, 0xd0, 0x60),   # Metal3
    (49, 0):  (0x98, 0xe8, 0xa8),   # Via3
    (50, 0):  (0xd0, 0xd0, 0x40),   # Metal4
    (66, 0):  (0xe8, 0xe8, 0x98),   # Via4
    (67, 0):  (0xff, 0x90, 0x30),   # Metal5
    (125, 0): (0xff, 0xc0, 0x88),   # TopVia1
    (126, 0): (0xff, 0x40, 0x60),   # TopMetal1
    (133, 0): (0xff, 0x98, 0xa8),   # TopVia2
    (134, 0): (0xc0, 0x60, 0xff),   # TopMetal2
}

#: The upper stack, which a single cell never carries and gds_to_image
#: therefore never had to name.
CHIP_LAYER_NAMES: Dict[Pair, str] = {
    **CELL_LAYER_NAMES,
    (29, 0):  "Via2",
    (30, 0):  "Metal3",
    (49, 0):  "Via3",
    (50, 0):  "Metal4",
    (66, 0):  "Via4",
    (67, 0):  "Metal5",
    (125, 0): "TopVia1",
    (126, 0): "TopMetal1",
    (133, 0): "TopVia2",
    (134, 0): "TopMetal2",
}

#: Never drawn on a die view. Pins and labels are duplicates of the metal
#: underneath them, prBoundary is 11738 overlapping rectangles of pure haze,
#: and the rest are recognition layers with no physical meaning here.
CHIP_HIDDEN_DATATYPES = (2, 25)
CHIP_HIDDEN_LAYERS = {PRBOUNDARY, (99, 31), (63, 0)}

#: Bottom-to-top, so the routing lands on the devices and not under them.
#: Anything unlisted goes on top, ordered by layer number.
CHIP_DRAW_ORDER: List[Pair] = [
    (31, 0), (46, 0), (14, 0), (7, 0), (1, 0), (5, 0), (6, 0),
    (8, 0), (19, 0), (10, 0), (29, 0), (30, 0), (49, 0), (50, 0),
    (66, 0), (67, 0), (125, 0), (126, 0), (133, 0), (134, 0),
]

#: Drawn as an outline instead of a fill, for the zoomed views: everything
#: above Metal1. Solid, they win -- a TopMetal2 power rail is tens of microns
#: wide and lies as an opaque band across the very cell the picture exists to
#: show, and the signal routing above the cell hides the cell. Hollow, the
#: cell reads through its own M1 and the routing still says where it goes.
ZOOM_HOLLOW: Tuple[Pair, ...] = ((10, 0), (29, 0), (30, 0), (49, 0), (50, 0),
                                 (66, 0), (67, 0), (125, 0), (126, 0),
                                 (133, 0), (134, 0))

# ---------------------------------------------------------------------
# The placement map's palette. Three classes, because a die is mostly not
# logic and saying so is the point: fill and decap are area the flow had to
# spend, not area anything computes in.
AION_COLOURS: Tuple[RGB, ...] = (
    (0xff, 0x62, 0x3d),     # AION cell type 1
    (0x3d, 0xd6, 0xff),     # 2
    (0xff, 0xd2, 0x3d),     # 3
    (0x8b, 0xff, 0x6a),     # 4
    (0xff, 0x6a, 0xd2),     # 5
    (0x9d, 0x8b, 0xff),     # 6
)
LOGIC_COLOUR: RGB = (0x3c, 0x48, 0x62)
FILLER_COLOUR: RGB = (0x26, 0x2a, 0x34)
DIE_COLOUR: RGB = (0x6a, 0x6a, 0x76)
CORE_COLOUR: RGB = (0x44, 0x48, 0x52)

#: Masters that occupy a site without computing anything. Substring match on
#: the master name, which is how SG13G2 spells all of them.
FILLER_HINTS = ("fill", "decap", "antenna", "tie", "tap")

GRID_COLOUR: RGBA = (60, 60, 66, 255)


# =====================================================================
# Reading the layout
# =====================================================================
@dataclass
class Placement:
    """One placed instance of one master, in um, in top-cell coordinates."""

    master: str
    box: db.DBox
    klass: str          # aion | logic | filler


@dataclass
class ChipStats:
    top: str
    die: db.DBox
    core: Optional[db.DBox]
    counts: Dict[str, int] = field(default_factory=dict)
    areas: Dict[str, float] = field(default_factory=dict)
    aion_counts: Dict[str, int] = field(default_factory=dict)
    aion_areas: Dict[str, float] = field(default_factory=dict)

    @property
    def instances(self) -> int:
        return sum(self.counts.values())

    @property
    def aion(self) -> int:
        return self.counts.get("aion", 0)

    def as_dict(self) -> dict:
        return {
            "top": self.top,
            "die_um": [self.die.width(), self.die.height()],
            "die_area_um2": self.die.width() * self.die.height(),
            "core_um": ([self.core.width(), self.core.height()]
                        if self.core else None),
            "core_area_um2": (self.core.width() * self.core.height()
                              if self.core else None),
            "instances": self.instances,
            "instances_by_class": dict(self.counts),
            "area_um2_by_class": {k: round(v, 3) for k, v in self.areas.items()},
            "aion_instances_by_cell": dict(self.aion_counts),
            "aion_area_um2_by_cell": {k: round(v, 3)
                                      for k, v in self.aion_areas.items()},
        }


def footprint(layout: db.Layout, cell: db.Cell) -> Optional[db.Box]:
    """A master's placement footprint in dbu: its prBoundary, or None.

    None is the honest answer for a via or a routing-only cell, and it is
    what keeps those out of the placement map without having to guess from
    their names.
    """
    index = layout.find_layer(*PRBOUNDARY)
    if index is None:
        return None
    region = db.Region(cell.begin_shapes_rec(index))
    return None if region.is_empty() else region.bbox()


def classify(master: str, prefix: str) -> str:
    if master.startswith(prefix):
        return "aion"
    low = master.lower()
    return "filler" if any(h in low for h in FILLER_HINTS) else "logic"


def scan(layout: db.Layout, top: db.Cell, prefix: str) -> Tuple[List[Placement], ChipStats]:
    """Every placed cell in `top`, classified, plus the totals.

    Only masters that draw a prBoundary are placements; that single test
    drops the via arrays, which outnumber the standard cells six to one and
    are not placed anywhere.
    """
    dbu = layout.dbu
    boxes: Dict[str, Optional[db.Box]] = {}
    placements: List[Placement] = []

    for inst in top.each_inst():
        master = inst.cell.name
        if master not in boxes:
            boxes[master] = footprint(layout, inst.cell)
        local = boxes[master]
        if local is None:
            continue
        klass = classify(master, prefix)
        for trans in inst.cell_inst.each_cplx_trans():
            placements.append(
                Placement(master, local.transformed(trans).to_dtype(dbu), klass))

    stats = ChipStats(top=top.name, die=top.dbbox(), core=None)
    core = db.DBox()
    for placement in placements:
        stats.counts[placement.klass] = stats.counts.get(placement.klass, 0) + 1
        area = placement.box.width() * placement.box.height()
        stats.areas[placement.klass] = stats.areas.get(placement.klass, 0.0) + area
        if placement.klass == "aion":
            stats.aion_counts[placement.master] = \
                stats.aion_counts.get(placement.master, 0) + 1
            stats.aion_areas[placement.master] = \
                stats.aion_areas.get(placement.master, 0.0) + area
        core += placement.box
    stats.core = core if not core.empty() else None
    return placements, stats


# =====================================================================
# The layout renderer
# =====================================================================
class Renderer:
    """klayout's own renderer, held open over one GDS.

    Loading is the expensive part and every picture here draws the same
    layout, so the view is built once and re-aimed with a target box.
    """

    def __init__(self, gds: Path, palette: Optional[Dict[Pair, RGB]] = None,
                 hollow: Tuple[Pair, ...] = (), background: str = "#000000"):
        try:
            import klayout.lay as lay
        except ImportError as exc:          # pragma: no cover - env problem
            raise RuntimeError(
                "klayout.lay is not available, so the layout cannot be "
                f"rendered ({exc}). `pip install klayout` provides it; it "
                "draws offscreen and needs no display.") from exc

        self._lay = lay
        self.view = lay.LayoutView()
        self.view.load_layout(str(gds), 0)
        self.view.max_hier()
        for key, value in (("background-color", background),
                           ("grid-visible", "false"),
                           ("grid-show-ruler", "false"),
                           ("text-visible", "false")):
            self.view.set_config(key, value)
        self.drawn = self._build_layers(palette or CHIP_LAYER_COLOURS,
                                        set(hollow))

    # -----------------------------------------------------------------
    def _build_layers(self, palette: Dict[Pair, RGB], hollow: set) -> List[Pair]:
        """Replace the auto-generated layer list with one in draw order.

        This has to build the list rather than restyle it, because klayout
        paints in list order and the list it makes from a GDS is in stream
        order. NWell is layer 31 and Metal1 is layer 8, so in stream order an
        opaque well is painted over the devices and the routing inside it --
        which reads as a chip with empty holes in its rows.

        Returns the layers that actually carry a shape, bottom-to-top: what
        the legend has to list, since naming a layer that was never drawn is
        worse than naming none.
        """
        layout = self.view.cellview(0).layout()
        top = self.view.cellview(0).cell

        present = []
        for index in layout.layer_indices():
            info = layout.get_info(index)
            key = (info.layer, info.datatype)
            if key in CHIP_HIDDEN_LAYERS or key[1] in CHIP_HIDDEN_DATATYPES:
                continue
            if top.begin_shapes_rec(index).at_end():
                continue
            present.append(key)

        rank = {key: i for i, key in enumerate(CHIP_DRAW_ORDER)}
        present.sort(key=lambda k: (rank.get(k, len(rank) + k[0]), k))

        self.view.clear_layers()
        for key in present:                 # bottom first: later paints over
            props = self._lay.LayerProperties()
            props.source = f"{key[0]}/{key[1]}@1"
            colour = palette.get(key) or deterministic_colour(*key)[:3]
            props.fill_color = (colour[0] << 16) | (colour[1] << 8) | colour[2]
            props.frame_color = props.fill_color
            # Pattern 0 is solid and 1 is hollow. Hollow needs a frame wide
            # enough to survive oversampling, or the layer renders as nothing
            # at all -- which is how a die view turns into a black rectangle.
            props.dither_pattern = 1 if key in hollow else 0
            props.width = 2 if key in hollow else 1
            props.transparent = False
            props.visible = True
            self.view.insert_layer(self.view.end_layers(), props)
        return present

    # -----------------------------------------------------------------
    def image(self, box: db.DBox, width: int, height: int,
              oversampling: int = 2) -> Image.Image:
        """Draw `box` at exactly `width` x `height`.

        `box` must already have the image's aspect ratio -- see `fit_box`.
        klayout centres a box whose aspect does not match, and then nothing
        drawn on top of the result lands where the caller computed it.
        """
        buffer = self.view.get_pixels_with_options(
            width, height, 0, max(1, min(3, oversampling)), 0, box)
        return Image.open(BytesIO(buffer.to_png_data())).convert("RGBA")


def fit_box(box: db.DBox, width: int, height: int) -> db.DBox:
    """`box` grown to the aspect ratio of a `width` x `height` image.

    Growing rather than cropping, so nothing the caller asked to see is cut
    off, and the mapping back to pixels stays a single scale factor.
    """
    if box.empty() or box.width() <= 0 or box.height() <= 0:
        raise ValueError("cannot render an empty box")
    want = width / height
    w, h = box.width(), box.height()
    if w / h < want:
        w = h * want
    else:
        h = w / want
    centre = box.center()
    return db.DBox(centre.x - w / 2, centre.y - h / 2,
                   centre.x + w / 2, centre.y + h / 2)


class Mapping:
    """um -> pixel, for whatever was drawn into a `fit_box` view."""

    def __init__(self, box: db.DBox, width: int, height: int,
                 origin: Tuple[int, int] = (0, 0)):
        self.box = box
        self.scale = width / box.width()
        self.origin = origin

    def point(self, x: float, y: float) -> Tuple[float, float]:
        return (self.origin[0] + (x - self.box.left) * self.scale,
                self.origin[1] + (self.box.top - y) * self.scale)

    def rect(self, box: db.DBox) -> Tuple[float, float, float, float]:
        left, top = self.point(box.left, box.top)
        right, bottom = self.point(box.right, box.bottom)
        return (left, top, right, bottom)


# =====================================================================
# The chrome: header, legend, and the panel they sit in
# =====================================================================
@dataclass
class LegendRow:
    colour: RGB
    label: str
    detail: str = ""


class Panel:
    """A titled, captioned, legended frame with a layout view inside it.

    Same geometry rule as gds_to_image: the caller asks for a finished image
    size and the annotations are carved out of it, so a request for 2400 px
    gets a 2400 px file however much the legend ends up costing.
    """

    def __init__(self, width: int, height: int, title: str, caption: str,
                 legend: List[LegendRow], margin: int = 20,
                 background: RGBA = BACKGROUND_COLOUR):
        self.width, self.height = width, height
        self.background = background
        self.canvas = Image.new("RGBA", (width, height), PANEL_COLOUR)
        self.draw = ImageDraw.Draw(self.canvas)

        self.title_px = max(16, height // 26)
        self.body_px = max(12, height // 44)
        self.title_font = _font(self.title_px, bold=True)
        self.body_font = _font(self.body_px)
        self.pad = max(9, self.body_px // 2)
        self.swatch = self.body_px + 3

        self._title, self._caption, self._legend = title, caption, legend
        self.header_h = self.pad * 2 + self.title_px + self.body_px + self.pad // 2
        self.legend_w = self._legend_width()

        self.view_w = max(1, width - self.legend_w - 2 * margin)
        self.view_h = max(1, height - self.header_h - 2 * margin)
        self.view_origin = (margin, self.header_h + margin)

    # -----------------------------------------------------------------
    @classmethod
    def fitted(cls, width: int, height: Optional[int], box: db.DBox,
               *args, **kwargs) -> "Panel":
        """A panel whose view has `box`'s aspect ratio, so nothing letterboxes.

        Chrome cannot be measured before it is built -- the legend is as wide
        as its longest label and the fonts scale with the image -- and it is
        what a fitted height has to leave room for. So the panel is built,
        measured, and rebuilt at the height its own chrome implies, until the
        answer stops moving. It converges in two.
        """
        if height is not None:
            return cls(width, height, *args, **kwargs)
        height = max(600, round(width * box.height() / box.width()))
        panel = cls(width, height, *args, **kwargs)
        for _ in range(3):
            wanted = panel.height_for(box)
            if wanted == panel.height:
                break
            panel = cls(width, wanted, *args, **kwargs)
        return panel

    def height_for(self, box: db.DBox) -> int:
        """The image height at which `box` exactly fills this panel's view."""
        chrome = self.height - self.view_h
        return max(600, round(self.view_w * box.height() / box.width()) + chrome)

    # -----------------------------------------------------------------
    def _legend_width(self) -> int:
        if not self._legend:
            return 0
        widest = max(_text_size(self.draw, f"{row.label}   {row.detail}",
                                self.body_font)[0] for row in self._legend)
        return self.pad * 3 + self.swatch + widest

    def paste(self, image: Image.Image) -> None:
        self.canvas.paste(image, self.view_origin, image)

    def overlay(self) -> ImageDraw.ImageDraw:
        """A draw handle over the whole canvas; view coordinates are absolute."""
        return ImageDraw.Draw(self.canvas, "RGBA")

    def finish(self, path: Path) -> Path:
        draw = ImageDraw.Draw(self.canvas)
        draw.text((self.pad, self.pad), self._title,
                  font=self.title_font, fill=TEXT_COLOUR)
        draw.text((self.pad, self.pad + self.title_px + self.pad // 2),
                  self._caption, font=self.body_font, fill=MUTED_COLOUR)
        draw.line([(0, self.header_h - 1), (self.width, self.header_h - 1)],
                  fill=GRID_COLOUR)

        if self.legend_w:
            x0 = self.width - self.legend_w
            draw.line([(x0, self.header_h), (x0, self.height)], fill=GRID_COLOUR)
            y = self.header_h + self.pad
            for row in self._legend:
                box = [x0 + self.pad, y,
                       x0 + self.pad + self.swatch, y + self.swatch]
                draw.rectangle(box, fill=_flatten((*row.colour, 255), self.background),
                               outline=(90, 90, 96))
                text_y = y + (self.swatch - self.body_px) // 2
                draw.text((x0 + self.pad * 2 + self.swatch, text_y), row.label,
                          font=self.body_font, fill=TEXT_COLOUR)
                if row.detail:
                    offset = _text_size(draw, row.label + "   ", self.body_font)[0]
                    draw.text((x0 + self.pad * 2 + self.swatch + offset, text_y),
                              row.detail, font=self.body_font, fill=MUTED_COLOUR)
                y += self.swatch + self.pad // 2

        path.parent.mkdir(parents=True, exist_ok=True)
        self.canvas.convert("RGB").save(path)
        return path


def _um(value: float) -> str:
    return f"{value:,.3f}".rstrip("0").rstrip(".")


def _share(part: float, whole: float) -> str:
    return "—" if whole <= 0 else f"{100.0 * part / whole:.2f}%"


# =====================================================================
# The three pictures
# =====================================================================
def layer_legend(renderer: Renderer) -> List[LegendRow]:
    """One row per layer that was actually drawn, topmost first, as read."""
    return [LegendRow(CHIP_LAYER_COLOURS.get(key) or deterministic_colour(*key)[:3],
                      CHIP_LAYER_NAMES.get(key, "layer"), f"{key[0]}/{key[1]}")
            for key in reversed(renderer.drawn)]


def render_die(renderer: Renderer, stats: ChipStats, placements: List[Placement],
               path: Path, width: int, height: Optional[int] = None,
               mark_aion: bool = True) -> Tuple[Path, int]:
    """The whole die, every layer, with the AION cells ringed.

    Returns the path and the height it settled on, so the placement map can
    be made the same size: the two are meant to be flipped between, and a
    die that changes scale between them defeats that.
    """
    panel = Panel.fitted(width, height, stats.die, stats.top,
                         die_caption(stats), layer_legend(renderer))

    box = fit_box(stats.die, panel.view_w, panel.view_h)
    panel.paste(renderer.image(box, panel.view_w, panel.view_h))
    mapping = Mapping(box, panel.view_w, panel.view_h, panel.view_origin)

    if mark_aion:
        colours = aion_palette(stats)
        draw = panel.overlay()
        for placement in placements:
            if placement.klass != "aion":
                continue
            # A 3.84 um cell on a 660 um die is fourteen pixels wide, and an
            # outline drawn on the footprint would cover most of it. The ring
            # goes outside, so the cell stays visible inside it.
            _ring(draw, mapping.rect(placement.box),
                  colours[placement.master], width=2)
    return panel.finish(path), panel.height


def render_placement(stats: ChipStats, placements: List[Placement],
                     path: Path, width: int, height: Optional[int] = None) -> Path:
    """Where the AION cells are: every site, coloured by what is standing on it."""
    colours = aion_palette(stats)
    legend = [LegendRow(colours[name], name,
                        f"{count} x   {_um(stats.aion_areas[name])} um²")
              for name, count in sorted(stats.aion_counts.items(),
                                        key=lambda kv: -kv[1])]
    legend += [
        LegendRow(LOGIC_COLOUR, "PDK logic",
                  f"{stats.counts.get('logic', 0)} x"),
        LegendRow(FILLER_COLOUR, "fill / decap / tie",
                  f"{stats.counts.get('filler', 0)} x"),
        LegendRow(CORE_COLOUR, "core", ""),
        LegendRow(DIE_COLOUR, "die", ""),
    ]

    panel = Panel.fitted(width, height, stats.die,
                         f"{stats.top} — AION cell placement",
                         aion_caption(stats), legend)
    box = fit_box(stats.die, panel.view_w, panel.view_h)
    view = Image.new("RGBA", (panel.view_w, panel.view_h), BACKGROUND_COLOUR)
    draw = ImageDraw.Draw(view)
    mapping = Mapping(box, panel.view_w, panel.view_h)

    if stats.core is not None:
        draw.rectangle(mapping.rect(stats.core), outline=(*CORE_COLOUR, 255))
    draw.rectangle(_inset(mapping.rect(stats.die)), outline=(*DIE_COLOUR, 255))

    # Filler first, then logic, then AION: the interesting class is drawn
    # last so nothing can land on top of it.
    for klass in ("filler", "logic"):
        colour = FILLER_COLOUR if klass == "filler" else LOGIC_COLOUR
        for placement in placements:
            if placement.klass == klass:
                draw.rectangle(mapping.rect(placement.box), fill=(*colour, 255))
    for placement in placements:
        if placement.klass != "aion":
            continue
        left, top, right, bottom = mapping.rect(placement.box)
        colour = (*colours[placement.master], 255)
        draw.rectangle([left, top, right, bottom], fill=colour)
        draw.rectangle([left - 2, top - 2, right + 2, bottom + 2],
                       outline=(*colours[placement.master], 120), width=2)

    panel.paste(view)
    return panel.finish(path)


def render_zoom(renderer: Renderer, stats: ChipStats, placements: List[Placement],
                cell: str, path: Path, width: int, height: int,
                sites: int = 32) -> Optional[Path]:
    """One instance of `cell`, abutted to its neighbours, at a readable zoom.

    The instance nearest the die centre, because an instance on the edge of
    the core has nothing on one side of it and the picture is meant to show
    a cell *among* PDK cells.
    """
    chosen = _nearest_to_centre(
        [p for p in placements if p.master == cell], stats.die.center())
    if chosen is None:
        return None

    # Never smaller than the cell plus a margin: `sites` is a comfortable
    # default, not a promise that the cell fits inside it. A multi-row or
    # very wide cell would otherwise be cropped by the window meant to show
    # it, and fit_box only corrects the aspect ratio, never the content.
    centre = chosen.box.center()
    half_w = max(sites * SITE_WIDTH_UM, chosen.box.width() * 1.6) / 2
    half_h = max(2 * ROW_HEIGHT_UM, chosen.box.height() * 1.6) / 2
    window = db.DBox(centre.x - half_w, centre.y - half_h,
                     centre.x + half_w, centre.y + half_h)

    count = stats.aion_counts.get(cell, 0)
    caption = (f"{count} instance(s) placed   "
               f"{_um(chosen.box.width())} x {_um(chosen.box.height())} um   "
               f"{chosen.box.width() / SITE_WIDTH_UM:.0f} sites   "
               f"at ({_um(chosen.box.left)}, {_um(chosen.box.bottom)}) um   "
               f"window {_um(window.width())} um wide, upper stack hollow")
    panel = Panel(width, height, f"{cell} in {stats.top}", caption,
                  layer_legend(renderer))

    box = fit_box(window, panel.view_w, panel.view_h)
    panel.paste(renderer.image(box, panel.view_w, panel.view_h))
    mapping = Mapping(box, panel.view_w, panel.view_h, panel.view_origin)

    # White, not the cell's map colour: a zoom rings exactly one cell, so the
    # ring has nothing to identify and everything to stay visible against --
    # and every colour in the palette is also some layer's colour.
    _ring(panel.overlay(), mapping.rect(chosen.box), (255, 255, 255),
          width=3, grow=2.0)
    return panel.finish(path)


def _inset(rect: Tuple[float, float, float, float], by: float = 0.5):
    left, top, right, bottom = rect
    return [left + by, top + by, right - by, bottom - by]


def _ring(draw: ImageDraw.ImageDraw, rect: Tuple[float, float, float, float],
          colour: RGB, width: int = 2, grow: float = 1.5) -> None:
    """A highlight that survives whatever it is drawn on.

    Two rings: a dark one outside a bright one. A single ring in the cell's
    own colour disappears the moment it crosses a layer of the same hue --
    the highlight for a cell coloured like Metal2 has to stay readable when
    it lands on Metal2.
    """
    left, top, right, bottom = rect
    outer = [left - grow - width, top - grow - width,
             right + grow + width, bottom + grow + width]
    draw.rectangle(outer, outline=(0, 0, 0, 210), width=width)
    draw.rectangle([left - grow, top - grow, right + grow, bottom + grow],
                   outline=(*colour, 255), width=width)


def _nearest_to_centre(candidates: List[Placement],
                       centre: db.DPoint) -> Optional[Placement]:
    if not candidates:
        return None
    return min(candidates, key=lambda p: (p.box.center().x - centre.x) ** 2
               + (p.box.center().y - centre.y) ** 2)


def aion_palette(stats: ChipStats) -> Dict[str, RGB]:
    """A stable colour per AION cell, assigned by name so re-runs match."""
    return {name: AION_COLOURS[i % len(AION_COLOURS)]
            for i, name in enumerate(sorted(stats.aion_counts))}


def die_caption(stats: ChipStats) -> str:
    die_area = stats.die.width() * stats.die.height()
    return (f"{_um(stats.die.width())} x {_um(stats.die.height())} um die  =  "
            f"{die_area:,.0f} um²   "
            f"{stats.instances:,} placed instance(s)   "
            f"{stats.aion} AION ({_share(stats.aion, stats.instances)})")


def aion_caption(stats: ChipStats) -> str:
    core_area = (stats.core.width() * stats.core.height()) if stats.core else 0.0
    aion_area = stats.areas.get("aion", 0.0)
    logic_area = stats.areas.get("logic", 0.0)
    return (f"{stats.aion} AION instance(s) of "
            f"{len(stats.aion_counts)} cell(s)   "
            f"{_um(aion_area)} um²  =  {_share(aion_area, core_area)} of the "
            f"core, {_share(aion_area, aion_area + logic_area)} of the logic")


# =====================================================================
def render_chip(gds: Path, outdir: Path, prefix: str = "AION_",
                width: int = 2400, height: Optional[int] = None,
                zoom_sites: int = 32, top_name: Optional[str] = None) -> dict:
    """Render every picture for one hardened GDS. Returns the stats."""
    gds, outdir = Path(gds), Path(outdir)
    layout = db.Layout()
    layout.read(str(gds))
    top = layout.cell(top_name) if top_name else layout.top_cell()
    if top is None:
        raise RuntimeError(f"{gds}: no top cell"
                           + (f" named {top_name!r}" if top_name else ""))
    if top.bbox().empty():
        raise RuntimeError(f"{gds}: top cell {top.name!r} draws nothing")

    placements, stats = scan(layout, top, prefix)
    outdir.mkdir(parents=True, exist_ok=True)

    renderer = Renderer(gds)
    chip_png, die_h = render_die(renderer, stats, placements,
                                 outdir / f"{stats.top}.chip.png",
                                 width, height)
    written: List[Path] = [
        chip_png,
        render_placement(stats, placements, outdir / f"{stats.top}.aion.png",
                         width, die_h),
    ]
    if stats.aion_counts:
        # A second view of the same GDS, because a zoom wants the power grid
        # outlined and the die view wants it solid.
        close_up = Renderer(gds, hollow=ZOOM_HOLLOW)
        zoom_h = max(700, round(width * 0.5))
        for cell in sorted(stats.aion_counts):
            path = render_zoom(close_up, stats, placements, cell,
                               outdir / f"{stats.top}.{cell}.png",
                               width, zoom_h, zoom_sites)
            if path is not None:
                written.append(path)

    record = stats.as_dict()
    record["gds"] = str(gds)
    record["images"] = [p.name for p in written]
    (outdir / "render.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


# =====================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a hardened chip GDS and show where the AION "
                    "cells landed.")
    parser.add_argument("gds", help="the hardened GDS, e.g. flow/7_pnr/gds/x.gds")
    parser.add_argument("outdir", help="directory to write the PNGs into")
    parser.add_argument("--prefix", default="AION_",
                        help="module prefix that marks an AI cell (default: AION_)")
    parser.add_argument("--width", type=int, default=2400,
                        help="image width in pixels (default: 2400)")
    parser.add_argument("--height", type=int,
                        help="image height (default: fit the die's aspect)")
    parser.add_argument("--zoom-sites", type=int, default=32, metavar="N",
                        help="width of the per-cell zoom window, in placement "
                             "sites (default: 32)")
    parser.add_argument("--top", help="top cell name (default: the layout's own)")

    args = parser.parse_args()
    try:
        record = render_chip(Path(args.gds), Path(args.outdir),
                             prefix=args.prefix, width=args.width,
                             height=args.height, zoom_sites=args.zoom_sites,
                             top_name=args.top)
    except Exception as exc:                # noqa: BLE001 - a CLI, not a library
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    for name in record["images"]:
        print(f"Saved {Path(args.outdir) / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
