-- ================================================================
--  SPDX-FileCopyrightText:    2026 Filippo Quadri
--  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
--  Created:                   2026-09-02
--  Description:               AION SoC - TinyTapeout Top-Level Wrapper
-- ================================================================

library ieee;
  use ieee.std_logic_1164.all;

library work;

entity tt_um_aion is
  port (
    clk     : in  std_ulogic;
    rst_n   : in  std_ulogic;
    ui_in   : in  std_ulogic_vector(7 downto 0);  -- Dedicated inputs (address/control)
    uo_out  : out std_ulogic_vector(7 downto 0);  -- Dedicated outputs (read data/status)
    uio_in  : in  std_ulogic_vector(7 downto 0);  -- IOs: Input path (write data)
    uio_out : out std_ulogic_vector(7 downto 0);  -- IOs: Output path (unused)
    uio_oe  : out std_ulogic_vector(7 downto 0)   -- IOs: Enable path (active high: 0=input, 1=output)
  );
end entity tt_um_aion;

architecture arch of tt_um_aion is

  component aion_soc is
    port (
      clk     : in  std_logic;
      rst_n   : in  std_logic;
      ui_in   : in  std_ulogic_vector(7 downto 0);
      uio_in  : in  std_ulogic_vector(7 downto 0);
      uo_out  : out std_ulogic_vector(7 downto 0);
      result  : out std_logic_vector(15 downto 0);
      done    : out std_logic
    );
  end component aion_soc;

begin

  -- ----------------------------------------------------------------
  -- Bidirectional IOs are statically configured as inputs.
  -- All write data comes from uio_in; uio_out is unused.
  -- ----------------------------------------------------------------
  uio_oe  <= (others => '0');
  uio_out <= (others => '0');

  -- ----------------------------------------------------------------
  -- AION SoC instance (contains register interface + compute core)
  -- ----------------------------------------------------------------
  aion_soc_inst : component aion_soc
    port map (
      clk    => std_logic(clk),
      rst_n  => std_logic(rst_n),
      ui_in  => ui_in,
      uio_in => uio_in,
      uo_out => uo_out,
      result => open,
      done   => open
    );

end architecture arch;
