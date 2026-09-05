// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Created:                   2026-09-01
//  Description:               Wave dumping for the Icarus simulations.
// ================================================================
//
// Instantiated as a second root alongside tt_um_aion, so it can reach the
// DUT by name without wrapping it (cocotb drives tt_um_aion directly).
//
//   +dumpfile=<path>   where to write the FST   (default aion.fst, in
//                      this target's own run directory)
//   +dumpdepth=<n>     levels below the DUT     (default 0 = the whole tree)
//
// A gate-level netlist is ~12k cells; dumping all of it is slow and produces
// a huge trace, so the post-* targets pass +dumpdepth=1 for the ports only.

module dump_waves;
  reg [1023:0] dumpfile;
  integer      dumpdepth;

  initial begin
    if (!$value$plusargs("dumpfile=%s", dumpfile)) dumpfile = "aion.fst";
    if (!$value$plusargs("dumpdepth=%d", dumpdepth)) dumpdepth = 0;

    $dumpfile(dumpfile);
    $dumpvars(dumpdepth, tt_um_aion);
  end
endmodule
