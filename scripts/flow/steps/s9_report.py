# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               9_report -- was any of this worth it?
#
#  Eight steps mine cells, prove them, draw them, harden a chip out of them
#  and take its picture.  None of them ever answers the question the flow
#  exists to ask: is the chip built from AI-generated cells better than the
#  same chip built from the PDK's, and by how much.
#
#  The comparison needs a second chip.  `make pnr_simple` hardens the same
#  RTL through the same config with the PDK standard cells only, and that is
#  the control: same die, same SDC, same synthesis strategy, so the cell
#  substitution is the only variable and the deltas are attributable to it.
#  This step runs it when it is missing and reuses it when it is not.
#
#  The second half of the report is harsher and more useful.  Step 3 predicts
#  an area saving from AREA_FACTOR -- a guess made before a single cell was
#  drawn -- and step 6 then draws them, at whatever size they actually came
#  out.  Those two numbers disagree, and a flow that reports only the
#  prediction is a flow that reports its own assumptions back to itself.
# ================================================================

from __future__ import annotations

import json

from .. import paths
from ..config import Config, flag
from ..runner import container_running
from ..style import Style, color, info, note, ok, warn
from .base import Context, Step, StepFailed

#: `make pnr_simple` saves here. Not a step directory: the baseline is not a
#: step -- nothing downstream consumes it and it is not part of the chain that
#: builds the chip. It is the control this one step measures against.
BASELINE_DIR = paths.FLOW_DIR / "pnr_simple"

REWRITE_REPORT = (paths.STEP_DIRS["3_rewrite"] / "report" / "rewrite_report.json")

#: How a verdict is coloured in the terminal.
VERDICT_STYLE = {
    "win": (Style.GREEN, Style.BOLD),
    "loss": (Style.RED, Style.BOLD),
    "tie": (Style.DIM,),
    "clean": (Style.GREEN,),
    "fail": (Style.RED, Style.BOLD),
    "info": (Style.DIM,),
    "n/a": (Style.DIM,),
}


