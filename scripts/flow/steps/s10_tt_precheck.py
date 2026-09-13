# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               10_tt_precheck -- would TinyTapeout take
#                             this chip as it is?
#
#  Nine steps build the AION chip and measure it.  None of them asks whether
#  it can be submitted.  TinyTapeout takes a GDS it did not build through its
#  custom_gds action, which routes nothing and checks everything: the
#  precheck compares the die and every pin against the tile's template DEF,
#  runs the PDK's KLayout deck with the recommended rules LibreLane leaves
#  out, and rejects a single shape on a forbidden layer.  What it does not do
#  is LVS or STA -- and LibreLane's checkers do not stop a LENIENT run -- so
#  the run's own signoff metrics are read back as well.
#
#  `make tt_precheck` does both (scripts/tt_precheck.py).  This step points
#  it at flow/7_pnr and keeps the package here: the three files custom_gds
#  takes, TT's reports in precheck/, and the verdict in tt_precheck.json.
#  Those three files are what the submission repository commits, and only
#  from a green run of this step.
#
#  The PDK-only baseline is not the chip this flow builds.  `make
#  tt_precheck` on its own checks flow/pnr_simple instead, packaged in place.
# ================================================================

from __future__ import annotations

import json
from typing import Optional

from .. import paths
from ..config import Config
from ..style import info, note, ok, warn
from .base import Context, Step, StepFailed

PNR_DIR = paths.STEP_DIRS["7_pnr"]


class TTPrecheckStep(Step):
    key = "10_tt_precheck"
    title = "TinyTapeout precheck"
    summary = "package the chip as TinyTapeout takes it and check it is submittable"
    upstream = ("7_pnr",)
    needs_container = True          # TT's precheck runs klayout on the PDK deck

    # -----------------------------------------------------------------
    def inputs(self, cfg: Config) -> list:
        """The views the package is copied from, and the signoff metrics."""
        return [PNR_DIR / "gds" / f"{cfg.TOP}.gds",
                PNR_DIR / "lef" / f"{cfg.TOP}.lef",
                PNR_DIR / "nl" / f"{cfg.TOP}.nl.v",
                PNR_DIR / "metrics.json"]

    def outputs(self, cfg: Config) -> list:
        return [self.outdir / f"{cfg.TOP}.gds",
                self.outdir / f"{cfg.TOP}.lef",
                self.outdir / f"{cfg.TOP}.v",
                self.outdir / "tt_precheck.json"]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, run = ctx.cfg, ctx.runner
        self.require(*self.inputs(cfg))

        # All or nothing: a GDS from this run beside a verdict from the last
        # one would read as a pass it never earned.
        self.fresh()
        result = run.host("tt_precheck", [
            f"TT_RUN_DIR={PNR_DIR}",
            f"TT_OUT_DIR={self.outdir}",
        ], name="tt_precheck")
        if self.dry_run:
            return "ok"

        verdict = self._verdict()
        if verdict is not None:
            self._report(ctx, verdict)
        run.check(result, "TinyTapeout precheck")
        if verdict is None:
            raise StepFailed(
                f"`make tt_precheck` succeeded but wrote no "
                f"{paths.rel_to_project(self.outdir)}/tt_precheck.json", 2)

        ok(f"{paths.rel_to_project(self.outdir)}/ — {cfg.TOP}.gds, "
           f"{cfg.TOP}.lef and {cfg.TOP}.v, as custom_gds takes them")
        return "ok"

    # -----------------------------------------------------------------
    def _verdict(self) -> Optional[dict]:
        record = self.outdir / "tt_precheck.json"
        try:
            return json.loads(record.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def _report(self, ctx: Context, verdict: dict) -> None:
        signoff = verdict.get("signoff", [])
        precheck = verdict.get("precheck", [])
        dirty = [row for row in signoff if not row["clean"]]
        failed = [row for row in precheck if not row["passed"]]

        print()
        info(f"signoff: {len(signoff) - len(dirty)}/{len(signoff)} clean   "
             f"TT precheck: {len(precheck) - len(failed)}/{len(precheck)} passed")
        for row in dirty:
            value = "not reported" if row["value"] is None else row["value"]
            warn(f"  {row['label']}: {value}")
        for row in failed:
            warn(f"  {row['check']}: {row['error']}")
        for row in verdict.get("advisory", []):
            if not row["clean"]:
                value = "not reported" if row["value"] is None else row["value"]
                note(f"  {row['label']}: {value} (advisory — not a reason "
                     "to reject, but worth a look)")

        if verdict.get("submittable"):
            ok("submittable: the signoff is clean and TT's precheck passes")
        ctx.note(f"signoff {len(signoff) - len(dirty)}/{len(signoff)}, "
                 f"precheck {len(precheck) - len(failed)}/{len(precheck)}")


STEP = TTPrecheckStep()
