#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Turn a PNG logo into a placeable macro:
#                             GDS art on a real PDK layer, a prBoundary,
#                             a LEF, and a preview.
#
#  The layer numbers are the IHP SG13G2 ones -- TopMetal1 is 126/0, not
#  "the top metal", and prBoundary is 189/4, the same pair the flow's cell
#  generator draws (aion_flow/.../aion_layout/tech.py) and the same pair
#  scripts/gds_to_image.py knows how to colour.  A GDS whose art lands on
#  a layer the PDK does not define streams out fine and disappears at mask
#  generation, so the table below is the whole point of the script.
#
#  Vectorising a bitmap is where the DRC risk lives, so the raster grid is
#  the design rule:
#
#    * every polygon edge falls on a multiple of --pixel-size, so the
#      narrowest drawable feature and the narrowest gap are both exactly
#      one pixel.  Default the pixel to the larger of the layer's two
#      minima and the art is min-width and min-spacing clean by
#      construction, whatever the picture looks like;
#    * the one thing that grid cannot rule out is a diagonal pixel pair
#      touching at a single point -- zero spacing, and a DRC error on
#      every deck.  Those are found and filled in the raster, before any
#      polygon exists;
#    * and the result is measured rather than assumed: every run of ink
#      and every gap is counted, so the narrowest of each is reported in
#      microns whether or not it breaks a rule.
#
#  Shrinking the art past the layer's minimum width is allowed -- it is
#  sometimes what you want on TopMetal2, where 2 um pixels make a small
#  logo unreadable -- but it is reported as a violation rather than
#  quietly repaired, because the honest fix is a bigger logo or a coarser
#  raster, and both of those are the caller's call.
#
#  The measurement is not signoff.  The PDK's own deck is, and it agrees:
#
#    scripts/docker_run.sh python3 \
#      $PDK_ROOT/$PDK/libs.tech/klayout/tech/drc/run_drc.py \
#      --path=implementation/macros/AION_logo.gds --table=topmetal1
#
#  The macro reproduces the picture as given -- its canvas, its blank
#  border and its aspect ratio -- because that border is usually part of
#  the design and is definitely part of it under --invert, where the
#  background is what gets drawn and the letters are cut out of it.
#  --crop asks for the strokes alone instead.
#
#  Nothing here needs the container: gdstk, numpy and Pillow all run on
#  the host, and the PNG preview borrows scripts/gds_to_image.py the way
#  scripts/render_chip.py does.
# ================================================================

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import gdstk
import numpy as np
from PIL import Image

Pair = Tuple[int, int]
Run = Tuple[int, int]


# ---------------------------------------------------------------------
# Technology
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ArtLayer:
    """A layer the logo may be drawn on, with the two rules that constrain it.

    ``min_spacing_um`` is the spacing a *logo* has to clear, which on the
    thin metals is not the headline number.  Their space rules are tiered:
    Metal1 wants 0.18 between ordinary lines but 0.22 (M1.e) as soon as one of
    them is wider than 0.30 um and they run alongside each other for more than
    1 um, and Metal2-5 want 0.21 rising to 0.24 (Mn.e) past 0.39 um.  A logo's
    strokes are always wider than those thresholds, so the tier is what applies
    and the flat rule would be the wrong number to build a raster on.

    Two tiers are deliberately not modelled, because whether they apply depends
    on how long two particular edges run alongside each other and no property of
    the raster can decide that: the 0.60 um one the metals reach past 10 um of
    width (M1.f, Mn.f), and TM2.bR, which asks 5 um of TopMetal2 spacing between
    lines wider than 5 um that run together for more than 50 um.  Those are the
    deck's business, and the deck is the signoff -- see :func:`measure_runs`.
    """

    name: str
    gds_layer: int
    gds_datatype: int
    min_width_um: float
    min_spacing_um: float


