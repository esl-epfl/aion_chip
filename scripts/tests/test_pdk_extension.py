# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-14
#  Description:               PDK-extension cells: a Liberty per corner, and
#                             a drawing agent told what verify failed
# ================================================================

"""The hand-designed cells get what the mined cells got.

Two things. A Liberty per timing corner, all the way through: pdk_cell.py
publishes <CELL>_<corner>.lib, discovery gives each corner its own file,
merge_lib.py splices each corner's into that corner's merged library. And the
drawing agent is handed the verify verdict itself, not only the evidence
packet: AION_mux2i_1/2 fail pin access, the rail band, the side edges and the
two-ways-up rule, and none of that is in the packet.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import collect_cells as cc
import merge_lib
import pdk_cell
from flow import agent

CELL = "AION_mux2i_1"
CORNER_LIBS = [f"{CELL}_{corner}.lib" for corner in cc.LIB_CORNERS]


def _files(directory: Path, *names: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text(f"library (x) {{ cell ({CELL}) {{ }} }}\n")
    return directory


# ---------------------------------------------------------------------------
# Discovery and the merged Liberty
# ---------------------------------------------------------------------------

def test_an_extension_cell_published_per_corner_has_a_liberty_per_corner(tmp_path):
    _files(tmp_path / CELL, f"{CELL}.lef", f"{CELL}.gds", f"{CELL}.v",
           f"{CELL}.provisional.lib", *CORNER_LIBS)
    [cell] = cc.collect_pdk_extension_cells(str(tmp_path))

    assert sorted(cell.corner_libs) == sorted(cc.LIB_CORNERS)
    assert "lib" not in cell.views, "the provisional estimate is not a view"
    assert cell.missing(("lef", "lib", "gds")) == []
    assert cc.corner_lib_problems(cell) == ([], [])


def test_merge_lib_splices_each_corner_with_its_own_liberty(tmp_path):
    _files(tmp_path / CELL, *CORNER_LIBS, f"{CELL}.provisional.lib")

    for corner in cc.LIB_CORNERS:
        path, how = merge_lib.extension_liberty(CELL, corner, root=tmp_path)
        assert path.name == f"{CELL}_{corner}.lib" and how == "characterized"


def test_merge_lib_refuses_a_corner_set_with_this_corner_missing(tmp_path):
    """Not a quiet fall-back to another corner's tables, nor to the estimate."""
    _files(tmp_path / CELL, *CORNER_LIBS[:2], f"{CELL}.provisional.lib")

    with pytest.raises(SystemExit, match="no Liberty for fast_1p32V_m40C"):
        merge_lib.extension_liberty(CELL, "fast_1p32V_m40C", provisional=True, root=tmp_path)


def test_a_single_liberty_is_spliced_everywhere_and_says_so_off_typ(tmp_path):
    _files(tmp_path / CELL, f"{CELL}.lib")

    assert merge_lib.extension_liberty(CELL, "typ_1p20V_25C", root=tmp_path)[1] == "characterized"
    path, how = merge_lib.extension_liberty(CELL, "slow_1p08V_125C", root=tmp_path)
    assert path.name == f"{CELL}.lib" and how == "characterized at typ only"


def test_the_estimate_is_used_only_when_asked_for(tmp_path):
    _files(tmp_path / CELL, f"{CELL}.provisional.lib")

    with pytest.raises(SystemExit, match="no characterized Liberty"):
        merge_lib.extension_liberty(CELL, "typ_1p20V_25C", root=tmp_path)
    assert merge_lib.extension_liberty(CELL, "typ_1p20V_25C", provisional=True,
                                       root=tmp_path)[1] == "PROVISIONAL"


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

