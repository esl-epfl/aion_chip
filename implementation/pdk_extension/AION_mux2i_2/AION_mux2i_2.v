// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Description:               Gold gate-level view of AION_mux2i_2
//
//  This is the *reference*, not the implementation.  aion_char builds the
//  gold truth table from the PDK Liberty functions of the cells instantiated
//  here and assembles reference_AION_mux2i_2 from their transistor views;
//  the transmission-gate implementation in AION_mux2i_2.spice is then
//  proved against it, vector by vector.
//
//  The cell is an INVERTING 2:1 multiplexer at drive 2:
//
//      O0 = !I0 when I2 = 0
//      O0 = !I1 when I2 = 1
//
//  Why mux2_1 + inv_2 and not mux2_2 + inv_2.  This netlist is two things at
//  once: the function aion_char proves against, and -- once its transistors
//  are abutted -- the area and delay baseline post-layout measures this cell
//  against.  So it has to be what the mapper would actually build if
//  AION_mux2i_2 did not exist, which is the cheapest PDK pair whose *output*
//  drive is X2.  That is mux2_1 driving inv_2: 10 + 4 = 14 CoreSites.
//  mux2_2 + inv_2 is 15 sites and buys only a faster internal node, and
//  mux2_2 + inv_1 is not this cell at all -- its output drive is X1.
//
//  The pairing is also the honest comparison, because it makes the same
//  trade this cell makes: mux2_1's own output inverter drives twice the gate
//  it was sized for, exactly as our transmission gates now charge node m
//  into a doubled inverter.  Both sides pay it.
//
//  Port names follow the AION convention (I0.. inputs, O0.. outputs) that
//  aion_char and aion_layout both key on:
//
//      I0 = A0   I1 = A1   I2 = S   O0 = !X
// ================================================================

module AION_mux2i_2 ( I0, I1, I2, O0);

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
  sg13g2_inv_2 g1 (
    .A(w0),    .Y(O0)  );

endmodule
