# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-08-19
#  Description:               Resolve a FuseSoC core's RTL source files,
#                             including dependencies, and emit them in the
#                             order a simulator/synthesis tool would consume.
# ================================================================

import argparse
import json
import logging
import os
import sys

from fusesoc.config import Config
from fusesoc.main import Fusesoc
from fusesoc.vlnv import Vlnv

# File types that represent synthesizable RTL sources.
RTL_FILE_TYPES = {
    "verilogSource",
    "systemVerilogSource",
    "vhdlSource",
}


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def get_rtl_sources(core_manager, core_name: str, target: str):
    """Return an ordered list of absolute RTL file paths for a core.

    Dependencies are resolved first (leaf-to-root), then the core's own
    filesets. Include files are collected separately because they must not be
    passed as standalone compilation units.
    """
    core = core_manager.get_core(Vlnv(core_name))
    flags = {"target": target}

    resolved = core_manager.get_depends(core.name, flags)
    # `get_depends` returns leaf-first; the top-level core is the last element.
    # If the core has no dependencies, `resolved` is empty and we only use `core`.
    cores = resolved if resolved else [core]

    rtl_files = []
    include_files = []

    for c in cores:
        core_flags = flags.copy()
        core_flags["is_toplevel"] = c.name == core.name
        files_root = c.files_root

        for f in c.get_files(core_flags):
            file_type = f.get("file_type", "")
            if file_type not in RTL_FILE_TYPES:
                continue

            abs_path = os.path.normpath(os.path.join(files_root, f["name"]))
            entry = {"path": abs_path, "core": str(c.name), "type": file_type}

            if f.get("is_include_file"):
                include_files.append(entry)
            else:
                rtl_files.append(entry)

    return {
        "toplevel": core.get_toplevel(flags),
        "rtl_files": rtl_files,
        "include_files": include_files,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract ordered RTL source files from a FuseSoC core."
    )
    parser.add_argument(
        "--core",
        required=True,
        help="VLNV of the FuseSoC core to resolve (e.g. epfl:aion:pm32:1.0.0).",
    )
    parser.add_argument(
        "--target",
        default="librelane",
        help="Target to query for filesets (default: librelane).",
    )
    parser.add_argument(
        "--config",
        default="fusesoc.conf",
        help="Path to the FuseSoC configuration file (default: fusesoc.conf).",
    )
    parser.add_argument(
        "--build-root",
        default=".build",
        help="FuseSoC build root (default: .build).",
    )
    parser.add_argument(
        "--format",
        choices=["json", "list"],
        default="json",
        help="Output format (default: json).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)

    if not os.path.isfile(args.config):
        print(f"Error: FuseSoC config not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    config = Config(path=args.config, create_if_missing=False)
    config.args_build_root = args.build_root

    fs = Fusesoc(config)

    try:
        result = get_rtl_sources(fs.cm, args.core, args.target)
    except Exception as e:
        print(f"Error: failed to resolve sources for {args.core}: {e}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        for entry in result["rtl_files"]:
            print(entry["path"])


if __name__ == "__main__":
    main()
