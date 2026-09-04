-- ================================================================
--  SPDX-FileCopyrightText:    2026 Filippo Quadri
--  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
--  Created:                   2026-09-02
--  Description:               AION SoC - Register Interface Block
-- ================================================================

library ieee;
  use ieee.std_logic_1164.all;

library work;

entity aion_interface is
  port (
    clk     : in  std_ulogic;
    rst_n   : in  std_ulogic;
    ui_in   : in  std_ulogic_vector(7 downto 0);  -- address/control
    uio_in  : in  std_ulogic_vector(7 downto 0);  -- write data
    uo_out  : out std_ulogic_vector(7 downto 0);  -- read data
    opA     : out std_logic_vector(15 downto 0);
    opB     : out std_logic_vector(15 downto 0);
    opcode  : out std_logic_vector(3 downto 0);
    start   : out std_logic;
    result  : in  std_logic_vector(15 downto 0);
    done    : in  std_logic
  );
end entity aion_interface;

architecture arch of aion_interface is

  -- Byte-addressable register file
  signal reg_opA_lo   : std_ulogic_vector(7 downto 0);
  signal reg_opA_hi   : std_ulogic_vector(7 downto 0);
  signal reg_opB_lo   : std_ulogic_vector(7 downto 0);
  signal reg_opB_hi   : std_ulogic_vector(7 downto 0);
  signal reg_control  : std_ulogic_vector(7 downto 0);
  signal reg_status   : std_ulogic_vector(7 downto 0);

  -- Address and direction decoded from ui_in
  signal addr         : std_ulogic_vector(2 downto 0);
  signal write_en     : std_ulogic;
  signal read_en      : std_ulogic;

  -- Registered start pulse
  signal start_d      : std_ulogic;
  signal start_pulse  : std_ulogic;

begin

  -- ----------------------------------------------------------------
  -- Address and direction decoding
  -- ----------------------------------------------------------------
  addr     <= ui_in(2 downto 0);
  write_en <= ui_in(7);
  read_en  <= not ui_in(7);

  -- ----------------------------------------------------------------
  -- Map verbose internal signals to register file
  -- ----------------------------------------------------------------
  opA    <= std_logic_vector(reg_opA_hi) & std_logic_vector(reg_opA_lo);
  opB    <= std_logic_vector(reg_opB_hi) & std_logic_vector(reg_opB_lo);
  opcode <= std_logic_vector(reg_control(3 downto 0));
  start  <= std_logic(start_pulse);

  -- ----------------------------------------------------------------
  -- Start pulse generation
  -- A write to address 4 with bit 7 set generates a one-cycle pulse.
  -- Bits 3:0 of reg_control carry the ALU opcode.
  -- ----------------------------------------------------------------
  start_pulse <= '1' when (write_en = '1' and addr = "100" and uio_in(7) = '1') else '0';

  process (clk, rst_n) is
  begin

    if (rst_n = '0') then
      reg_opA_lo  <= (others => '0');
      reg_opA_hi  <= (others => '0');
      reg_opB_lo  <= (others => '0');
      reg_opB_hi  <= (others => '0');
      reg_control <= (others => '0');
      start_d     <= '0';
    elsif rising_edge(clk) then
      start_d <= start_pulse;

      if (write_en = '1') then
        case addr is
          when "000"  => reg_opA_lo  <= uio_in;
          when "001"  => reg_opA_hi  <= uio_in;
          when "010"  => reg_opB_lo  <= uio_in;
          when "011"  => reg_opB_hi  <= uio_in;
          when "100"  => reg_control <= uio_in;
          when others => null;
        end case;
      end if;
    end if;

  end process;

  -- ----------------------------------------------------------------
  -- Status register
  -- ----------------------------------------------------------------
  reg_status <= "0000000" & std_ulogic(done);

  -- ----------------------------------------------------------------
  -- Read data mux to dedicated outputs
  -- ----------------------------------------------------------------
  process (addr, reg_opA_lo, reg_opA_hi, reg_opB_lo, reg_opB_hi,
           reg_control, result, reg_status) is
  begin

    case addr is
      when "000"  => uo_out <= reg_opA_lo;
      when "001"  => uo_out <= reg_opA_hi;
      when "010"  => uo_out <= reg_opB_lo;
      when "011"  => uo_out <= reg_opB_hi;
      when "100"  => uo_out <= reg_control;
      when "101"  => uo_out <= std_ulogic_vector(result(7 downto 0));
      when "110"  => uo_out <= std_ulogic_vector(result(15 downto 8));
      when "111"  => uo_out <= reg_status;
      when others => uo_out <= (others => '0');
    end case;

  end process;

end architecture arch;