#: IHP SG13G2.  The layer/datatype pairs come from
#: libs.tech/klayout/tech/sg13g2.lyp; the widths and spacings are the deck's own
#: rule values, sg13g2_tech_default.json, not the flat LEF numbers -- see
#: ArtLayer.  TopMetal1 is the default because it is thick, coarse and above
#: every layer the router uses, so a logo drawn there collides with nothing the
#: flow places.
ART_LAYERS: Dict[str, ArtLayer] = {
    #                                   L    D   width  space
    "Metal1": ArtLayer("Metal1", 8, 0, 0.16, 0.22),      # M1.a, M1.e
    "Metal2": ArtLayer("Metal2", 10, 0, 0.20, 0.24),     # Mn.a, Mn.e
    "Metal3": ArtLayer("Metal3", 30, 0, 0.20, 0.24),
    "Metal4": ArtLayer("Metal4", 50, 0, 0.20, 0.24),
    "Metal5": ArtLayer("Metal5", 67, 0, 0.20, 0.24),
    "TopMetal1": ArtLayer("TopMetal1", 126, 0, 1.64, 1.64),   # TM1.a, TM1.b
    "TopMetal2": ArtLayer("TopMetal2", 134, 0, 2.00, 2.00),   # TM2.a, TM2.b
    # A passivation opening, not metal: drawing it exposes whatever is under it.
    # Pas.c also wants 2.1 um of TopMetal2 around every opening, which this
    # script does not draw -- a Passiv logo needs the same art on TopMetal2,
    # grown, underneath it.
    "Passiv": ArtLayer("Passiv", 9, 0, 2.10, 3.50),           # Pas.a, Pas.b
}

#: prBoundary.boundary. The placement footprint, and the only thing in the
#: cell that tells a place-and-route tool how much room the macro takes.
PRBOUNDARY: Pair = (189, 4)

#: Recog.drawing -- the PDK's own "this shape is not a circuit" marker.  The
#: slit rules derive their input as ``TopMetal1.ext_not(Recog_or_MIM_or_dfpad)``,
#: so a Recog rectangle over the macro is the sanctioned way to exempt decorative
#: metal from them.  It exempts nothing else, density included.
RECOG: Pair = (99, 0)

#: Slt.c: metal wider than 30 um has to carry slits.  The deck finds the regions
#: that do by eroding 3 um then 12 um and growing 15 um back, so anything that
#: survives a 15 um erosion is what the rule is about.
SLIT_EROSION_UM = 15.0

#: TM1.c and TM1.d, and their equivalents on the other metals: global density
#: must land between these.  "Global" means over the whole chip, so a macro's own
#: density is an indication, never the verdict -- see :func:`convert`.
MIN_DENSITY, MAX_DENSITY = 0.25, 0.70

#: GDSII units. 1 um user unit on a 1 nm database grid, as everything else in
#: the PDK; every coordinate this script emits is quantised to that grid.
GDS_UNIT = 1e-6
GDS_PRECISION = 1e-9
DB_GRID_UM = GDS_PRECISION / GDS_UNIT

#: Boolean resolution handed to gdstk, in user units. One database grid step:
#: finer would invent coordinates the GDS cannot store.
CLIP_PRECISION = DB_GRID_UM

#: Filling a point contact can create a new one next to it. Eight rounds is far
#: more than any real logo needs; the cap only exists so a pathological image
#: fails loudly instead of looping.
MAX_FILL_ROUNDS = 8


class LogoError(RuntimeError):
    """Raised when the picture, or the geometry it produces, cannot be used."""


# ---------------------------------------------------------------------
# PNG -> ink mask
# ---------------------------------------------------------------------


