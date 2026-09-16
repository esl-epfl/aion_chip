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
# Publishing a cell that lost the comparison on delay
# ---------------------------------------------------------------------------

#: The tail of AION_maj3i_1's first post-layout run, verbatim.
LOSS_RUN = """\
  [1/7] build + DRC + LVS (candidate)
  RESULT: PASS
  [7/7] compare against the abutted baseline
  COMPARE: LOSS area -36.4% (12.701 vs 19.958 um2)  worst delay +3.3% (520.0 vs 503.1 ps)
COMPARE: LOSS area -36.4% (12.701 vs 19.958 um2) worst delay +3.3% (520.0 vs 503.1 ps)
make[2]: *** [Makefile:206: flow] Error 1
"""
WIN_RUN = ("  RESULT: PASS\nCOMPARE: WIN  area -46.2% (12.701 vs 23.587 um2)  "
           "worst delay -24.6% (324.4 vs 430.4 ps)\n")


def test_the_comparison_is_read_from_its_column_zero_line():
    assert pdk_cell.read_comparison(LOSS_RUN) == pdk_cell.Comparison("LOSS", -36.4, 3.3)
    assert pdk_cell.read_comparison(WIN_RUN) == pdk_cell.Comparison("WIN", -46.2, -24.6)
    assert pdk_cell.read_comparison("  [4/7] characterize (candidate)\n") is None


@pytest.mark.parametrize("output, accept, allowed, accepted", [
    (WIN_RUN, None, True, False),
    (LOSS_RUN, None, False, False),                 # the default stays strict
    (LOSS_RUN, 5, True, True),
    (LOSS_RUN, 3.3, True, True),                    # the printed value, inclusive
    (LOSS_RUN, 2, False, False),                    # slower than accepted
    (LOSS_RUN.replace("area -36.4%", "area +4.0%"), 50, False, False),   # not smaller
    (LOSS_RUN.replace("RESULT: PASS", "RESULT: FAIL"), 5, False, False),  # re-graded
    ("  [4/7] characterize (candidate)\nTraceback ...\n", 5, False, False),  # never compared
])
def test_a_loss_is_published_only_when_accepted_and_smaller(output, accept, allowed, accepted):
    got_allowed, got_accepted, reason = pdk_cell.may_publish(output, accept)
    assert (got_allowed, got_accepted) == (allowed, accepted), reason


def test_publish_only_reuses_the_last_run_without_running_it(cell, monkeypatch, capsys):
    """`--only publish` needs no container and runs no make target."""
    _export(cell, *CORNER_LIBS)
    log = pdk_cell.Runner().log_dir / f"{CELL}.flow.log"   # where post-layout wrote it
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(LOSS_RUN)
    monkeypatch.setattr(pdk_cell.Runner, "_exec",
                        lambda *a, **k: pytest.fail("publish must not run anything"))
    monkeypatch.setattr(pdk_cell, "container_running",
                        lambda: pytest.fail("publish must not need the container"))

    assert pdk_cell.main([CELL, "--only", "publish"]) == 1
    assert not any(cell.dir.glob(f"{CELL}_*.lib")), "a refused LOSS publishes nothing"
    assert "--accept-delay-loss" in capsys.readouterr().out

    assert pdk_cell.main([CELL, "--only", "publish", "--accept-delay-loss", "5"]) == 0
    assert sorted(p.name for p in cell.dir.glob(f"{CELL}_*.lib")) == sorted(CORNER_LIBS)
    assert "LOSS ACCEPTED" in capsys.readouterr().out


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


# ---------------------------------------------------------------------------
# Mining: an extension cell is a leaf
# ---------------------------------------------------------------------------

def test_the_miner_dictionary_marks_every_extension_cell_a_leaf():
    """Only the cells merge_lib.py spliced in; the PDK's own entries are untouched.

    With PDK_EXT on, step 2 mined AION_mux2i_0 (two AION_mux2i_1) and
    AION_mux2i_sg13g2_mux2_6, and step 4 could not characterize either. Kept a
    leaf, the mux is never folded into a pattern at all.
    """
    import merge_tech_dict

    base = {"cells": {"sg13g2_mux2_1": {"area": 20.0, "pins": {"A0": "input", "X": "output"},
                                        "function": "A0"}}}
    lib_text = (
        "library (x) {\n"
        "  cell (sg13g2_mux2_1) { area : 20.0; pin (X) { direction : output; function : \"A0\"; } }\n"
        "  cell (AION_mux2i_1) { area : 12.7; pin (I0) { direction : input; }\n"
        "    pin (O0) { direction : output; function : \"!I0\"; } }\n"
        "}\n")
    merged, added = merge_tech_dict.merge(base, lib_text)

    assert added == ["AION_mux2i_1"]
    assert merged["cells"]["AION_mux2i_1"]["mineable"] is False
    assert "mineable" not in merged["cells"]["sg13g2_mux2_1"], "PDK cells stay mineable"
    assert "mineable" not in base["cells"]["sg13g2_mux2_1"], "the base dictionary is not mutated"


def test_mine_exclude_marks_every_strength_of_a_matching_pdk_cell_a_leaf():
    """MINE_EXCLUDE's default keeps XOR and XNOR out of every mined pattern.

    Both drive strengths, because aion_opt folds them onto one key and reads
    whichever is smallest; and nothing else, `*xor*` notwithstanding.
    """
    import merge_tech_dict

    base = {"cells": {name: {"area": 1.0} for name in (
        "sg13g2_xor2_1", "sg13g2_xor2_2", "sg13g2_xnor2_1", "sg13g2_nor2_1")}}
    marked, matched = merge_tech_dict.exclude(base, ["*xor*", "*xnor*"])

    assert matched == ["sg13g2_xnor2_1", "sg13g2_xor2_1", "sg13g2_xor2_2"]
    assert all(marked["cells"][name]["mineable"] is False for name in matched)
    assert "mineable" not in marked["cells"]["sg13g2_nor2_1"]
    assert "mineable" not in base["cells"]["sg13g2_xor2_1"], "the base dictionary is not mutated"


def test_step2_and_the_sweep_write_the_same_bytes_and_name_a_typo(tmp_path):
    """aion_opt fingerprints a selection by the dictionary's content.

    scripts/explore_cells.py and step 2 each write their own copy; an
    `--emit` from the sweep is only a cache hit in step 3 if the copies are
    byte-equal. A glob that matches nothing is reported, not ignored.
    """
    import json

    import merge_tech_dict

    source = tmp_path / "base.json"
    source.write_text(json.dumps({"cells": {"sg13g2_xor2_1": {"area": 1.0},
                                            "sg13g2_nor2_1": {"area": 1.0}}}))
    patterns = merge_tech_dict.exclude_patterns(" *xor* , *xnro*,")
    assert patterns == ["*xor*", "*xnro*"]
    assert merge_tech_dict.exclude_patterns("") == []

    first, second = tmp_path / "s2" / "tech_dict.json", tmp_path / "sweep" / "tech_dict.json"
    matched, unmatched = merge_tech_dict.write_excluded(source, patterns, first)
    merge_tech_dict.write_excluded(source, patterns, second)

    assert matched == ["sg13g2_xor2_1"]
    assert unmatched == ["*xnro*"]
    assert first.read_bytes() == second.read_bytes()
