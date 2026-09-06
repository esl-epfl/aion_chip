// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Description:               Placeholder for the drawn AION cell models
// ================================================================
//
// This file declares nothing, on purpose.
//
// `epfl:aion:flow_cells:1.0.0` (flow/aion_cells.core) is the handle
// post_pnr_sim_ai uses to find the Verilog models of the AION cells that flow
// step 6 drew. FuseSoC refuses a fileset with no files, so before any cell has
// been drawn the core points here.
//
// `./flow.py 6` rewrites aion_cells.core to list the real models, one per
// published cell. Until then a post-PnR AI simulation stops at
// `Unknown module type: AION_...`, which is the honest answer: there are no
// drawn cells to model yet.
