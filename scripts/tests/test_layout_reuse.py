# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-14
#  Description:               Step 6 keeps a characterization its layout still has
# ================================================================

"""A verified cell whose layout is the published one is not characterized again.

PEX, the baseline, SPICE at every corner and the comparison depend only on the
drawn geometry, so re-running them on an unchanged cell is the hour step 6
spent for nothing. What must still run is the export and the publish checks
-- that is how an exporter fix reaches a cell nobody redrew. And the reuse is
only safe when it can be shown: the same geometry (not the same bytes, a
rebuilt GDS has new timestamps), the Liberty set LAYOUT_CORNERS asks for, and
LAYOUT_REUSE left on. Everything else runs the whole chain.
"""

from types import SimpleNamespace

import pytest

pya = pytest.importorskip("klayout.db")

import flow.steps.s6_layout_drawing as s6  # noqa: E402
from collect_cells import LIB_CORNERS  # noqa: E402
from flow.runner import Result  # noqa: E402

CELL = "AION_xor2_5"


def _gds(path, *boxes):
    layout = pya.Layout()
    layout.dbu = 0.001
    top = layout.create_cell(CELL)
    metal1 = layout.layer(8, 0)
    for box in boxes:
        top.shapes(metal1).insert(pya.Box(*box))
    path.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(path))
    return path


class _Runner:
    def __init__(self, output="STEP: export OK\n"):
        self.calls, self.output = [], output

    def layout(self, target, variables=(), *, name=""):
        self.calls.append((target, list(variables)))
        return Result(f"make aion-layout-{target}", 0, self.output, 0.0)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A published cell with a Liberty per corner, and a build that verified."""
    cells = tmp_path / "cells"
    published = cells / CELL
    for name in (f"{CELL}.lef", *(f"{CELL}_{corner}.lib" for corner in LIB_CORNERS)):
        published.mkdir(parents=True, exist_ok=True)
        (published / name).write_text("x\n")
    _gds(published / f"{CELL}.gds", (0, 0, 1000, 500))
    build = tmp_path / "build"
    _gds(build / f"{CELL}.gds", (0, 0, 1000, 500))       # rebuilt: new bytes, same shapes
    (build / f"{CELL}.compare.json").write_text('{"verdict_line": "COMPARE: WIN area -5%"}')
    monkeypatch.setattr(s6.paths, "CELLS_DIR", cells)

    step = s6.LayoutDrawingStep()
    published_calls = []
    monkeypatch.setattr(step, "_publish",
                        lambda ctx, cell, final, common: published_calls.append(cell))
    cfg = SimpleNamespace(LAYOUT_REUSE=True, LAYOUT_CORNERS="all", DRAW_PUBLISH_ON_LOSS=True)
    ctx = SimpleNamespace(cfg=cfg, runner=_Runner(), note=lambda message: None)
    return SimpleNamespace(step=step, ctx=ctx, cfg=cfg, build=build, published=published,
                           published_calls=published_calls)


def _reuse(world):
    return world.step._reuse_published(world.ctx, CELL, world.build, world.build / "final",
                                       [f"CELL={CELL}"])


def test_an_unchanged_published_cell_is_exported_again_and_not_characterized(world):
    outcome = _reuse(world)

    assert outcome is not None and outcome.published and outcome.state == "drawn"
    assert outcome.detail == "characterization reused"
    assert outcome.compare == "COMPARE: WIN area -5%", "the comparison it was published with"
    [(target, variables)] = world.ctx.runner.calls
    assert target == "export", "nothing but the export runs: no pex, baseline or characterize"
    libs = next(v for v in variables if v.startswith("CANDIDATE_LIBS="))
    for corner in LIB_CORNERS:
        assert str(world.published / f"{CELL}_{corner}.lib") in libs, libs
    assert world.published_calls == [CELL], "the publish checks run again"


def test_a_redrawn_layout_is_characterized_again(world):
    _gds(world.build / f"{CELL}.gds", (0, 0, 1000, 500), (2000, 0, 2200, 500))

    assert _reuse(world) is None
    assert world.ctx.runner.calls == []


def test_a_liberty_set_of_the_other_shape_is_characterized_again(world):
    world.cfg.LAYOUT_CORNERS = "typ"
    assert _reuse(world) is None, "per-corner files cannot stand in for <cell>.lib"


def test_layout_reuse_off_always_runs_the_whole_chain(world):
    world.cfg.LAYOUT_REUSE = False
    assert _reuse(world) is None


def test_a_cell_never_published_runs_the_whole_chain(world, monkeypatch):
    monkeypatch.setattr(s6.paths, "CELLS_DIR", world.build / "nowhere")
    assert _reuse(world) is None


def test_a_failed_re_export_falls_back_to_the_whole_chain(world):
    world.ctx.runner = _Runner(output="STEP: export FAIL\n")

    assert _reuse(world) is None
    assert world.published_calls == [], "nothing is published from a failed export"


def test_a_loss_is_not_republished_when_losses_are_withheld(world):
    (world.build / f"{CELL}.compare.json").write_text('{"verdict_line": "COMPARE: LOSS delay +20%"}')
    world.cfg.DRAW_PUBLISH_ON_LOSS = False
    assert _reuse(world) is None


def test_the_same_layout_is_decided_on_geometry_not_bytes(tmp_path):
    a = _gds(tmp_path / "a.gds", (0, 0, 100, 100))
    layout = pya.Layout()
    layout.read(str(a))
    layout.write(str(tmp_path / "b.gds"))
    assert s6._same_layout(a, tmp_path / "b.gds") is True

    _gds(tmp_path / "c.gds", (0, 0, 100, 105))
    assert s6._same_layout(a, tmp_path / "c.gds") is False
    (tmp_path / "junk.gds").write_bytes(b"not a gds")
    assert s6._same_layout(a, tmp_path / "junk.gds") is False
