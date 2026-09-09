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
    ena     : in  std_ulogic;                     -- High while the tile is selected and powered
    ui_in   : in  std_ulogic_vector(7 downto 0);  -- Dedicated inputs (address/control)
    uo_out  : out std_ulogic_vector(7 downto 0);  -- Dedicated outputs (read data/status)
    uio_in  : in  std_ulogic_vector(7 downto 0);  -- IOs: Input path (write data)
    uio_out : out std_ulogic_vector(7 downto 0);  -- IOs: Output path (unused)
    uio_oe  : out std_ulogic_vector(7 downto 0)   -- IOs: Enable path (active high: 0=input, 1=output)
  );
end entity tt_um_aion;

architecture arch of tt_um_aion is

  -- Every input the harness drives but this design does not read, collected in
  -- one place. The ports are not optional -- tt-support-tools' check_ports
  -- rejects a tt_um_* module that is missing any of them -- so the choice is
  -- between a port with no reader and a port whose only reader says, here,
  -- that dropping it was deliberate.
  signal unused : std_ulogic;

  component aion_soc is
    port (
      clk     : in  std_logic;
      rst_n   : in  std_logic;
      ui_in   : in  std_ulogic_vector(7 downto 0);
      uio_in  : in  std_ulogic_vector(7 downto 0);
      uo_out  : out std_ulogic_vector(7 downto 0);
      result  : out std_logic_vector(31 downto 0);
      done    : out std_logic
    );
  end component aion_soc;

begin

  -- ----------------------------------------------------------------
  -- `ena` goes high when the multiplexer selects this tile and stays high for
  -- as long as it is powered. AION gates nothing on it: the register file is
  -- reset by rst_n and driven by ui_in, and a tile that is not selected sees
  -- no clock edges worth acting on. It is read here and nowhere else.
  -- ----------------------------------------------------------------
  unused <= ena;

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
