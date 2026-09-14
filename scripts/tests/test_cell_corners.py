# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-14
#  Description:               A cell's Liberty per timing corner
# ================================================================

"""A cell characterized at every corner is one cell, timed per corner.

Step 6 with LAYOUT_CORNERS=all publishes <CELL>_typ_1p20V_25C.lib,
<CELL>_slow_1p08V_125C.lib and <CELL>_fast_1p32V_m40C.lib. Grouped by stem,
the way discovery used to, those were three phantom cells with only a
Liberty, and PnR refused to start. Handed to LibreLane as EXTRA_LIBS, which is
read into every corner alike, each corner would have held all three timing
models of the cell. These pin the three things that have to hold instead:
discovery makes one cell of them, step 6 publishes exactly that set, and each
corner's Liberty lands in that corner's CELL_LIBS entry, after the PDK's.
"""

import pytest

import collect_cells as cc
import prepare_librelane_config as prep

CELL = "AION_xor2_5"
CORNER_FILES = [f"{CELL}_{corner}.lib" for corner in cc.LIB_CORNERS]


def _publish(root, *names, cell=CELL):
    directory = root / "cells" / cell
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text(f"library (x) {{ cell ({cell}) {{ }} }}\n")
    return root / "cells"


def _only(cells):
    assert len(cells) == 1, [cell.name for cell in cells]
    return cells[0]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_a_liberty_per_corner_is_one_cell_not_three(tmp_path):
    cells_dir = _publish(tmp_path, f"{CELL}.lef", f"{CELL}.gds", *CORNER_FILES)
    cell = _only(cc.collect_cells(str(cells_dir)))

    assert cell.name == CELL
    assert sorted(cell.corner_libs) == sorted(cc.LIB_CORNERS)
    assert "lib" not in cell.views, "a corner file is not the cell's single Liberty"
    assert cell.missing(("lef", "lib", "gds")) == [], "the corner set is the lib view"
    assert cc.corner_lib_problems(cell) == ([], [])


def test_a_corner_missing_from_the_set_is_an_error(tmp_path):
    """A corner with no Liberty for the cell cannot link the netlist at all."""
    cells_dir = _publish(tmp_path, f"{CELL}.lef", f"{CELL}.gds", *CORNER_FILES[:2])
    errors, _ = cc.corner_lib_problems(_only(cc.collect_cells(str(cells_dir))))

    assert len(errors) == 1 and "fast_1p32V_m40C" in errors[0], errors


def test_a_stale_single_liberty_beside_a_corner_set_is_an_error(tmp_path):
    cells_dir = _publish(tmp_path, f"{CELL}.lef", f"{CELL}.gds", f"{CELL}.lib", *CORNER_FILES)
    errors, _ = cc.corner_lib_problems(_only(cc.collect_cells(str(cells_dir))))

    assert len(errors) == 1 and f"{CELL}.lib" in errors[0] and "two timing models" in errors[0]


def test_a_single_liberty_is_still_a_cell_and_says_what_it_costs(tmp_path):
    cells_dir = _publish(tmp_path, f"{CELL}.lef", f"{CELL}.gds", f"{CELL}.lib")
    cell = _only(cc.collect_cells(str(cells_dir)))
    errors, warnings = cc.corner_lib_problems(cell)

    assert cell.corner_libs == {} and "lib" in cell.views
    assert errors == []
    assert len(warnings) == 1 and "same data at slow and fast" in warnings[0], warnings


# ---------------------------------------------------------------------------
# What step 6 may publish
# ---------------------------------------------------------------------------

def test_step_6_publishes_one_liberty_or_exactly_one_per_corner():
    assert cc.published_lib_problem(CELL, [f"{CELL}.lib"]) is None
    assert cc.published_lib_problem(CELL, reversed(CORNER_FILES)) is None

    partial = cc.published_lib_problem(CELL, CORNER_FILES[:2])
    assert partial is not None and "exactly one per corner" in partial, partial
    assert cc.published_lib_problem(CELL, [f"{CELL}.lib", *CORNER_FILES]) is not None
    assert cc.published_lib_problem(CELL, ["AION_other.lib"]) is not None
    assert cc.published_lib_problem(CELL, []) is not None


