#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               AION chip flow -- entry point
#
#  The handler lives in scripts/flow/; everything it produces lives in
#  flow/.  See `python flow.py --help`.
# ================================================================

import sys
from pathlib import Path

# Line-buffer our own output. A flow run is long and usually watched through a
# pipe, a log file or `tee`, and Python block-buffers stdout there -- which
# makes a running step look like a hung one.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(line_buffering=True)
    except (AttributeError, OSError):      # not a real stream (tests, pipes)
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from flow.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
