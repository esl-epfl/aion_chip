# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-02
#  Updated:                   2026-09-10
#  Description:               Prepare a LibreLane configuration file
#                             for a VHDL design.
# ================================================================

import argparse
import json
import os
import re
import sys
from decimal import Decimal

from collect_cells import (collect_cells, collect_pdk_extension_cells,
                           corner_lib_problems, EXTRA_KEY_BY_VIEW, LIB_CORNERS)

# CELL_LIBS as ihp-sg13g2 declares it (libs.tech/librelane/config.tcl): per
# corner, the standard cells and then the I/O pads. Restated only when a cell
# brings a Liberty per corner, because LibreLane replaces CELL_LIBS wholesale
# rather than merging into the PDK's. The standard-cell library stays first:
# the IR-drop step reads the supply voltage off the first library it finds.
PDK_CELL_LIBS = {
    "typ_1p20V_25C": (
        "pdk_dir::libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_typ_1p20V_25C.lib",
        "pdk_dir::libs.ref/sg13g2_io/lib/sg13g2_io_typ_1p2V_3p3V_25C.lib",
    ),
    "fast_1p32V_m40C": (
        "pdk_dir::libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_fast_1p32V_m40C.lib",
        "pdk_dir::libs.ref/sg13g2_io/lib/sg13g2_io_fast_1p32V_3p6V_m40C.lib",
    ),
    "slow_1p08V_125C": (
        "pdk_dir::libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_slow_1p08V_125C.lib",
        "pdk_dir::libs.ref/sg13g2_io/lib/sg13g2_io_slow_1p08V_3p0V_125C.lib",
    ),
}
assert set(PDK_CELL_LIBS) == set(LIB_CORNERS), "PDK_CELL_LIBS and LIB_CORNERS disagree"


# Flow that stops after synthesis for VHDL designs. Synthesis proper, its
# sanity checkers, then a pre-PnR STA so the SDC is exercised and a timing
# report lands next to the netlist.
VHDL_SYNTH_ONLY_FLOW = [
    "Yosys.VHDLSynthesis",
    "Checker.YosysUnmappedCells",
    "Checker.YosysSynthChecks",
    "Checker.NetlistAssignStatements",
    "OpenROAD.CheckSDCFiles",
    "OpenROAD.STAPrePNR",
]

# Keys the template carries for our own benefit that LibreLane must not see.
# Any key starting with "//" is a comment: the template groups its notes next to
# the settings they explain ("//floorplan", "//power"), which JSON has no syntax
# for and LibreLane would report as unknown variables.
TEMPLATE_ONLY_KEYS = ("_modes",)
COMMENT_PREFIX = "//"


def is_template_only(key) -> bool:
    """Whether a key belongs to the template rather than to LibreLane."""
    return isinstance(key, str) and (
        key in TEMPLATE_ONLY_KEYS or key.startswith(COMMENT_PREFIX)
    )

MODES = ("synth", "pnr", "pnr_simple")


# Only this subtree is mounted into the LibreLane container (see
# scripts/docker_run.sh), so a dir:: path that escapes it resolves to nothing
# once the flow actually runs.
PROJECT_ROOT = None


def make_relative(path: str, base: str) -> str:
    """Return a path relative to base, using LibreLane's dir:: prefix."""
    abs_path = os.path.abspath(path)
    abs_base = os.path.abspath(base)

    if PROJECT_ROOT is not None:
        if os.path.commonpath([abs_path, PROJECT_ROOT]) != PROJECT_ROOT:
            print(
                f"Error: {abs_path} lies outside {PROJECT_ROOT}, which is the only "
                "directory mounted into the LibreLane container. Move it into the "
                "project, or symlink it in.",
                file=sys.stderr,
            )
            sys.exit(1)

    rel = os.path.relpath(abs_path, abs_base)
    return f"dir::{rel}"