# ---------------------------------------------------------------------------
# What LibreLane is given
# ---------------------------------------------------------------------------

def _config(tmp_path, cells_dir, cell_libs=None):
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    sources = [(str(cells_dir), cc.collect_cells(str(cells_dir)))]
    return prep.cell_library_config(sources, str(run), cell_libs or {})


def test_each_corner_liberty_goes_to_its_own_corner_after_the_pdks(tmp_path):
    cells_dir = _publish(tmp_path, f"{CELL}.lef", f"{CELL}.gds", *CORNER_FILES)
    views, cell_libs = _config(tmp_path, cells_dir)

    assert "EXTRA_LIBS" not in views, "EXTRA_LIBS is read into every corner alike"
    assert views["EXTRA_LEFS"] == [f"dir::../cells/{CELL}/{CELL}.lef"]
    assert sorted(cell_libs) == sorted(f"*_{corner}" for corner in cc.LIB_CORNERS)
    for corner in cc.LIB_CORNERS:
        libs = cell_libs[f"*_{corner}"]
        assert libs[:2] == list(prep.PDK_CELL_LIBS[corner]), (
            f"the PDK's libraries for {corner} stay first and unchanged: {libs}")
        assert libs[2:] == [f"dir::../cells/{CELL}/{CELL}_{corner}.lib"], libs
        assert "sg13g2_stdcell" in libs[0], "the IR-drop step reads its voltage off the first"


def test_a_single_liberty_still_goes_to_extra_libs_and_leaves_cell_libs_alone(tmp_path):
    cells_dir = _publish(tmp_path, f"{CELL}.lef", f"{CELL}.gds", f"{CELL}.lib")
    views, cell_libs = _config(tmp_path, cells_dir)

    assert views["EXTRA_LIBS"] == [f"dir::../cells/{CELL}/{CELL}.lib"]
    assert cell_libs == {}, "LibreLane keeps the PDK's CELL_LIBS: nothing is restated"


def test_cells_of_both_kinds_are_each_placed_where_they_belong(tmp_path):
    _publish(tmp_path, f"{CELL}.lef", f"{CELL}.gds", *CORNER_FILES)
    cells_dir = _publish(tmp_path, "AION_mux2_0.lef", "AION_mux2_0.gds", "AION_mux2_0.lib",
                         cell="AION_mux2_0")
    views, cell_libs = _config(tmp_path, cells_dir)

    assert views["EXTRA_LIBS"] == ["dir::../cells/AION_mux2_0/AION_mux2_0.lib"]
    assert cell_libs["*_slow_1p08V_125C"][-1] == f"dir::../cells/{CELL}/{CELL}_slow_1p08V_125C.lib"


def test_corner_libraries_extend_a_cell_libs_map_given_on_the_command_line(tmp_path):
    """--cell-lib replaces the PDK map; the cells' corners are added to that one."""
    cells_dir = _publish(tmp_path, f"{CELL}.lef", *CORNER_FILES)
    given = {f"*_{corner}": [f"dir::merged_{corner}.lib"] for corner in cc.LIB_CORNERS}
    _, cell_libs = _config(tmp_path, cells_dir, given)

    assert cell_libs["*_typ_1p20V_25C"] == [
        "dir::merged_typ_1p20V_25C.lib", f"dir::../cells/{CELL}/{CELL}_typ_1p20V_25C.lib"]
    assert given["*_typ_1p20V_25C"] == ["dir::merged_typ_1p20V_25C.lib"], "the input is not mutated"

    with pytest.raises(ValueError, match="no '\\*_fast_1p32V_m40C' entry"):
        _config(tmp_path, cells_dir, {key: value for key, value in given.items()
                                      if "fast" not in key})


def test_an_incomplete_corner_set_never_reaches_librelane(tmp_path):
    """LENIENT=1 downgrades the check before PnR; the config still refuses it."""
    cells_dir = _publish(tmp_path, f"{CELL}.lef", *CORNER_FILES[:1])
    with pytest.raises(ValueError, match="no Liberty for corner"):
        _config(tmp_path, cells_dir)
