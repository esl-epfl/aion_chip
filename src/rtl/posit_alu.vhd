-- ================================================================
--  SPDX-FileCopyrightText:    2026 Filippo Quadri
--  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
--  Created:                   2026-09-01
--  Description:               Posit ALU - Add/Multiply wrapper
-- ================================================================

library ieee;
  use ieee.std_logic_1164.all;

library work;

entity posit_alu is
  port (
    clk    : in  std_logic;
    rst_n  : in  std_logic;
    opA    : in  std_logic_vector(15 downto 0);
    opB    : in  std_logic_vector(15 downto 0);
    opcode : in  std_logic_vector(3 downto 0);  -- 0000=add, 0001=mult, 0010-0110=cmp, 0111-1001=bitwise
    start  : in  std_logic;
    result : out std_logic_vector(15 downto 0);
    done   : out std_logic
  );
end entity posit_alu;

architecture arch of posit_alu is

  component positadder is
    port (
      clk : in  std_logic;
      x   : in  std_logic_vector(15 downto 0);
      y   : in  std_logic_vector(15 downto 0);
      r   : out std_logic_vector(15 downto 0)
    );
  end component positadder;

  component positmult is
    port (
      clk : in  std_logic;
      x   : in  std_logic_vector(15 downto 0);
      y   : in  std_logic_vector(15 downto 0);
      r   : out std_logic_vector(15 downto 0)
    );
  end component positmult;

  component posit_compare is
    port (
      x      : in  std_logic_vector(15 downto 0);
      y      : in  std_logic_vector(15 downto 0);
      op     : in  std_logic;
      result : out std_logic_vector(15 downto 0)
    );
  end component posit_compare;

  component posit_bitwise is
    port (
      x      : in  std_logic_vector(15 downto 0);
      y      : in  std_logic_vector(15 downto 0);
      op     : in  std_logic_vector(1 downto 0);
      result : out std_logic_vector(15 downto 0)
    );
  end component posit_bitwise;

  signal add_result : std_logic_vector(15 downto 0);
  signal mul_result : std_logic_vector(15 downto 0);
  signal cmp_result : std_logic_vector(15 downto 0);
  signal bit_result : std_logic_vector(15 downto 0);
  signal start_d    : std_logic;

begin

  add_inst : component positadder
    port map (
      clk => clk,
      x   => opA,
      y   => opB,
      r   => add_result
    );

  mul_inst : component positmult
    port map (
      clk => clk,
      x   => opA,
      y   => opB,
      r   => mul_result
    );

  cmp_inst : component posit_compare
    port map (
      x      => opA,
      y      => opB,
      op     => opcode(0),
      result => cmp_result
    );

  bit_inst : component posit_bitwise
    port map (
      x      => opA,
      y      => opB,
      op     => opcode(1 downto 0),
      result => bit_result
    );

  with opcode select
    result <= add_result when "0000",
              mul_result when "0001",
              cmp_result when "0010" | "0011",
              bit_result when "0100" | "0101" | "0110",
              (others => '0') when others;

  process (clk, rst_n)
  begin
    if rst_n = '0' then
      start_d <= '0';
      done    <= '0';
    elsif rising_edge(clk) then
      start_d <= start;
      done    <= start_d;
    end if;
  end process;

end architecture arch;