def load_ink(
    path: Path,
    threshold: int = 128,
    invert: bool = False,
    source: str = "auto",
) -> np.ndarray:
    """Read ``path`` and return a 2-D boolean array, ``True`` where the ink is.

    Two kinds of logo turn up, and guessing wrong between them yields a solid
    rectangle rather than an obvious failure:

    * a flattened one, opaque, dark strokes on white -- the ink is the *dark*
      half of the luminance;
    * a cut-out one with an alpha channel, whose colour outside the strokes is
      undefined and frequently black -- the ink is the *opaque* half of alpha,
      and luminance says nothing at all.

    ``source='auto'`` picks alpha when the image has an alpha channel that is
    not uniformly opaque, and luminance otherwise.  Row 0 of the result is the
    top of the picture, as in the file.
    """
    with Image.open(path) as image:
        image.load()
        has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
        alpha = (
            np.asarray(image.convert("RGBA"))[..., 3] if has_alpha else None
        )
        luma = np.asarray(image.convert("L"))

    if source == "auto":
        source = "alpha" if alpha is not None and alpha.min() < 255 else "luma"
    if source == "alpha":
        if alpha is None:
            raise LogoError(f"{path}: no alpha channel to read the ink from")
        mask = alpha >= threshold
    else:
        mask = luma < threshold

    if invert:
        mask = ~mask
    if not mask.any():
        raise LogoError(
            f"{path}: no ink at threshold {threshold} reading '{source}' -- "
            "try --invert, another --threshold, or --ink to force the channel"
        )
    if mask.all():
        raise LogoError(
            f"{path}: every pixel is ink at threshold {threshold} reading "
            f"'{source}' -- the result would be a solid rectangle"
        )
    return mask


def crop_to_ink(mask: np.ndarray) -> np.ndarray:
    """Trim the surrounding blank margin, so --width sizes the strokes.

    Off by default, because cropping is a framing decision rather than a
    cleanup: a logo drawn with deliberate space around it has a different
    aspect ratio from its own strokes -- 2640 x 840 against 1041 x 364 for
    AION_Logo_CorrectSize -- and cropping trades the picture's proportions for
    the strokes'.  Keeping the canvas reproduces the picture; ``--crop`` asks
    for the strokes alone, at the price of the border and the aspect ratio.
    """
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    return mask[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]


def resample(mask: np.ndarray, cols: int, rows: int, coverage: float) -> np.ndarray:
    """Rescale the ink mask to ``cols`` x ``rows`` pixels.

    Downsampling averages over each destination pixel and keeps it when at
    least ``coverage`` of its area was ink, which thins strokes evenly instead
    of dropping every second one the way point sampling does.  Upsampling uses
    nearest-neighbour: there is no detail to recover, and a smooth filter would
    only fringe the edges with pixels the original never had.
    """
    if (cols, rows) == (mask.shape[1], mask.shape[0]):
        return mask
    resized = Image.fromarray(mask.astype(np.uint8) * 255).resize(
        (cols, rows),
        Image.Resampling.BOX if cols <= mask.shape[1] else Image.Resampling.NEAREST,
    )
    out = np.asarray(resized) >= coverage * 255
    if not out.any():
        raise LogoError(
            f"nothing survived the resample to {cols} x {rows} pixels -- "
            "the raster is too coarse for this picture: raise --width or "
            "lower --pixel-size"
        )
    return out


def fill_point_contacts(mask: np.ndarray) -> Tuple[np.ndarray, int]:
    """Fill every diagonal pixel pair, and report how many were filled.

    Two ink pixels meeting corner-to-corner have zero spacing at that point.
    The union merges them into one polygon that pinches to nothing, which is a
    minimum-spacing error in every deck and a shape most tools cannot even
    represent honestly.  Filling the two blank pixels of the pair opens the
    pinch into a square corner; at one pixel it is invisible in the art, and it
    is the only repair that cannot make the geometry worse.

    Filling can create a fresh diagonal pair against a neighbour, so this
    iterates to a fixed point.
    """
    out = mask.copy()
    filled = 0
    for _ in range(MAX_FILL_ROUNDS):
        top_left, top_right = out[:-1, :-1], out[:-1, 1:]
        bot_left, bot_right = out[1:, :-1], out[1:, 1:]
        falling = top_left & bot_right & ~top_right & ~bot_left
        rising = top_right & bot_left & ~top_left & ~bot_right
        count = int(falling.sum() + rising.sum())
        if count == 0:
            return out, filled
        # Basic slices are views, so these write through into `out`.
        out[:-1, 1:][falling] = True
        out[1:, :-1][falling] = True
        out[:-1, :-1][rising] = True
        out[1:, 1:][rising] = True
        filled += count
    raise LogoError(
        f"still finding diagonal pixel contacts after {MAX_FILL_ROUNDS} rounds "
        "of filling -- the raster is too fine for this picture"
    )


