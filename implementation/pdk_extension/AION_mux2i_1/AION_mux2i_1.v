// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Description:               Gold gate-level view of AION_mux2i_1
//
//  This is the *reference*, not the implementation.  aion_char builds the
//  gold truth table from the PDK Liberty functions of the cells instantiated
//  here and assembles reference_AION_mux2i_1 from their transistor views;
//  the transmission-gate implementation in AION_mux2i_1.spice is then
//  proved against it, vector by vector.
//
//  The cell is an INVERTING 2:1 multiplexer:
//
//      O0 = !I0 when I2 = 0
//      O0 = !I1 when I2 = 1
//
//  which is why the reference is a mux2 followed by an inverter.  The
//  inversion is not a design choice made for its own sake: a transmission
//  gate steers its input straight through, so the restoring stage that makes
//  the cell modelable is necessarily an inverter, and a second one to undo
//  the polarity would cost two more CoreSites and buy nothing electrical.
//  The technology mapper is happy to use an inverting mux where the logic
//  already needs one; the PDK has no such cell.
//
//  Port names follow the AION convention (I0.. inputs, O0.. outputs) that
//  aion_char and aion_layout both key on:
//
//      I0 = A0   I1 = A1   I2 = S   O0 = !X
// ================================================================

module AION_mux2i_1 ( I0, I1, I2, O0);

  input I0;
  wire I0;
  input I1;
  wire I1;
  input I2;
  wire I2;
  output O0;
  wire O0;

  wire w0;


  sg13g2_mux2_1 g0 (
    .A0(I0),    .A1(I1),    .S(I2),    .X(w0)  );
  sg13g2_inv_1 g1 (
    .A(w0),    .Y(O0)  );

endmodule
