// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Description:               Gold gate-level view of AION_maj3i_1
//
//  This is the *reference*, not the implementation.  aion_char builds the
//  gold truth table from the PDK Liberty functions of the cells instantiated
//  here and assembles reference_AION_maj3i_1 from their transistor views;
//  the implementation in AION_maj3i_1.spice is then proved against it,
//  vector by vector.
//
//  The cell is an INVERTING 3-input majority -- the complemented carry of a
//  full adder:
//
//      O0 = !((I0 * I1) + (I0 * I2) + (I1 * I2))
//
//  The PDK has no majority cell.  The cheapest pair of PDK cells that builds
//  it is an OR2 feeding an AOI22, 6 + 5 = 11 sites, since
//
//      I0*I1 + I2*(I0 + I1)  ==  I0*I1 + I0*I2 + I1*I2
//
//  and that pair, abutted, is the baseline the drawn cell is measured
//  against.
// ================================================================

module AION_maj3i_1 ( I0, I1, I2, O0);

  input I0;
  wire I0;
  input I1;
  wire I1;
  input I2;
  wire I2;
  output O0;
  wire O0;

  wire w0;


  sg13g2_or2_1 g0 (
    .A(I0),    .B(I1),    .X(w0)  );
  sg13g2_a22oi_1 g1 (
    .A1(I0),    .A2(I1),    .B1(I2),    .B2(w0),    .Y(O0)  );

endmodule
