// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Created:                   2026-09-01
//  Description:               Dump vars to FST for the AION SoC.
// ================================================================

module dump_waves;
  initial begin
    $dumpfile("../../aion.fst");
    $dumpvars(0, tt_um_aion);
  end
endmodule