# ---------------------------------------------------------------------
# Ink mask -> polygons
# ---------------------------------------------------------------------


def _row_runs(row: np.ndarray) -> List[Run]:
    """The ``[start, end)`` column spans of ink in one raster row."""
    edges = np.diff(np.concatenate(([0], row.view(np.uint8), [0])).astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1).tolist(),
                    np.flatnonzero(edges == -1).tolist()))


def mask_to_rectangles(
    mask: np.ndarray,
    pixel_um: float,
    origin: Tuple[float, float],
    layer: ArtLayer,
) -> List[gdstk.Polygon]:
    """Cover the ink with rectangles, in GDS coordinates.

    Each raster row contributes its runs of ink, and a run repeated unchanged
    on the row below is grown downwards instead of emitted again -- so a solid
    logo costs a handful of tall rectangles rather than one per row, and the
    union that follows has correspondingly less to do.

    Raster row 0 is the top of the picture and GDS y grows upwards, so the row
    index is mirrored on the way out.
    """
    rows, _ = mask.shape
    origin_x, origin_y = origin
    rectangles: List[gdstk.Polygon] = []
    open_runs: Dict[Run, int] = {}

    def close(run: Run, first_row: int, last_row: int) -> None:
        start, end = run
        x0 = origin_x + start * pixel_um
        x1 = origin_x + end * pixel_um
        y0 = origin_y + (rows - last_row) * pixel_um
        y1 = origin_y + (rows - first_row) * pixel_um
        rectangles.append(gdstk.rectangle(
            (x0, y0), (x1, y1),
            layer=layer.gds_layer, datatype=layer.gds_datatype,
        ))

    for index in range(rows + 1):
        current = set(_row_runs(mask[index])) if index < rows else set()
        for run, first_row in list(open_runs.items()):
            if run not in current:
                close(run, first_row, index)
                del open_runs[run]
        for run in current:
            open_runs.setdefault(run, index)
    return rectangles


def union(polygons: Sequence[gdstk.Polygon], layer: ArtLayer) -> List[gdstk.Polygon]:
    """Merge abutting rectangles into as few polygons as the art allows."""
    return gdstk.boolean(
        list(polygons), [], "or",
        precision=CLIP_PRECISION,
        layer=layer.gds_layer, datatype=layer.gds_datatype,
    )


# ---------------------------------------------------------------------
# Rule checks
# ---------------------------------------------------------------------


def measure_runs(mask: np.ndarray) -> Tuple[int, Optional[int]]:
    """Narrowest ink run and narrowest interior gap, in pixels, over both axes.

    Every polygon edge lies on the raster grid, so the narrowest axis-aligned
    feature *is* the shortest run of ink along a row or a column, and the
    narrowest notch or gap is the shortest run of blank pixels with ink on both
    sides of it.  Counting runs measures that exactly, in one pass, with no
    tolerance to choose.

    Axis-aligned is the right measurement, not a convenient one.  The IHP deck
    checks TM1.a and TM1.b between *facing* edges, so the corners of a 45
    degree staircase -- whose edges are perpendicular and face nothing -- are
    not width or space violations to it, and a check that reported them would
    condemn every diagonal stroke in the picture.  Verified the way it should
    be: the deck's own topmetal1 table, run on this script's output, finds
    nothing.  Zero-distance corner contacts are the one thing this measurement
    cannot see, and :func:`fill_point_contacts` has already removed them.

    Signoff is still the deck itself, in the container:

        scripts/docker_run.sh python3 \
            $PDK_ROOT/$PDK/libs.tech/klayout/tech/drc/run_drc.py \
            --path=implementation/macros/AION_logo.gds --table=topmetal1
    """
    narrowest_ink = mask.size
    narrowest_gap: Optional[int] = None
    for lines in (mask, mask.T):
        for line in lines:
            bounds = np.concatenate((
                [0], np.flatnonzero(np.diff(line.view(np.uint8))) + 1, [line.size]))
            lengths = np.diff(bounds)
            values = line[bounds[:-1]]
            if values.any():
                narrowest_ink = min(narrowest_ink, int(lengths[values].min()))
            interior = ~values
            interior[0] = interior[-1] = False       # the blank outside is not a gap
            if interior.any():
                shortest = int(lengths[interior].min())
                narrowest_gap = min(narrowest_gap or shortest, shortest)
    return narrowest_ink, narrowest_gap