def cell_library_config(sources, ip_dir: str, cell_libs):
    """The EXTRA_* views and the CELL_LIBS map for the cells PnR is handed.

    `sources` is [(label, cells)] from collect_cells(); `cell_libs` is the
    CELL_LIBS map --cell-lib built, or empty. Returns (views, cell_libs):
    views maps each EXTRA_* key to its dir:: paths, and cell_libs is the map to
    write, or the one given when no cell brings a Liberty per corner.

    A cell's single <CELL>.lib goes to EXTRA_LIBS, which LibreLane loads into
    every timing corner alike. A Liberty per corner cannot go there -- each
    corner would read all of them -- so each one is appended to its own
    corner's CELL_LIBS entry, after the PDK's libraries for that corner, which
    are left exactly as they were. Raises ValueError for a cell whose corner set
    is incomplete or doubled: no Liberty in a corner cannot link, and two in one
    corner is two timing models of the same cell.
    """
    views = {}
    per_corner = {}
    for _, cells in sources:
        for cell in cells:
            errors, _ = corner_lib_problems(cell)
            if errors:
                raise ValueError(errors[0])
            for view, path in cell.views.items():
                views.setdefault(EXTRA_KEY_BY_VIEW[view], []).append(
                    make_relative(path, ip_dir))
            for corner, path in cell.corner_libs.items():
                per_corner.setdefault(corner, []).append(make_relative(path, ip_dir))
    if not per_corner:
        return views, cell_libs

    merged = {key: list(paths) for key, paths in (
        cell_libs or {f"*_{corner}": PDK_CELL_LIBS[corner] for corner in LIB_CORNERS}
    ).items()}
    for corner, paths in sorted(per_corner.items()):
        key = f"*_{corner}"
        if key not in merged:
            raise ValueError(
                f"CELL_LIBS has no {key!r} entry to add the {corner} Liberty of "
                f"the cells to; it has {', '.join(sorted(merged))}")
        merged[key] += sorted(paths)
    return views, merged


def def_die_area(path: str) -> list:
    """The DIEAREA of a DEF file, in microns, as [x0, y0, x1, y1]."""
    units = None
    with open(path) as f:
        for line in f:
            match = re.match(r"\s*UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", line)
            if match:
                units = int(match.group(1))
                continue
            match = re.match(
                r"\s*DIEAREA\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)"
                r"\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*;", line)
            if match:
                if units is None:
                    break
                return [Decimal(v) / units for v in match.groups()]
    print(f"Error: no UNITS and DIEAREA statements in {path}", file=sys.stderr)
    sys.exit(1)


def strip_template_keys(obj):
    """Recursively drop the comment and overlay keys from the template."""
    if isinstance(obj, dict):
        return {
            k: strip_template_keys(v)
            for k, v in obj.items()
            if not is_template_only(k)
        }
    if isinstance(obj, list):
        return [strip_template_keys(v) for v in obj]
    return obj


