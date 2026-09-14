# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-14
#  Description:               Host tests for the scripts that hand cells to PnR
#
#  Run from the project root:  python3 -m pytest scripts/tests -q
# ================================================================

import sys
from pathlib import Path

# The scripts import each other as top-level modules, the way `make` runs them.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
