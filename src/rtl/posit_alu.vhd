-- ================================================================
--  SPDX-FileCopyrightText:    2026 Filippo Quadri
--  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
--  Created:                   2026-09-01
--  Description:               Posit ALU - add/multiply/compare/bitwise,
--                             built on a MAC array
-- ================================================================

library ieee;
  use ieee.std_logic_1164.all;

library work;

entity posit_alu is
  generic (
    -- Lanes in the MAC array. 1 is the smallest legal value and reproduces
    -- the pre-MAC design: lane 0 carries the only adder and multiplier in the
    -- chip, and the plain add and multiply opcodes bypass into it. Every lane
    -- past the first costs about 28,900 um2, so this is the knob that sizes
    -- the die -- see implementation/README.md.
    MAC_LANES : positive := 1
  );
  port (
    clk    : in  std_logic;
    rst_n  : in  std_logic;
    opA    : in  std_logic_vector(15 downto 0);
    opB    : in  std_logic_vector(15 downto 0);
    opcode : in  std_logic_vector(3 downto 0);  -- see the table below
    lane   : in  std_logic_vector(2 downto 0);  -- MAC lane, from reg_control(6:4)
    start  : in  std_logic;
    result : out std_logic_vector(15 downto 0);
    done   : out std_logic
  );
end entity posit_alu;

architecture arch of posit_alu is

  -- The opcode map, in one place. 0000-0110 are unchanged; 1000-1011 are the
  -- MAC additions, taken from what reg_control(3 downto 0) left free.
  constant OP_ADD       : std_logic_vector(3 downto 0) := "0000";
  constant OP_MULT      : std_logic_vector(3 downto 0) := "0001";
  constant OP_EQ        : std_logic_vector(3 downto 0) := "0010";
  constant OP_LT        : std_logic_vector(3 downto 0) := "0011";
  constant OP_AND       : std_logic_vector(3 downto 0) := "0100";
  constant OP_OR        : std_logic_vector(3 downto 0) := "0101";
  constant OP_XOR       : std_logic_vector(3 downto 0) := "0110";
  constant OP_MAC_LOAD  : std_logic_vector(3 downto 0) := "1000";
  constant OP_MAC_RUN   : std_logic_vector(3 downto 0) := "1001";
  constant OP_MAC_CLEAR : std_logic_vector(3 downto 0) := "1010";
  constant OP_MAC_READ  : std_logic_vector(3 downto 0) := "1011";

  component posit_mac_array is
    generic (
      LANES : positive
    );
    port (
      clk    : in  std_logic;
      rst_n  : in  std_logic;
      bypass : in  std_logic;
      sel    : in  std_logic_vector(2 downto 0);
      bus_x  : in  std_logic_vector(15 downto 0);
      bus_y  : in  std_logic_vector(15 downto 0);
      load   : in  std_logic;
      run    : in  std_logic;
      clear  : in  std_logic;
      prod0  : out std_logic_vector(15 downto 0);
      sum0   : out std_logic_vector(15 downto 0);
      acc    : out std_logic_vector(15 downto 0)
    );
  end component posit_mac_array;

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
  signal mac_result : std_logic_vector(15 downto 0);
  signal start_d1   : std_logic;
  signal start_d2   : std_logic;
  signal is_mac     : std_logic;

  -- The arithmetic opcodes borrow lane 0's units instead of owning a second
  -- adder and multiplier; the MAC opcodes let the lanes use their own.
  signal mac_bypass : std_logic;
  signal mac_load   : std_logic;
  signal mac_run    : std_logic;
  signal mac_clear  : std_logic;

begin

  mac_bypass <= '1' when (opcode = OP_ADD or opcode = OP_MULT) else '0';

  is_mac <= '1' when (opcode = OP_MAC_LOAD or opcode = OP_MAC_RUN or
                      opcode = OP_MAC_CLEAR or opcode = OP_MAC_READ) else '0';

  -- Gated on the *registered* pulse, not on `start`. `start` is combinational
  -- off ui_in/uio_in, so it is high during the very write that sets
  -- reg_control -- at which point `opcode` is still the previous command, and
  -- a MAC pulse decoded from it fires under the wrong opcode. One cycle later
  -- the two agree. (This is why the plain ALU never needed them aligned: it is
  -- combinational and is only ever read after `done`.)
  mac_load   <= start_d1 when (opcode = OP_MAC_LOAD)  else '0';
  mac_run    <= start_d1 when (opcode = OP_MAC_RUN)   else '0';
  mac_clear  <= start_d1 when (opcode = OP_MAC_CLEAR) else '0';

  mac_inst : component posit_mac_array
    generic map (
      LANES => MAC_LANES
    )
    port map (
      clk    => clk,
      rst_n  => rst_n,
      bypass => mac_bypass,
      sel    => lane,
      bus_x  => opA,
      bus_y  => opB,
      load   => mac_load,
      run    => mac_run,
      clear  => mac_clear,
      prod0  => mul_result,
      sum0   => add_result,
      acc    => mac_result
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

  -- Every MAC opcode reads back the selected lane's accumulator, so LOAD, RUN
  -- and CLEAR all leave it on `result` and a separate read opcode is only
  -- needed when you want to look without disturbing anything.
  with opcode select
    result <= add_result when OP_ADD,
              mul_result when OP_MULT,
              cmp_result when OP_EQ | OP_LT,
              bit_result when OP_AND | OP_OR | OP_XOR,
              mac_result when OP_MAC_LOAD | OP_MAC_RUN | OP_MAC_CLEAR | OP_MAC_READ,
              (others => '0') when others;

  -- `done` says the result is readable, so it has to wait for whichever result
  -- is slower. The combinational opcodes are ready two edges after the write,
  -- as they always were. A MAC accumulate needs three: one for the pulse to
  -- line up with its opcode, one to register the product, one to add it in.
  process (clk, rst_n)
  begin
    if rst_n = '0' then
      start_d1 <= '0';
      start_d2 <= '0';
      done     <= '0';
    elsif rising_edge(clk) then
      start_d1 <= start;
      start_d2 <= start_d1;

      if is_mac = '1' then
        done <= start_d2;
      else
        done <= start_d1;
      end if;
    end if;
  end process;

end architecture arch;