def _total_area(polygons: Sequence[gdstk.Polygon]) -> float:
    """Total area of a polygon list, in square microns."""
    return sum(polygon.area() for polygon in polygons)


def needs_slits(polygons: Sequence[gdstk.Polygon]) -> bool:
    """Whether any of the art is wide enough for the slit rule to apply.

    Inverting a logo turns it from strokes into a plate, and a plate of top
    metal is exactly what Slt.c is about: metal wider than 30 um has to be cut
    with slits, because a large unbroken sheet delaminates.  A 15 um erosion is
    the deck's own test for it, so this is the deck's question asked early
    rather than a guess at what it might mean.

    The fix is not to slit a logo -- stripes through the letterforms are not a
    logo any more -- but to mark it with :data:`RECOG`, which is what the rule's
    own input derivation exempts.
    """
    return bool(gdstk.offset(
        list(polygons), -SLIT_EROSION_UM,
        join="miter", precision=CLIP_PRECISION, use_union=True,
    ))


# ---------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------


def build_cell(
    name: str,
    art: Sequence[gdstk.Polygon],
    size: Tuple[float, float],
    recog: bool = False,
) -> gdstk.Library:
    """Assemble the library: the art, plus the prBoundary that sizes the macro.

    The boundary is drawn from the origin so the macro's own coordinate system
    starts at (0, 0) -- LEF ``ORIGIN 0 0`` and the GDS then agree, and a
    placement at (x, y) puts the boundary's lower-left corner there.
    """
    library = gdstk.Library(name=name, unit=GDS_UNIT, precision=GDS_PRECISION)
    cell = library.new_cell(name)
    cell.add(gdstk.rectangle(
        (0.0, 0.0), size,
        layer=PRBOUNDARY[0], datatype=PRBOUNDARY[1],
    ))
    if recog:
        cell.add(gdstk.rectangle(
            (0.0, 0.0), size,
            layer=RECOG[0], datatype=RECOG[1],
        ))
    cell.add(*art)
    return library


def write_lef(
    path: Path,
    name: str,
    size: Tuple[float, float],
    layer: ArtLayer,
    lef_class: str,
    provenance: Sequence[str],
    obstruction: bool = True,
) -> None:
    """Write the macro's LEF: how big it is, and what it blocks.

    A logo has no pins, so the LEF says two things.  ``SIZE`` is the
    prBoundary, and must be, or the placer reserves an area that does not match
    the GDS.  ``OBS`` covers that whole area on the art layer -- not just the
    strokes' bounding box, because the point of the obstruction is that nothing
    is routed across the tile, and a router that threads a wire between the A
    and the I has satisfied a bounding box and ruined the logo.

    ``CLASS BLOCK`` reserves the footprint: no standard cell is placed under
    it.  ``CLASS COVER`` is the alternative for a logo sitting on a top metal
    with a populated core underneath, which most tools do not treat as a
    placement blockage -- correct here, but only if the layers really do not
    collide, so it is not the default.
    """
    width, height = size
    with open(path, "w") as lef:
        for line in provenance:
            lef.write(f"# {line}\n")
        lef.write("VERSION 5.8 ;\n")
        lef.write('DIVIDERCHAR "/" ;\n')
        lef.write('BUSBITCHARS "[]" ;\n')
        lef.write("UNITS\n")
        lef.write("  DATABASE MICRONS 1000 ;\n")
        lef.write("END UNITS\n\n")
        lef.write(f"MACRO {name}\n")
        lef.write(f"  CLASS {lef_class} ;\n")
        lef.write(f"  FOREIGN {name} 0 0 ;\n")
        lef.write("  ORIGIN 0.000 0.000 ;\n")
        lef.write(f"  SIZE {width:.3f} BY {height:.3f} ;\n")
        lef.write("  SYMMETRY X Y ;\n")
        if obstruction:
            lef.write("  OBS\n")
            lef.write(f"    LAYER {layer.name} ;\n")
            lef.write(f"      RECT 0.000 0.000 {width:.3f} {height:.3f} ;\n")
            lef.write("  END\n")
        lef.write(f"END {name}\n\n")
        lef.write("END LIBRARY\n")


