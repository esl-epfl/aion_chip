-- ================================================================
--  SPDX-FileCopyrightText:    2026 Filippo Quadri
--  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
--  Created:                   2026-09-03
--  Description:               Posit Comparator - EQ/LT/LE/GT/GE
-- ================================================================

library ieee;
  use ieee.std_logic_1164.all;
  use ieee.numeric_std.all;

library work;

entity posit_compare is
  port (
    x      : in  std_logic_vector(15 downto 0);
    y      : in  std_logic_vector(15 downto 0);
    op     : in  std_logic;                     -- '0'=EQ, '1'=LT
    result : out std_logic_vector(15 downto 0)
  );
end entity posit_compare;

architecture arch of posit_compare is

  signal eq  : std_logic;
  signal lt  : std_logic;
  signal res : std_logic;

begin

  -- Posit comparison uses two's-complement signed ordering.
  -- A posit value is larger when its signed integer interpretation is larger.
  eq <= '1' when signed(x) = signed(y) else '0';
  lt <= '1' when signed(x) < signed(y) else '0';

  res <= eq when op = '0' else lt;

  result <= (0 => res, others => '0');

end architecture arch;