@pytest.fixture
def cell(tmp_path, monkeypatch):
    monkeypatch.setattr(pdk_cell, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(pdk_cell, "CELLS_ROOT", tmp_path / "pdk_extension")
    monkeypatch.setattr(pdk_cell, "BUILD_ROOT", tmp_path / "build")
    monkeypatch.setattr(pdk_cell, "LAYOUT_CELLS", tmp_path / "generators")
    monkeypatch.setattr(pdk_cell, "check_lef", lambda path: [])
    monkeypatch.setattr(pdk_cell, "_render_png", lambda cell, gds: None)
    it = pdk_cell.Cell(CELL)
    _files(it.dir, f"{CELL}.v", f"{CELL}.spice", f"{CELL}.provisional.lib", f"{CELL}.lib")
    return it


def _export(cell, *libs):
    final = _files(cell.build / "layout" / "final", f"{CELL}.lef", f"{CELL}.gds", *libs)
    (final / f"{CELL}.spice").write_bytes(cell.spice.read_bytes())
    return final


def test_publishing_a_corner_set_replaces_the_single_liberty(cell):
    _export(cell, *CORNER_LIBS)

    assert pdk_cell._publish_views(cell) is True
    published = sorted(p.name for p in cell.dir.glob("*.lib"))
    assert published == sorted(CORNER_LIBS + [f"{CELL}.provisional.lib"]), (
        f"the earlier {CELL}.lib goes, the authored estimate stays: {published}")


def test_a_corner_set_with_a_corner_missing_is_not_published(cell):
    _export(cell, *CORNER_LIBS[:2])

    assert pdk_cell._publish_views(cell) is False
    assert (cell.dir / f"{CELL}.lib").exists(), "nothing was cleared for a refused publish"


def test_the_corners_default_to_all(monkeypatch):
    """The parser is built inside main(), so the parsed namespace is captured there."""
    import argparse
    original, seen = argparse.ArgumentParser.parse_args, {}

    def capture(self, argv=None, namespace=None):
        seen.update(vars(original(self, argv, namespace)))
        raise SystemExit(0)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(SystemExit):
        pdk_cell.main([CELL])
    assert seen["corners"] == "all"


# ---------------------------------------------------------------------------
# The drawing agent's prompt
# ---------------------------------------------------------------------------

def test_the_agent_is_handed_the_verdict_not_only_the_evidence(cell, monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["prompt"] = argv[argv.index("-p") + 1]
        return agent.AgentTurn(returncode=0, report="done")

    monkeypatch.setattr(pdk_cell.agent, "run", fake_run)
    run = SimpleNamespace(log_dir=cell.build / "logs", quiet=True)
    verdict = "  3. abstract (TritonRoute): PIN I2: no access point\nRESULT: FAIL"

    pdk_cell._agent_turn(cell, run, pdk_cell.Draw(), verdict, "evidence body", 1)

    prompt = captured["prompt"]
    assert "=== the verdict of the last `make verify` ===" in prompt
    assert "abstract (TritonRoute): PIN I2: no access point" in prompt
    assert "evidence body" in prompt
    assert prompt.index("PIN I2: no access point") < prompt.index("evidence body")


def test_a_report_from_an_earlier_turn_is_never_passed_off_as_this_ones(cell, monkeypatch):
    """A verify that died before grading writes no report; its output stands in."""
    cell.report.parent.mkdir(parents=True, exist_ok=True)
    cell.report.write_text("STALE verdict from yesterday\nRESULT: FAIL\n")
    cell.generator.parent.mkdir(parents=True, exist_ok=True)
    cell.generator.write_text("# v0\n")
    handed = []

    def fake_verify(cell_, run, common, turn=None):
        if turn == 1:
            return pdk_cell.Result(2, "magic crashed before grading\n")
        return pdk_cell.Result(0, "RESULT: PASS\n")

    def fake_turn(cell_, run, draw, verdict, evidence, turn):
        handed.append(verdict)
        cell_.generator.write_text(f"# v{turn}\n")
        return "edited"

    monkeypatch.setattr(pdk_cell, "_verify", fake_verify)
    monkeypatch.setattr(pdk_cell, "_evidence", lambda *a, **k: "")
    monkeypatch.setattr(pdk_cell, "_agent_turn", fake_turn)

    assert pdk_cell._draw_auto(cell, None, [], pdk_cell.Draw(max_iters=3)) == "RESULT: PASS"
    assert len(handed) == 1
    assert "STALE" not in handed[0] and "magic crashed before grading" in handed[0], handed


def test_verdict_text_reads_the_report_when_verify_wrote_one(tmp_path):
    report = tmp_path / "cell.report.md"
    report.write_text("  1. abstract (LEF): PIN I0 can put a Via2 on 1 Metal3 track\nRESULT: FAIL\n")

    assert "PIN I0 can put a Via2" in agent.verdict_text(report, "ignored output")
