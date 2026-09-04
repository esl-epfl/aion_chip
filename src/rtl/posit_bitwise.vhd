-- ================================================================
--  SPDX-FileCopyrightText:    2026 Filippo Quadri
--  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
--  Created:                   2026-09-03
--  Description:               Posit Bitwise - AND/OR/XOR
-- ================================================================

library ieee;
  use ieee.std_logic_1164.all;

library work;

entity posit_bitwise is
  port (
    x      : in  std_logic_vector(15 downto 0);
    y      : in  std_logic_vector(15 downto 0);
    op     : in  std_logic_vector(1 downto 0);  -- 00=AND, 01=OR, 10=XOR
    result : out std_logic_vector(15 downto 0)
  );
end entity posit_bitwise;

architecture arch of posit_bitwise is
begin

  with op select
    result <= (x and y) when "00",
              (x or y)  when "01",
              (x xor y) when "10",
              (others => '0') when others;

end architecture arch;
