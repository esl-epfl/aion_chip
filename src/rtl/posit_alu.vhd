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
    opcode : in  std_logic;                     -- '0' = add, '1' = mult
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

  signal add_result : std_logic_vector(15 downto 0);
  signal mul_result : std_logic_vector(15 downto 0);
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

  result <= mul_result when opcode = '1' else
            add_result;

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
