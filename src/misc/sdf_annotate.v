// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Created:                   2026-09-04
//  Description:               SDF back-annotation for the post-PnR run.
// ================================================================
//
// Instantiated as a second root alongside tt_um_aion, so it can name the DUT
// without wrapping it (cocotb drives tt_um_aion directly).
//
//   +sdf_file=<path>   SDF emitted by the PnR flow, e.g.
//                      flow/pnr_simple/sdf/nom_typ_1p20V_25C/tt_um_aion__*.sdf
//
// Icarus only. Verilator prints "Ignoring unsupported: $sdf_annotate" and
// carries on at zero delay, so a Verilator run is never a timing check.
//
// Two things have to line up for this to actually annotate anything:
//   * the cells must be the `specify`-carrying models (epfl:aion:tech_timing)
//   * iverilog must be given -gspecify, or it parses specify blocks and
//     discards them
// The min:typ:max triplet is picked by iverilog's -T flag, not from here.
//
// What this buys you is delays, not a timing check. Icarus annotates the SDF's
// IOPATH and INTERCONNECT records and discards its TIMINGCHECK records --
// "SDF WARNING: ... TIMINGCHECK not supported", one per flop -- and implements
// no timing checks in the first place: elaborating the cell library prints
// "Timing checks are not supported" for every $setuphold/$recrem/$width in it.
// So the `notifier` reg never toggles and the ihp_dff_r UDP row that would
// drive Q to x on a violation is unreachable. Setup and hold are STA's job.

module sdf_annotate;
  reg [1023:0] sdf_file;

  initial begin
    if (!$value$plusargs("sdf_file=%s", sdf_file)) begin
      $display("FATAL: post-PnR simulation started without +sdf_file=<path>.");
      $display("       Without an SDF this run has no cell or wire delays");
      $display("       at all. Pass SDF=<path> to make.");
      $finish;
    end
    $sdf_annotate(sdf_file, tt_um_aion);
  end
endmodule
