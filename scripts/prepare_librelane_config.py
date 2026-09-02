# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-02
#  Description:               Prepare a LibreLane configuration file
#                             for a VHDL design.
# ================================================================

import argparse
import json
import os
import sys


# Flow that stops after synthesis for Verilog designs.
SYNTH_ONLY_FLOW = [
    "Verilator.Lint",
    "Checker.LintTimingConstructs",
    "Checker.LintErrors",
    "Checker.LintWarnings",
    "Yosys.JsonHeader",
    "Yosys.Synthesis",
    "Checker.YosysUnmappedCells",
    "Checker.YosysSynthChecks",
    "Checker.NetlistAssignStatements",
]

# Flow that stops after synthesis for VHDL designs.
VHDL_SYNTH_ONLY_FLOW = [
    "Yosys.VHDLSynthesis",
    "Checker.YosysUnmappedCells",
    "Checker.YosysSynthChecks",
    "Checker.NetlistAssignStatements",
]


def make_relative(path: str, base: str) -> str:
    """Return a path relative to base, using LibreLane's dir:: prefix."""
    abs_path = os.path.abspath(path)
    abs_base = os.path.abspath(base)
    rel = os.path.relpath(abs_path, abs_base)
    return f"dir::{rel}"


def main():
    parser = argparse.ArgumentParser(
        description="Generate a LibreLane config.json for a VHDL design."
    )
    parser.add_argument(
        "--src-config",
        required=True,
        help="Path to the source implementation/config.json template.",
    )
    parser.add_argument(
        "--dst-config",
        required=True,
        help="Path where the generated LibreLane config.json will be written.",
    )
    parser.add_argument(
        "--ip-dir",
        required=True,
        help="Directory used as the base for dir:: relative paths.",
    )
    parser.add_argument(
        "--pin-order",
        default=None,
        help="Path to the pin order configuration file (optional).",
    )
    parser.add_argument(
        "--vhdl-files",
        required=True,
        help="Comma-separated list of VHDL source file paths.",
    )
    parser.add_argument(
        "--synth-only",
        action="store_true",
        help="If set, stop the flow after synthesis.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.src_config):
        print(f"Error: source config not found: {args.src_config}", file=sys.stderr)
        sys.exit(1)

    with open(args.src_config, "r") as f:
        config = json.load(f)

    # Ensure the VHDLClassic flow is selected.
    config.setdefault("meta", {})
    config["meta"]["flow"] = "VHDLClassic"

    # Convert absolute VHDL paths to dir:: paths relative to the IP directory.
    vhdl_files = [p.strip() for p in args.vhdl_files.split(",") if p.strip()]
    config["VHDL_FILES"] = [make_relative(p, args.ip_dir) for p in vhdl_files]

    # Remove Verilog-specific keys that are not used by the VHDLClassic flow.
    for key in (
        "VERILOG_FILES",
        "VERILOG_INCLUDE_DIRS",
        "USE_SLANG",
        "SLANG_ARGUMENTS",
    ):
        config.pop(key, None)

    if args.pin_order is not None:
        config["PIN_ORDER_CONFIG"] = make_relative(args.pin_order, args.ip_dir)
    else:
        config.pop("PIN_ORDER_CONFIG", None)

    if args.synth_only:
        config["meta"]["flow"] = VHDL_SYNTH_ONLY_FLOW

    os.makedirs(os.path.dirname(args.dst_config), exist_ok=True)
    with open(args.dst_config, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
