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
    opA     : out std_logic_vector(31 downto 0);
    opB     : out std_logic_vector(31 downto 0);
    opcode  : out std_logic_vector(3 downto 0);
    start   : out std_logic;
    result  : in  std_logic_vector(31 downto 0);
    done    : in  std_logic
  );
end entity aion_interface;

architecture arch of aion_interface is

  -- Byte-addressable register file. A Posit<32,2> operand is four bytes, so
  -- the map needs fourteen addresses where the Posit<16,2> one needed eight,
  -- and the address field grows from ui_in(2:0) to ui_in(3:0). ui_in(6:4) is
  -- now unread; ui_in(7) is still the direction bit.
  signal reg_opA      : std_ulogic_vector(31 downto 0);
  signal reg_opB      : std_ulogic_vector(31 downto 0);
  signal reg_control  : std_ulogic_vector(7 downto 0);
  signal reg_status   : std_ulogic_vector(7 downto 0);

  -- Address and direction decoded from ui_in
  signal addr         : std_ulogic_vector(3 downto 0);
  signal write_en     : std_ulogic;

  -- Registered start pulse
  signal start_pulse  : std_ulogic;

begin

  -- ----------------------------------------------------------------
  -- Address and direction decoding
  -- ----------------------------------------------------------------
  addr     <= ui_in(3 downto 0);
  write_en <= ui_in(7);

  -- ----------------------------------------------------------------
  -- Map verbose internal signals to register file
  -- ----------------------------------------------------------------
  opA    <= std_logic_vector(reg_opA);
  opB    <= std_logic_vector(reg_opB);
  opcode <= std_logic_vector(reg_control(3 downto 0));
  start  <= std_logic(start_pulse);

  -- ----------------------------------------------------------------
  -- Start pulse generation
  -- A write to address 8 with bit 7 set generates a one-cycle pulse.
  -- Bits 3:0 of reg_control carry the ALU opcode; bits 6:4 are unused.
  -- ----------------------------------------------------------------
  start_pulse <= '1' when (write_en = '1' and addr = "1000" and uio_in(7) = '1') else '0';

  process (clk, rst_n) is
  begin

    if (rst_n = '0') then
      reg_opA     <= (others => '0');
      reg_opB     <= (others => '0');
      reg_control <= (others => '0');
    elsif rising_edge(clk) then
      if (write_en = '1') then
        case addr is
          when "0000" => reg_opA(7 downto 0)   <= uio_in;
          when "0001" => reg_opA(15 downto 8)  <= uio_in;
          when "0010" => reg_opA(23 downto 16) <= uio_in;
          when "0011" => reg_opA(31 downto 24) <= uio_in;
          when "0100" => reg_opB(7 downto 0)   <= uio_in;
          when "0101" => reg_opB(15 downto 8)  <= uio_in;
          when "0110" => reg_opB(23 downto 16) <= uio_in;
          when "0111" => reg_opB(31 downto 24) <= uio_in;
          when "1000" => reg_control           <= uio_in;
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
  process (addr, reg_opA, reg_opB, reg_control, result, reg_status) is
  begin

    case addr is
      when "0000" => uo_out <= reg_opA(7 downto 0);
      when "0001" => uo_out <= reg_opA(15 downto 8);
      when "0010" => uo_out <= reg_opA(23 downto 16);
      when "0011" => uo_out <= reg_opA(31 downto 24);
      when "0100" => uo_out <= reg_opB(7 downto 0);
      when "0101" => uo_out <= reg_opB(15 downto 8);
      when "0110" => uo_out <= reg_opB(23 downto 16);
      when "0111" => uo_out <= reg_opB(31 downto 24);
      when "1000" => uo_out <= reg_control;
      when "1001" => uo_out <= std_ulogic_vector(result(7 downto 0));
      when "1010" => uo_out <= std_ulogic_vector(result(15 downto 8));
      when "1011" => uo_out <= std_ulogic_vector(result(23 downto 16));
      when "1100" => uo_out <= std_ulogic_vector(result(31 downto 24));
      when "1101" => uo_out <= reg_status;
      when others => uo_out <= (others => '0');
    end case;

  end process;

end architecture arch;