def apply_overlay(config: dict, overlay: dict, label: str) -> None:
    """Merge an overlay into the config, one level deep for 'meta'."""
    for key, value in overlay.items():
        if is_template_only(key):
            continue
        if key == "meta" and isinstance(value, dict):
            config.setdefault("meta", {}).update(value)
        else:
            config[key] = value
    keys = sorted(k for k in overlay if not is_template_only(k))
    if keys:
        print(f"  overlay '{label}': {', '.join(keys)}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate a LibreLane config.json for a VHDL design."
    )
    parser.add_argument(
        "--src-config",
        required=True,
        help="Path to the source implementation/config.json template.",
    )
    parser.add_argument(
        "--dst-config",
        required=True,
        help="Path where the generated LibreLane config.json will be written.",
    )
    parser.add_argument(
        "--ip-dir",
        required=True,
        help="Directory used as the base for dir:: relative paths (the run dir).",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="pnr_simple",
        help="Which flow this configuration is for (default: pnr_simple).",
    )
    parser.add_argument(
        "--vhdl-files",
        required=True,
        help="Comma-separated list of VHDL source file paths.",
    )
    parser.add_argument(
        "--sdc",
        default=None,
        help="Path to the SDC file used for both PnR and signoff STA.",
    )
    parser.add_argument(
        "--pin-order",
        default=None,
        help="Path to the pin order configuration file (optional).",
    )
    parser.add_argument(
        "--def-template",
        default=None,
        help="Path to a DEF template that fixes pin placement and PDN (optional). "
        "Overrides --pin-order when both are given.",
    )
    parser.add_argument(
        "--macro",
        action="append",
        default=[],
        metavar="NAME=DIR",
        help="Fill in MACROS[NAME]'s view paths from DIR/NAME.{gds,lef,lib}. "
        "The template declares the macro and where its instances go; this only "
        "rewrites the paths, which is the one part that depends on the run "
        "directory. Repeatable.",
    )
    parser.add_argument(
        "--cells-dir",
        default=None,
        help="Directory holding AI-generated cell views to wire in as EXTRA_* "
        "(only meaningful in --mode pnr).",
    )
    parser.add_argument(
        "--pdk-ext-dir",
        default=None,
        help="implementation/pdk_extension/, whose hand-designed cells are "
        "wired in as EXTRA_* beside the mined ones. A separate flag from "
        "--cells-dir because the two directories name their views "
        "differently, not because the cells are treated differently (only "
        "meaningful in --mode pnr).",
    )
    parser.add_argument(
        "--cell-lib",
        action="append",
        default=[],
        metavar="CORNER=LIB",
        help="Replace the PDK's standard-cell Liberty for one timing corner. "
        "CORNER is a CELL_LIBS corner wildcard, e.g. '*_typ_1p20V_25C'. "
        "Repeatable, and repeating the same corner adds a second library to "
        "it. Passing any at all replaces CELL_LIBS wholesale, so give one per "
        "corner in STA_CORNERS.",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Directory mounted into the LibreLane container. Referenced files "
        "must live inside it.",
    )
    parser.add_argument(
        "--lenient",
        action="store_true",
        help="Apply the 'lenient' overlay: downgrade hard checkers to warnings.",
    )
    args = parser.parse_args()

    if args.project_root is not None:
        global PROJECT_ROOT
        PROJECT_ROOT = os.path.abspath(args.project_root)

    if not os.path.isfile(args.src_config):
        print(f"Error: source config not found: {args.src_config}", file=sys.stderr)
        sys.exit(1)

    with open(args.src_config, "r") as f:
        template = json.load(f)

    modes = template.get("_modes", {})
    config = strip_template_keys(template)

    print(f"[prepare] mode={args.mode} -> {args.dst_config}")

    # Ensure the VHDLClassic flow is selected. `synth` narrows it to a
    # hand-picked step list; the PnR modes keep the full flow and are trimmed
    # from the front by `librelane --from` instead.
    config.setdefault("meta", {})
    config["meta"]["flow"] = "VHDLClassic"

    # Convert absolute VHDL paths to dir:: paths relative to the run directory.
    # The PnR-from-netlist mode skips synthesis, but VHDL_FILES is still a
    # required variable of the (skipped) synthesis step, so it stays populated.
    vhdl_files = [p.strip() for p in args.vhdl_files.split(",") if p.strip()]
    config["VHDL_FILES"] = [make_relative(p, args.ip_dir) for p in vhdl_files]

    # Remove Verilog-specific keys that are not used by the VHDLClassic flow.
    for key in (
        "VERILOG_FILES",
        "VERILOG_INCLUDE_DIRS",
        "USE_SLANG",
        "SLANG_ARGUMENTS",
    ):
        config.pop(key, None)

    # ------------------------------------------------------------------
    # Timing constraints
    # ------------------------------------------------------------------
    if args.sdc is not None:
        if not os.path.isfile(args.sdc):
            print(f"Error: SDC file not found: {args.sdc}", file=sys.stderr)
            sys.exit(1)
        sdc = make_relative(args.sdc, args.ip_dir)
        config["PNR_SDC_FILE"] = sdc
        config["SIGNOFF_SDC_FILE"] = sdc
        print(f"  sdc: {sdc}")
    else:
        config.pop("PNR_SDC_FILE", None)
        config.pop("SIGNOFF_SDC_FILE", None)

    # ------------------------------------------------------------------
    # Pin placement
    # ------------------------------------------------------------------
    config.pop("IO_PIN_ORDER_CFG", None)
    config.pop("FP_DEF_TEMPLATE", None)

    if args.def_template is not None:
        if not os.path.isfile(args.def_template):
            print(
                f"Error: DEF template not found: {args.def_template}", file=sys.stderr
            )
            sys.exit(1)
        config["FP_DEF_TEMPLATE"] = make_relative(args.def_template, args.ip_dir)
        print(f"  def template: {config['FP_DEF_TEMPLATE']}")
    elif args.pin_order is not None:
        if not os.path.isfile(args.pin_order):
            print(f"Error: pin order file not found: {args.pin_order}", file=sys.stderr)
            sys.exit(1)
        config["IO_PIN_ORDER_CFG"] = make_relative(args.pin_order, args.ip_dir)
        print(f"  pin order: {config['IO_PIN_ORDER_CFG']}")

    # ------------------------------------------------------------------
    # Standard-cell Liberty
    #
    # CELL_LIBS (LibreLane <= 3.0 spelled it LIB) is the list the technology
    # mapper reads -- librelane/steps/pyosys.py hands it to ABC -- so a cell
    # that is to be *synthesized* has to be in the file this points at.
    # EXTRA_LIBS, which --cells-dir fills in, is read with `-setattr blackbox`
    # and registers a cell for STA and hierarchy while hiding it from the
    # mapper: right for a macro, wrong for a standard cell. See
    # implementation/pdk_extension/README.md.
    #
    # The variable is a whole map of corner wildcard -> libraries and
    # LibreLane replaces it rather than merging into the PDK's, so every
    # corner the design is timed at has to appear here. A corner left out has
    # no Liberty at all, and OpenSTA cannot link a netlist whose cells it has
    # never read.
    # ------------------------------------------------------------------
    config.pop("CELL_LIBS", None)
    config.pop("LIB", None)

    cell_libs = {}
    for spec in args.cell_lib:
        corner, sep, path = spec.partition("=")
        if not sep or not corner.strip() or not path.strip():
            print(
                f"Error: --cell-lib expects CORNER=LIB, got {spec!r}",
                file=sys.stderr,
            )
            sys.exit(1)
        corner, path = corner.strip(), path.strip()
        if not os.path.isfile(path):
            print(
                f"Error: --cell-lib {corner}: no Liberty at {path}.\n"
                f"       Build it with `make pdk_ext_lib`, or synthesize "
                f"against the plain PDK library with PDK_EXT=0.",
                file=sys.stderr,
            )
            sys.exit(1)
        cell_libs.setdefault(corner, []).append(make_relative(path, args.ip_dir))

    if cell_libs:
        config["CELL_LIBS"] = cell_libs
        for corner, libs in sorted(cell_libs.items()):
            print(f"  cell libs {corner}: {', '.join(libs)}")

    # ------------------------------------------------------------------
    # Cells that are not the PDK's
    #
    # Two sources, one destination. The mined cells come from --cells-dir,
    # where a cell is a set of files sharing one stem; the hand-designed ones
    # come from --pdk-ext-dir, where the views are named exactly because that
    # directory also holds the cell's sources. Both land in the same EXTRA_*
    # lists, because from PnR's side there is no difference: a leaf standard
    # cell with a LEF, a Liberty and a GDS.
    #
    # A single Liberty per cell goes to EXTRA_LIBS and NOT to CELL_LIBS, which
    # is the opposite of what `make synth` does with the same cells. Both are
    # right. Synthesis has to offer them to ABC, and only CELL_LIBS is read by
    # the mapper. PnR maps nothing -- the netlist is fixed, and `--from
    # Checker.NetlistAssignStatements` skips synthesis entirely -- so all the
    # Liberty has to do is let OpenSTA link and time the instances, which is
    # what EXTRA_LIBS is for. Putting them in both would define every
    # extension cell twice.
    #
    # EXTRA_LIBS is read into every timing corner alike, though, so a cell
    # characterized per corner (<CELL>_<corner>.lib, LAYOUT_CORNERS=all) has
    # each corner's Liberty appended to that corner's CELL_LIBS entry instead,
    # after the PDK's own libraries, which stay exactly as they were: the PDK
    # cells are timed against the same files as in `make pnr_simple`, so the
    # substitution is still the only variable between the two runs, and step
    # 9's comparison still measures one thing. CELL_LIBS also feeds the
    # OpenROAD GUI's library list, which EXTRA_LIBS never reaches.
    # ------------------------------------------------------------------
    for extra_key in EXTRA_KEY_BY_VIEW.values():
        config.pop(extra_key, None)

    sources = []
    if args.cells_dir is not None and os.path.isdir(args.cells_dir):
        sources.append((args.cells_dir, collect_cells(args.cells_dir)))
    if args.pdk_ext_dir is not None and os.path.isdir(args.pdk_ext_dir):
        sources.append((args.pdk_ext_dir,
                        collect_pdk_extension_cells(args.pdk_ext_dir)))

    for label, cells in sources:
        if not cells:
            print(f"  cells: none found under {label}")
            continue
        print(f"  cells: {len(cells)} from {label} "
              f"({', '.join(c.name for c in cells)})")
    try:
        views, with_cells = cell_library_config(sources, args.ip_dir, cell_libs)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    for key, paths in sorted(views.items()):
        config[key] = sorted(paths)
    if views:
        print(f"  cell views -> {', '.join(sorted(views))}")
    if with_cells is not cell_libs:
        config["CELL_LIBS"] = with_cells
        for corner, libs in sorted(with_cells.items()):
            print(f"  cell libs {corner}: {', '.join(libs)}")

    # ------------------------------------------------------------------
    # Macros
    #
    # A macro's placement belongs in the template next to the floorplan it
    # depends on; only its view paths have to be recomputed per run directory,
    # so that is all this does. A --macro naming a key the template does not
    # declare is a typo, not a request to invent a placement, so it fails.
    # ------------------------------------------------------------------
    for spec in args.macro:
        name, _, directory = spec.partition("=")
        if not name or not directory:
            print(f"Error: --macro expects NAME=DIR, got {spec!r}", file=sys.stderr)
            sys.exit(1)
        macros = config.get("MACROS")
        if not isinstance(macros, dict) or name not in macros:
            print(
                f"Error: --macro {name}: the config template declares no "
                f"MACROS['{name}'] to attach views to.",
                file=sys.stderr,
            )
            sys.exit(1)
        views = {}
        for view, key in (("gds", "gds"), ("lef", "lef"), ("lib", "lib")):
            path = os.path.join(directory, f"{name}.{view}")
            if os.path.isfile(path):
                views[key] = [make_relative(path, args.ip_dir)]
        for required in ("gds", "lef"):
            if required not in views:
                print(
                    f"Error: --macro {name}: no {name}.{required} in {directory} "
                    "-- run `make logo` first?",
                    file=sys.stderr,
                )
                sys.exit(1)
        macros[name].update(views)
        print(f"  macro {name}: {', '.join(sorted(views))} from {directory}")

    # ------------------------------------------------------------------
    # Mode overlays
    # ------------------------------------------------------------------
    apply_overlay(config, modes.get(args.mode, {}), args.mode)
    if args.lenient:
        apply_overlay(config, modes.get("lenient", {}), "lenient")

    # ------------------------------------------------------------------
    # Die area against the DEF template
    #
    # The template fixes the pins and DIE_AREA fixes the die, and nothing
    # downstream holds the two together: Odb.ApplyDEFTemplate only warns when
    # they differ, then places the pins at the template's coordinates on
    # whatever die it was given. TinyTapeout's precheck compares both the
    # LEF's SIZE and every pin rectangle against the template, so a mismatch
    # is a run that cannot be submitted -- refused here rather than found
    # after PnR. Checked after the overlays, so no mode can slip past it.
    # ------------------------------------------------------------------
    if args.def_template is not None:
        template_area = def_die_area(args.def_template)
        die_area = config.get("DIE_AREA")
        if die_area is None:
            config["DIE_AREA"] = [float(v) for v in template_area]
        elif [Decimal(str(v)) for v in die_area] != template_area:
            print(
                f"Error: DIE_AREA {die_area} is not the die of "
                f"{args.def_template} ({[float(v) for v in template_area]}).\n"
                "       The template's pins only line up with its own die: set "
                "DIE_AREA in the config template to the template's.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"  die area: {config['DIE_AREA']} (the DEF template's)")

    if args.mode == "synth":
        config["meta"]["flow"] = VHDL_SYNTH_ONLY_FLOW
        # The trimmed flow has no step that consumes these.
        config["meta"].pop("substituting_steps", None)

    os.makedirs(os.path.dirname(args.dst_config), exist_ok=True)
    with open(args.dst_config, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
