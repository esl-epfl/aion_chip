# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Make an extracted layout verifiable in SPICE
#
#  `aion-char-verify-spice` is the only exhaustive functional proof in this
#  flow that runs on a real transistor netlist: it stimulates the Liberty
#  gold model, the PDK-cell reference and a candidate netlist on the same
#  vectors and compares all three.  Pointing it at a PEX netlist closes the
#  last gap in step 6 -- LVS proves the layout matches the schematic, and
#  this proves the schematic still computes the cell's function once the
#  extracted parasitics are in the loop.
#
#  Two things stand in the way, and both are mechanical:
#
#  1. Port ORDER.  Magic lists the ports of an extracted `.subckt` in its own
#     order.  aion_char requires exactly `<inputs in Verilog header order>
#     <outputs in header order> VDD VSS` and refuses anything else.
#  2. Port NAME.  The checked subckt's name must equal the Verilog module
#     name, and the extracted one already uses it.
#
#  So: rename the extracted subckt out of the way, then wrap it in one that
#  has the right name and the right order and binds the two by NAME.  The
#  result is self-contained -- no .include, no path that has to mean the
#  same thing on the host and in the container.
# ================================================================

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

from . import paths

_SUBCKT = re.compile(r"^\s*\.subckt\s+(\S+)(.*)$", re.IGNORECASE)
_ENDS = re.compile(r"^\s*\.ends\b(.*)$", re.IGNORECASE)


class PexError(RuntimeError):
    pass


def module_ports(cells_v: Path, cell: str) -> list:
    """The port order aion_char demands, from aion_char's own parser.

    Reusing tb_generator here rather than re-deriving the rule is the point:
    if the contract ever changes, the wrapper changes with it.
    """
    tool = paths.AION_FLOW / "tools" / "aion_char"
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    try:
        from aion_char.tb_generator import parse_netlist, spice_ports
    except ImportError as exc:                       # pragma: no cover
        raise PexError(f"cannot import aion_char.tb_generator: {exc}") from exc

    for module in parse_netlist(Path(cells_v)):
        if module.name == cell:
            return list(spice_ports(module))
    raise PexError(f"{cells_v} defines no module {cell!r}")


def read_subckt_ports(spice: Path, cell: str) -> list:
    """The ports of `.subckt <cell> ...`, in the file's own order."""
    text = Path(spice).read_text()
    for line in _continued(text):
        match = _SUBCKT.match(line)
        if match and match.group(1).lower() == cell.lower():
            return match.group(2).split()
    raise PexError(f"{spice} has no '.subckt {cell}'")


def write_wrapper(pex: Path, cell: str, cells_v: Path, out: Path) -> Path:
    """Wrap `pex` so that `.subckt <cell>` has aion_char's port order.

    Returns `out`.  Raises if the two port sets disagree by name, which
    would mean the extraction and the Verilog model describe different
    cells -- a real finding, not something to paper over.
    """
    wanted = module_ports(cells_v, cell)
    actual = read_subckt_ports(pex, cell)

    if {p.upper() for p in wanted} != {p.upper() for p in actual}:
        raise PexError(
            f"{cell}: the extracted netlist and the Verilog model do not "
            f"agree on the port NAMES, so no reordering can reconcile them.\n"
            f"  extracted: {' '.join(actual)}\n"
            f"  expected : {' '.join(wanted)}"
        )

    inner = f"{cell}_pex"
    body = _rename_subckt(Path(pex).read_text(), cell, inner)

    # Bind by name: for each port of the inner subckt, in ITS order, pass the
    # wrapper port that carries the same name.
    by_upper = {p.upper(): p for p in wanted}
    binding = " ".join(by_upper[p.upper()] for p in actual)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"* AION flow: port-ordered wrapper around the extracted layout of {cell}\n"
        f"* source: {paths.rel_to_project(pex)}\n"
        f"* the extracted subckt is renamed {inner} and bound by port name;\n"
        f"* {cell} below carries the order aion_char requires:\n"
        f"*   {' '.join(wanted)}\n"
        f"\n{body.rstrip()}\n\n"
        f".subckt {cell} {' '.join(wanted)}\n"
        f"X0 {binding} {inner}\n"
        f".ends {cell}\n"
    )
    return out


# ---------------------------------------------------------------------
def _rename_subckt(text: str, old: str, new: str) -> str:
    """Rename one top-level `.subckt`, its `.ends`, and any instantiation."""
    out = []
    depth = 0
    renamed = False
    for line in text.splitlines():
        match = _SUBCKT.match(line)
        if match:
            if depth == 0 and match.group(1).lower() == old.lower():
                line = re.sub(r"(?i)^(\s*\.subckt\s+)\S+", rf"\g<1>{new}", line, count=1)
                renamed = True
            depth += 1
            out.append(line)
            continue
        end = _ENDS.match(line)
        if end:
            depth -= 1
            if depth == 0 and renamed and end.group(1).strip().lower() == old.lower():
                line = re.sub(r"(?i)^(\s*\.ends\s+)\S+", rf"\g<1>{new}", line, count=1)
            out.append(line)
            continue
        out.append(line)
    if not renamed:
        raise PexError(f"no top-level '.subckt {old}' to rename")
    return "\n".join(out)


def _continued(text: str):
    """SPICE line continuations: a leading '+' extends the previous line."""
    buffer: Optional[str] = None
    for raw in text.splitlines():
        if raw.lstrip().startswith("+") and buffer is not None:
            buffer += " " + raw.lstrip()[1:]
            continue
        if buffer is not None:
            yield buffer
        buffer = raw
    if buffer is not None:
        yield buffer
