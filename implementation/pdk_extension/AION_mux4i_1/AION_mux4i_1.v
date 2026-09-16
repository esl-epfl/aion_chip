// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Description:               Gold gate-level view of AION_mux4i_1
//
//  This is the *reference*, not the implementation.  aion_char builds the
//  gold truth table from the PDK Liberty functions of the cells instantiated
//  here and assembles reference_AION_mux4i_1 from their transistor views;
//  the transmission-gate implementation in AION_mux4i_1.spice is then proved
//  against it, vector by vector.
//
//  The cell is an INVERTING 4:1 multiplexer, AION_mux2i_1 one level deeper:
//
//      O0 = !I0 when I5 = 0, I4 = 0
//      O0 = !I1 when I5 = 0, I4 = 1
//      O0 = !I2 when I5 = 1, I4 = 0
//      O0 = !I3 when I5 = 1, I4 = 1
//
//  so the reference is a mux4 followed by an inverter, 21 + 3 = 24 sites.
//  The PDK has no inverting mux at any width, which is what the cell
//  competes with.
//
//  Port names follow the AION convention (I0.. inputs, O0.. outputs) that
//  aion_char and aion_layout both key on:
//
//      I0 = A0   I1 = A1   I2 = A2   I3 = A3   I4 = S0   I5 = S1   O0 = !X
// ================================================================

module AION_mux4i_1 ( I0, I1, I2, I3, I4, I5, O0);

  input I0;
  wire I0;
  input I1;
  wire I1;
  input I2;
  wire I2;
  input I3;
  wire I3;
  input I4;
  wire I4;
  input I5;
  wire I5;
  output O0;
  wire O0;

  wire w0;


  sg13g2_mux4_1 g0 (
    .A0(I0),    .A1(I1),    .A2(I2),    .A3(I3),    .S0(I4),    .S1(I5),    .X(w0)  );
  sg13g2_inv_1 g1 (
    .A(w0),    .Y(O0)  );

endmodule
