// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Created:                   2026-08-19 11:36:04
//  Updated:                   2026-08-19 21:52:18
//  Description:               xxx
// ================================================================

`timescale 1ns / 1ps

module top (
    input  wire        clk,
    input  wire        rst,
    input  wire        start,
    input  wire [31:0] mc,
    input  wire [31:0] mp,
    output reg  [63:0] p,
    output wire        done
);
  pm32 pm32 (
      .clk  (clk),
      .rst  (rst),
      .start(start),
      .mc   (mc),
      .mp   (mp),
      .p    (p),
      .done (done)
  );
endmodule