def write_png(gds: Path, png: Path, title: str) -> None:
    """Render a preview with the repo's own GDS renderer, if it is importable.

    scripts/render_chip.py already borrows gds_to_image this way; doing the
    same here means the logo is drawn in the same colours as every cell
    picture the flow makes.  A preview is a convenience, so an import failure
    is reported and survived rather than raised.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gds_to_image import render_gds  # noqa: E402  (optional, path-dependent)

    render_gds(str(gds), str(png), width=1600, height=700, title=title)


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


def show(path: Path) -> str:
    """A path relative to the working directory when it is under it.

    The Makefile passes absolute paths; recording those in the LEF's provenance
    comments would tie a tracked file to one machine's home directory.
    """
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def quantise(value: float) -> float:
    """Snap a length to the GDS database grid, so no vertex is ever rounded."""
    return round(value / DB_GRID_UM) * DB_GRID_UM


def convert(args: argparse.Namespace) -> int:
    """Run the whole conversion and report every measurement it made."""
    layer = ART_LAYERS[args.layer]
    png = Path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # ---- the picture ------------------------------------------------
    mask = load_ink(png, threshold=args.threshold, invert=args.invert,
                    source=args.ink)
    source_shape = mask.shape
    # Inverted, the ink *is* the border, so its bounding box is the canvas and
    # cropping would be a no-op at best. Refusing it outright beats letting the
    # framing depend on whether the strokes happen to reach the picture's edge.
    crop = args.crop and not args.invert
    if crop:
        mask = crop_to_ink(mask)
    src_rows, src_cols = mask.shape
    framing = "cropped to the ink" if crop else "the picture's own canvas"
    print(f"{show(png)}: {source_shape[1]} x {source_shape[0]} px"
          f"{' inverted' if args.invert else ''}, "
          f"rasterising {src_cols} x {src_rows} px -- {framing}")

    # ---- the raster -------------------------------------------------
    # One pixel is the narrowest thing the raster can draw and the narrowest
    # gap it can leave, so the default has to clear *both* rules -- on Metal1
    # the spacing (0.18) is the larger of the two, and a pixel sized to the
    # width alone leaves every one-pixel gap illegal.
    default_pixel = max(layer.min_width_um, layer.min_spacing_um)
    pixel_um = quantise(args.pixel_size if args.pixel_size else default_pixel)
    if pixel_um <= 0:
        raise LogoError("--pixel-size must be positive")
    if args.width and args.height:
        cols = max(1, round(args.width / pixel_um))
        rows = max(1, round(args.height / pixel_um))
    elif args.height:
        rows = max(1, round(args.height / pixel_um))
        cols = max(1, round(rows * src_cols / src_rows))
    else:
        cols = max(1, round(args.width / pixel_um))
        rows = max(1, round(cols * src_rows / src_cols))
    if cols > src_cols or rows > src_rows:
        print(f"warning: upsampling {src_cols} x {src_rows} px to "
              f"{cols} x {rows} -- the extra pixels carry no extra detail")

    mask = resample(mask, cols, rows, args.coverage)
    mask, filled = fill_point_contacts(mask)
    art_w, art_h = quantise(cols * pixel_um), quantise(rows * pixel_um)
    print(f"raster: {cols} x {rows} px at {pixel_um:.3f} um/px "
          f"-> {art_w:.3f} x {art_h:.3f} um of art")
    if filled:
        print(f"filled {filled} diagonal pixel contact(s): corner-to-corner ink "
              "is zero spacing, which no DRC deck allows")

    # ---- the geometry -----------------------------------------------
    margin = quantise(args.margin if args.margin is not None else layer.min_spacing_um)
    if margin < 0:
        raise LogoError("--margin cannot be negative")
    rectangles = mask_to_rectangles(mask, pixel_um, (margin, margin), layer)
    art = union(rectangles, layer)
    if not art:
        raise LogoError("the union produced no polygons -- nothing to draw")
    size = (quantise(art_w + 2 * margin), quantise(art_h + 2 * margin))
    print(f"geometry: {len(rectangles)} rectangle(s) merged into {len(art)} "
          f"polygon(s) on {layer.name} ({layer.gds_layer}/{layer.gds_datatype}), "
          f"{_total_area(art):.1f} um^2 of metal")
    print(f"macro: {size[0]:.3f} x {size[1]:.3f} um including a {margin:.3f} um "
          f"margin, prBoundary on {PRBOUNDARY[0]}/{PRBOUNDARY[1]}")

    # ---- what the rules say about it --------------------------------
    violations = 0
    if args.check:
        ink_px, gap_px = measure_runs(mask)
        for pixels, what, limit in (
            (ink_px, "feature", layer.min_width_um),
            (gap_px, "gap", layer.min_spacing_um),
        ):
            if pixels is None:                       # solid art: no gap to measure
                print(f"checked: no {layer.name} gap at all, so nothing to "
                      f"compare against {limit:.3f} um")
                continue
            measured = pixels * pixel_um
            broken = measured < limit - DB_GRID_UM
            violations += int(broken)
            print(f"checked: narrowest {layer.name} {what} {measured:.3f} um "
                  f"({pixels} px), {'violates' if broken else 'rule'} "
                  f"{limit:.3f} um")
        if violations and pixel_um < max(layer.min_width_um, layer.min_spacing_um):
            print(f"warning: --pixel-size {pixel_um:.3f} um is finer than "
                  f"{layer.name} allows; raise it, or scale the logo up, to "
                  "draw this cleanly")

        # Slt.c. Inverting a logo is what usually trips this: strokes are never
        # 30 um wide, a plate with strokes cut out of it always is.
        if needs_slits(art):
            if args.recog:
                print(f"checked: {layer.name} wider than "
                      f"{SLIT_EROSION_UM * 2:.0f} um is present, and the "
                      f"Recog marker ({RECOG[0]}/{RECOG[1]}) exempts it from "
                      "the slit rules (Slt.c)")
            else:
                violations += 1
                print(f"warning: {layer.name} wider than "
                      f"{SLIT_EROSION_UM * 2:.0f} um needs slits (Slt.c), "
                      "which would cut stripes through the art -- pass "
                      "--recog to mark it as decoration instead")

        # TM1.c / TM1.d and their siblings divide by the *chip* area, which this
        # macro is not, so the number below is the macro's own and the rule's
        # verdict depends on the die it lands in. Reported because an inverted
        # logo is nearly solid metal, and that is worth seeing before tapeout.
        density = _total_area(art) / (size[0] * size[1])
        note = ""
        if density > MAX_DENSITY:
            note = (f" -- above the {MAX_DENSITY:.0%} maximum on its own, but "
                    "the rule is global: divide by the die area, not this")
        elif density < MIN_DENSITY:
            note = (f" -- below the {MIN_DENSITY:.0%} minimum on its own; the "
                    "rule is global, so the rest of the die decides")
        print(f"checked: {density:.1%} of the macro is {layer.name}{note}")

    # ---- the views --------------------------------------------------
    provenance = [
        f"{Path(__file__).name}: {args.cell} generated from {show(png)}",
        f"{Path(__file__).name}: art on {layer.name} "
        f"({layer.gds_layer}/{layer.gds_datatype}), prBoundary on "
        f"{PRBOUNDARY[0]}/{PRBOUNDARY[1]}, {pixel_um:.3f} um raster",
        f"{Path(__file__).name}: SIZE is the prBoundary; the OBS covers it "
        "entirely, so nothing is routed across the art",
    ]
    if args.recog:
        provenance.append(
            f"{Path(__file__).name}: the GDS also carries Recog "
            f"({RECOG[0]}/{RECOG[1]}) over the macro, which exempts the art "
            "from the slit rules (Slt.c)")
    library = build_cell(args.cell, art, size, recog=args.recog)
    gds = outdir / f"{args.cell}.gds"
    lef = outdir / f"{args.cell}.lef"
    library.write_gds(gds)
    write_lef(lef, args.cell, size, layer, args.lef_class, provenance,
              obstruction=not args.no_obs)
    written = [gds, lef]

    if not args.no_svg:
        svg = outdir / f"{args.cell}.svg"
        library.cells[0].write_svg(svg)
        written.append(svg)
    if not args.no_png:
        preview = outdir / f"{args.cell}.png"
        try:
            write_png(gds, preview, args.cell)
            written.append(preview)
        except Exception as exc:                     # preview only, never fatal
            print(f"warning: no PNG preview ({exc})")

    for path in written:
        print(f"wrote {show(path)}")

    if violations and args.strict:
        print("error: --strict, and the geometry violates the layer's rules",
              file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a PNG logo into a GDS + LEF macro on an IHP "
                    "SG13G2 layer, with a prBoundary.",
        epilog="The default raster pixel clears both of the art layer's "
               "minima, which makes the output min-width and min-spacing "
               "clean by construction.",
    )
    parser.add_argument("input", nargs="?", default="logo/AION_Logo_CorrectSize.png",
                        help="source PNG (default: logo/AION_Logo_CorrectSize.png)")
    parser.add_argument("-o", "--outdir", default="implementation/macros",
                        help="where to write the views (default: implementation/macros)")
    parser.add_argument("--cell", default="AION_logo",
                        help="cell and macro name (default: AION_logo)")
    parser.add_argument("--layer", default="TopMetal1", choices=sorted(ART_LAYERS),
                        help="layer to draw the art on (default: TopMetal1)")

    size = parser.add_argument_group("size")
    size.add_argument("--width", type=float, default=400.0, metavar="UM",
                      help="art width in microns (default: 400)")
    size.add_argument("--height", type=float, metavar="UM",
                      help="art height in microns (default: keep the aspect ratio)")
    size.add_argument("--pixel-size", type=float, metavar="UM",
                      help="raster pixel in microns (default: the layer's "
                           "min width or min spacing, whichever is larger)")
    size.add_argument("--margin", type=float, metavar="UM",
                      help="blank border between the art and the prBoundary "
                           "(default: the layer's min spacing)")

    ink = parser.add_argument_group("ink")
    ink.add_argument("--threshold", type=int, default=128, metavar="0-255",
                     help="luminance below / alpha above which a pixel is ink "
                          "(default: 128)")
    ink.add_argument("--invert", action="store_true",
                     help="draw the background instead of the strokes, so the "
                          "logo reads as metal with the letters cut out of it")
    ink.add_argument("--crop", action="store_true",
                     help="size the macro from the strokes' bounding box "
                          "instead of the picture's canvas, dropping whatever "
                          "blank border the picture has (never with --invert, "
                          "where the border is the metal)")
    ink.add_argument("--ink", default="auto", choices=("auto", "luma", "alpha"),
                     help="which channel carries the ink (default: auto)")
    ink.add_argument("--coverage", type=float, default=0.5, metavar="0-1",
                     help="ink fraction that keeps a pixel when downsampling "
                          "(default: 0.5; lower fattens the strokes)")

    out = parser.add_argument_group("output")
    out.add_argument("--lef-class", default="BLOCK", choices=("BLOCK", "COVER"),
                     help="LEF macro class (default: BLOCK, which reserves the "
                          "footprint; COVER lets cells sit underneath)")
    out.add_argument("--recog", action="store_true",
                     help="draw a Recog (99/0) marker over the macro, which "
                          "exempts wide art from the slit rules -- needed by "
                          "--invert, which makes the logo a metal plate")
    out.add_argument("--no-obs", action="store_true",
                     help="omit the OBS block from the LEF")
    out.add_argument("--no-svg", action="store_true", help="skip the SVG preview")
    out.add_argument("--no-png", action="store_true", help="skip the PNG preview")
    out.add_argument("--no-check", dest="check", action="store_false",
                     help="skip the min-width and min-spacing measurement")
    out.add_argument("--strict", action="store_true",
                     help="exit non-zero if the geometry violates those rules")

    args = parser.parse_args()
    try:
        return convert(args)
    except (LogoError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
