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
    clk     : in  std_logic;
    rst_n   : in  std_logic;
    ui_in   : in  std_ulogic_vector(7 downto 0);  -- address/control
    uio_in  : in  std_ulogic_vector(7 downto 0);  -- write data
    uo_out  : out std_ulogic_vector(7 downto 0);  -- read data
    result  : out std_logic_vector(15 downto 0);
    done    : out std_logic
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

  component aion_interface is
    port (
      clk     : in  std_ulogic;
      rst_n   : in  std_ulogic;
      ui_in   : in  std_ulogic_vector(7 downto 0);
      uio_in  : in  std_ulogic_vector(7 downto 0);
      uo_out  : out std_ulogic_vector(7 downto 0);
      opA     : out std_logic_vector(15 downto 0);
      opB     : out std_logic_vector(15 downto 0);
      opcode  : out std_logic;
      start   : out std_logic;
      result  : in  std_logic_vector(15 downto 0);
      done    : in  std_logic
    );
  end component aion_interface;

  signal opA    : std_logic_vector(15 downto 0);
  signal opB    : std_logic_vector(15 downto 0);
  signal opcode : std_logic;
  signal start  : std_logic;
  signal result_i : std_logic_vector(15 downto 0);
  signal done_i : std_logic;

begin

  result <= result_i;
  done   <= done_i;

  aion_interface_inst : component aion_interface
    port map (
      clk    => std_ulogic(clk),
      rst_n  => std_ulogic(rst_n),
      ui_in  => ui_in,
      uio_in => uio_in,
      uo_out => uo_out,
      opA    => opA,
      opB    => opB,
      opcode => opcode,
      start  => start,
      result => result_i,
      done   => done_i
    );

  posit_alu_inst : component posit_alu
    port map (
      clk    => clk,
      rst_n  => rst_n,
      opA    => opA,
      opB    => opB,
      opcode => opcode,
      start  => start,
      result => result_i,
      done   => done_i
    );

end architecture arch;