class ReportStep(Step):
    key = "9_report"
    title = "Report"
    summary = "compare the AION chip against the PDK-only baseline and say what it bought"
    upstream = ("3_rewrite", "6_layout_drawing", "7_pnr")
    # The baseline may have to be hardened, which needs the container -- but
    # only then. Declaring it here would demand a running container for every
    # re-report of a baseline that already exists, which is the common case,
    # so the step checks for itself at the point it actually needs one.
    needs_container = False

    def inputs(self, cfg: Config) -> list:
        return [paths.STEP_DIRS["7_pnr"] / "metrics.json",
                BASELINE_DIR / "metrics.json",
                REWRITE_REPORT,
                paths.CELLS_DIR]

    def outputs(self, cfg: Config) -> list:
        return [self.outdir / "report.md", self.outdir / "report.json",
                self.outdir / "report.html"]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg = ctx.cfg
        aion_metrics = paths.STEP_DIRS["7_pnr"] / "metrics.json"
        self.require(aion_metrics)

        self._ensure_baseline(ctx)
        self.ensure()
        if self.dry_run:
            info(f"would compare {paths.rel_to_project(aion_metrics)} against "
                 f"{paths.rel_to_project(BASELINE_DIR)}/metrics.json")
            return "ok"

        try:
            from compare_runs import compare, render_markdown
        except ImportError as exc:
            raise StepFailed(f"cannot import the comparison: {exc}", 2) from exc

        try:
            record = compare(paths.STEP_DIRS["7_pnr"], BASELINE_DIR,
                             REWRITE_REPORT, paths.CELLS_DIR,
                             prefix=cfg.CELL_PREFIX)
        except Exception as exc:            # noqa: BLE001 - reported as a failure
            raise StepFailed(f"comparison failed: {exc}", 2) from exc

        markdown = self.outdir / "report.md"
        markdown.write_text(render_markdown(record))
        (self.outdir / "report.json").write_text(json.dumps(record, indent=2) + "\n")
        page = self._write_page(record, cfg.TOP)

        self._report(ctx, record, page)
        return "ok"

    # -----------------------------------------------------------------
    def _write_page(self, record: dict, top: str):
        """The same record as a page you can look at.

        Never allowed to fail the step: the report is report.md and report.json,
        and a host that cannot render the page still has both.
        """
        try:
            from report_html import render_html
        except ImportError as exc:
            warn(f"no HTML page — {exc}")
            return None
        try:
            page = self.outdir / "report.html"
            page.write_text(
                render_html(record, images=self._pictures(top)))
        except Exception as exc:        # noqa: BLE001 - see the docstring
            warn(f"could not render report.html — {exc}")
            return None
        return page

    def _pictures(self, top: str) -> list:
        """Step 8's renders, linked relative so the page works off the disk."""
        render_dir = paths.STEP_DIRS["8_render"]
        wanted = [(f"{top}.chip.png", "The hardened die, every layer, with the "
                                      "AION cells ringed"),
                  (f"{top}.aion.png", "Where the AION cells landed")]
        found = []
        for name, caption in wanted:
            if (render_dir / name).is_file():
                found.append((caption, f"../{render_dir.name}/{name}"))
        return found

    # -----------------------------------------------------------------
    def _ensure_baseline(self, ctx: Context) -> None:
        """Harden the PDK-only control, unless one is already on disk."""
        metrics = BASELINE_DIR / "metrics.json"
        if metrics.is_file() and not ctx.cfg.REPORT_REBUILD_BASELINE:
            info(f"baseline: reusing {paths.rel_to_project(BASELINE_DIR)}")
            return
        if self.dry_run:
            info("would run `make pnr_simple` for the baseline")
            return

        if not ctx.cfg.REPORT_RUN_BASELINE:
            raise StepFailed(
                f"no PDK-only baseline at {paths.rel_to_project(metrics)}, and "
                "REPORT_RUN_BASELINE is off\n"
                "  run `make pnr_simple` yourself, or re-run with "
                "`--set REPORT_RUN_BASELINE=true`", 2)
        if not container_running():
            raise StepFailed(
                f"no PDK-only baseline at {paths.rel_to_project(metrics)}, and "
                f"the EDA container {paths.CONTAINER_NAME} is not running to "
                "build one\n  start iic-osic-tools, or run `make pnr_simple` "
                "on a machine that has it", 4)

        warn("no PDK-only baseline on disk — hardening one now "
             "(`make pnr_simple`, ~10 min)")
        ctx.runner.check(
            ctx.runner.host("pnr_simple", [*flag("LENIENT", ctx.cfg.LENIENT)],
                            name="pnr_simple"),
            "harden the PDK-only baseline",
        )
        self.require(metrics)
        ctx.note("hardened the PDK-only baseline")

    # -----------------------------------------------------------------
    def _report(self, ctx: Context, record: dict, page=None) -> None:
        if record["comparable"]:
            warn("the two runs are NOT configured the same way — every delta "
                 "below mixes the substitution with a config change:")
            for problem in record["comparable"]:
                note(f"  {problem}")

        info(record["headline"] and _verdict_line(record))
        print()
        _table(record["headline"]["rows"])
        print()

        totals = record.get("ledger_totals")
        if totals and totals.get("actual_saving") is not None:
            info("predicted vs delivered, on the substituted sites only")
            note(f"  predicted {totals['predicted_saving']:>9,.1f} um²  "
                 f"({totals['predicted_saving_pct']:.1f}% of the area replaced)")
            note(f"  delivered {totals['actual_saving']:>9,.1f} um²  "
                 f"({totals['actual_saving_pct']:.1f}%)")
            if totals["predicted_saving"] > 0:
                ratio = totals["actual_saving"] / totals["predicted_saving"]
                (ok if ratio >= 0.95 else warn)(
                    f"the drawn cells delivered {100 * ratio:.0f}% of what "
                    f"AREA_FACTOR={totals.get('area_factor')} promised")
        elif totals:
            warn("no AION cell has been published, so there is nothing to "
                 "compare against the prediction")

        names = "report.md, report.json" + (", report.html" if page else "")
        ok(f"{paths.rel_to_project(self.outdir)}/ — {names}")
        head = record["headline"]
        ctx.note(f"vs baseline: {head['wins']} win, {head['losses']} loss, "
                 f"{head['ties']} tie")


def _verdict_line(record: dict) -> str:
    head = record["headline"]
    return (f"{head['wins']} win / {head['losses']} loss / {head['ties']} tie "
            f"across area, setup slack, power and wirelength")


def _table(rows: list) -> None:
    """The headline comparison, aligned, one line per metric."""
    print(color(f"    {'metric':<26}{'AION':>14}{'baseline':>14}"
                f"{'delta':>16}   ", Style.BOLD))
    for row in rows:
        digits = 0 if row["unit"] in ("", "um") else 3
        unit = f" {row['unit']}" if row["unit"] else ""
        delta = ("—" if row["absolute"] is None
                 else f"{row['absolute']:+,.{digits}f}"
                 + ("" if row["percent"] is None else f" ({row['percent']:+.2f}%)"))
        line = (f"    {row['label'] + unit:<26}"
                f"{_cell(row['aion'], digits):>14}"
                f"{_cell(row['baseline'], digits):>14}"
                f"{delta:>16}   ")
        print(line + color(row["verdict"], *VERDICT_STYLE[row["verdict"]]))


def _cell(value, digits: int) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


STEP = ReportStep()
