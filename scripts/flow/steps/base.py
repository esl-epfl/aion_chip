# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The shape of one flow step
# ================================================================

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import paths
from ..coherence import BLOCKED, Coherence, OK
from ..config import Config
from ..runner import Runner, StepFailed


@dataclass
class Context:
    cfg: Config
    runner: Runner
    coherence: Coherence
    force: bool = False
    notes: list = field(default_factory=list)

    def note(self, message: str) -> None:
        self.notes.append(message)


class Step:
    """One numbered stage of the chip flow.

    Every step is runnable and re-runnable on its own: `run()` reads only
    what `inputs()` names, writes only under `outdir()` (plus, for step 6,
    implementation/cells/), and never assumes the previous step ran in the
    same process.
    """

    key: str = ""
    title: str = ""
    summary: str = ""
    upstream: tuple = ()
    #: Does this step need the EDA container to be up?
    needs_container: bool = False
    #: Set by the CLI for --dry-run, so a preview can walk past inputs that
    #: only exist once an earlier step has actually run.
    dry_run: bool = False

    # -----------------------------------------------------------------
    @property
    def outdir(self) -> Path:
        return paths.STEP_DIRS[self.key]

    @property
    def log_dir(self) -> Path:
        """Where the runner tees this step's command output.

        Deliberately NOT inside outdir. `make synth` and `make pnr` both end
        in _save_run, which does `rm -rf $(OUT_DIR)` on exactly the step
        directory -- so a log written there is unlinked while the runner is
        still writing to it, and the path the flow prints on failure names a
        file that no longer exists. flow/logs/<step>/ is outside every
        target's reach.
        """
        return paths.FLOW_DIR / "logs" / self.key

    def inputs(self, cfg: Config) -> list:
        """Files this step consumes, for the coherence record."""
        return []

    def outputs(self, cfg: Config) -> list:
        """Files this step produces, for the coherence record."""
        return []

    def run(self, ctx: Context) -> str:
        raise NotImplementedError

    # -----------------------------------------------------------------
    def require(self, *candidates: Path) -> None:
        """Fail with the step that would have produced a missing input."""
        if self.dry_run:
            return
        for path in candidates:
            if not Path(path).exists():
                owner = _owner_of(path)
                hint = f"  run `python flow.py {owner}` first" if owner else ""
                raise StepFailed(
                    f"missing input: {paths.rel_to_project(path)}\n{hint}", 2)

    def fresh(self, *subdirs: str) -> Path:
        """Recreate a subdirectory of the step's output tree, empty."""
        target = self.outdir.joinpath(*subdirs) if subdirs else self.outdir
        if self.dry_run:
            return target
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def ensure(self, *subdirs: str) -> Path:
        target = self.outdir.joinpath(*subdirs) if subdirs else self.outdir
        if not self.dry_run:
            target.mkdir(parents=True, exist_ok=True)
        return target


def _owner_of(path) -> Optional[str]:
    text = str(Path(path).resolve())
    for key, directory in paths.STEP_DIRS.items():
        if text.startswith(str(directory)):
            return key.split("_", 1)[0]
    return None


__all__ = ["Step", "Context", "StepFailed", "BLOCKED", "OK"]
