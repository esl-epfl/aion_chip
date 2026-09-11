# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Host <-> container path algebra for the flow
#
#  There are two namespaces in this flow and mixing them is the single
#  easiest way to get a silently wrong run:
#
#    host       /home/<user>/phd/aion/aion_chip/flow/1_synth/nl/x.v
#    container  /foss/designs/aion_chip/flow/1_synth/nl/x.v
#
#  Steps that run on the host (aion_opt mining, the rewriter, the SPICE
#  splitter, LibreLane's make wrappers) take HOST paths.  Steps that run in
#  the EDA container (aion_char, kepler-formal, the minimizer's SPICE
#  verification) take CONTAINER paths.  `container()` is the only conversion,
#  and it refuses anything that is not under the project root, because
#  nothing else is reachable from inside the container under that name.
#
#  Why the project root and not the submodule: the container mounts
#  aion_chip at /foss/designs/aion_chip and, separately, a DIFFERENT
#  standalone aion_flow checkout at /foss/designs/aion_flow.  The submodule
#  we actually drive is only reachable as /foss/designs/aion_chip/aion_flow.
#  aion_flow's own scripts/docker_run.sh hardcodes the wrong one, so every
#  containerised step here goes through aion_chip's runner instead — see
#  runner.py.
# ================================================================

from __future__ import annotations

from pathlib import Path

# scripts/flow/paths.py -> scripts/flow -> scripts -> <project root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]

AION_FLOW = PROJECT_ROOT / "aion_flow"
FLOW_DIR = PROJECT_ROOT / "flow"
IMPL_DIR = PROJECT_ROOT / "implementation"
CELLS_DIR = IMPL_DIR / "cells"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DOCKER_RUN = SCRIPTS_DIR / "docker_run.sh"

LAYOUT_TOOL = AION_FLOW / "tools" / "aion_layout_claude"
LAYOUT_CELLS = LAYOUT_TOOL / "cells"

# The hand-designed cells, and the extended standard-cell Liberty `make
# pdk_ext_lib` splices out of them.  The technology mapper reads that file
# (CELL_LIBS), so a netlist synthesized with PDK_EXT on can instantiate a cell
# the plain PDK Liberty has never heard of -- which is why everything that
# links that netlist afterwards has to read this one instead.
PDK_EXT_DIR = IMPL_DIR / "pdk_extension"
PDK_EXT_LIB_DIR = FLOW_DIR / "pdk_extension" / "lib"
PDK_EXT_DICT_DIR = FLOW_DIR / "pdk_extension" / "tech_dict"

# The corner the extension cells are characterized at.  kepler-formal links a
# netlist against a single Liberty and only reads the cells' functions out of
# it, so which corner it is does not change the verdict -- but a file that is
# not on disk does.  Keep it one of the Makefile's PDK_EXT_CORNERS.
PDK_EXT_CORNER = "typ_1p20V_25C"

# The mount this project has inside iic-osic-tools, and the name the
# aion_flow submodule answers to there.
CONTAINER_ROOT = "/foss/designs/aion_chip"
CONTAINER_AION_FLOW = f"{CONTAINER_ROOT}/aion_flow"

CONTAINER_NAME = "iic-osic-tools_shell_uid_1000"

# One directory per step, all under flow/.
STEP_DIRS = {
    "1_synth": FLOW_DIR / "1_synth",
    "2_pattern_extraction": FLOW_DIR / "2_pattern_extraction",
    "3_rewrite": FLOW_DIR / "3_rewrite",
    "4_characterization": FLOW_DIR / "4_characterization",
    "5_gate_minimization": FLOW_DIR / "5_gate_minimization",
    "6_layout_drawing": FLOW_DIR / "6_layout_drawing",
    "7_pnr": FLOW_DIR / "7_pnr",
    "8_render": FLOW_DIR / "8_render",
    "9_report": FLOW_DIR / "9_report",
}

COHERENCE_FILE = FLOW_DIR / "coherence.json"


class PathError(RuntimeError):
    """A path cannot be expressed in the namespace it is needed in."""


def container(path) -> str:
    """Map a host path to its name inside the EDA container.

    Refuses anything outside PROJECT_ROOT: only that subtree is mounted at
    a name this flow can rely on, and a silently-unmapped path is how you
    end up reading the other aion_flow checkout.
    """
    resolved = Path(path).resolve()
    try:
        rel = resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise PathError(
            f"{resolved} lies outside {PROJECT_ROOT}, which is the only tree "
            f"this flow can name inside the container. Keep flow inputs and "
            f"outputs under the project."
        ) from exc
    return f"{CONTAINER_ROOT}/{rel}" if str(rel) != "." else CONTAINER_ROOT


def host(path) -> Path:
    """Map a container path back to the host. Inverse of `container()`."""
    text = str(path)
    if text == CONTAINER_ROOT:
        return PROJECT_ROOT
    prefix = CONTAINER_ROOT + "/"
    if not text.startswith(prefix):
        raise PathError(f"{text} is not under {CONTAINER_ROOT}")
    return PROJECT_ROOT / text[len(prefix):]


def pdk_ext_cells() -> list:
    """Cell names under implementation/pdk_extension/, one directory each.

    The same rule as the Makefile's PDK_EXT_CELLS, and for the same reason:
    no cells means there is nothing to splice into the Liberty, so PDK_EXT is
    on but has changed nothing.
    """
    if not PDK_EXT_DIR.is_dir():
        return []
    return sorted(child.name for child in PDK_EXT_DIR.iterdir()
                  if child.is_dir() and child.name != "views")


def pdk_ext_lib(corner: str = PDK_EXT_CORNER) -> Path:
    """The extended Liberty for one corner. `make pdk_ext_lib` writes it."""
    return PDK_EXT_LIB_DIR / f"sg13g2_stdcell_aion_{corner}.lib"


def pdk_ext_dict() -> Path:
    """The extended technology dictionary aion_opt reads.

    One file, not one per corner: aion_opt takes area and Boolean function
    out of it and has no timing in it to vary.
    """
    return PDK_EXT_DICT_DIR / "sg13g2_stdcell_aion.json"


def rel_to_project(path) -> str:
    """Project-relative spelling, for logs and reports."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)
