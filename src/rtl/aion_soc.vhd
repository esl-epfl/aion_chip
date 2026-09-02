-- ================================================================
--  SPDX-FileCopyrightText:    2026 Filippo Quadri
--  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
--  Created:                   2026-09-01
--  Description:               AION SoC - Top Level Posit Arithmetic Unit
-- ================================================================

library ieee;
  use ieee.std_logic_1164.all;

library work;

entity aion_soc is
  port (
    clk    : in  std_logic;
    rst_n  : in  std_logic;
    opA    : in  std_logic_vector(15 downto 0);
    opB    : in  std_logic_vector(15 downto 0);
    opcode : in  std_logic;                     -- '0' = add, '1' = mult
    start  : in  std_logic;
    result : out std_logic_vector(15 downto 0);
    done   : out std_logic
  );
end entity aion_soc;

architecture arch of aion_soc is

  component posit_alu is
    port (
      clk    : in  std_logic;
      rst_n  : in  std_logic;
      opA    : in  std_logic_vector(15 downto 0);
      opB    : in  std_logic_vector(15 downto 0);
      opcode : in  std_logic;
      start  : in  std_logic;
      result : out std_logic_vector(15 downto 0);
      done   : out std_logic
    );
  end component posit_alu;

begin

  posit_alu_inst : component posit_alu
    port map (
      clk    => clk,
      rst_n  => rst_n,
      opA    => opA,
      opB    => opB,
      opcode => opcode,
      start  => start,
      result => result,
      done   => done
    );

end architecture arch;


