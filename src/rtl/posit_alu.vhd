-- ================================================================
--  SPDX-FileCopyrightText:    2026 Filippo Quadri
--  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
--  Created:                   2026-09-01
--  Description:               Posit ALU - add/multiply/compare/bitwise
-- ================================================================

library ieee;
  use ieee.std_logic_1164.all;

library work;

entity posit_alu is
  port (
    clk    : in  std_logic;
    rst_n  : in  std_logic;
    opA    : in  std_logic_vector(31 downto 0);
    opB    : in  std_logic_vector(31 downto 0);
    opcode : in  std_logic_vector(3 downto 0);  -- see the table below
    start  : in  std_logic;
    result : out std_logic_vector(31 downto 0);
    done   : out std_logic
  );
end entity posit_alu;

architecture arch of posit_alu is

  -- The opcode map, in one place.
  constant OP_ADD  : std_logic_vector(3 downto 0) := "0000";
  constant OP_MULT : std_logic_vector(3 downto 0) := "0001";
  constant OP_EQ   : std_logic_vector(3 downto 0) := "0010";
  constant OP_LT   : std_logic_vector(3 downto 0) := "0011";
  constant OP_AND  : std_logic_vector(3 downto 0) := "0100";
  constant OP_OR   : std_logic_vector(3 downto 0) := "0101";
  constant OP_XOR  : std_logic_vector(3 downto 0) := "0110";

  component PositAdder is
    port (
      clk : in  std_logic;
      X   : in  std_logic_vector(31 downto 0);
      Y   : in  std_logic_vector(31 downto 0);
      R   : out std_logic_vector(31 downto 0)
    );
  end component PositAdder;

  component PositMult is
    port (
      clk : in  std_logic;
      X   : in  std_logic_vector(31 downto 0);
      Y   : in  std_logic_vector(31 downto 0);
      R   : out std_logic_vector(31 downto 0)
    );
  end component PositMult;

  component posit_compare is
    port (
      x      : in  std_logic_vector(31 downto 0);
      y      : in  std_logic_vector(31 downto 0);
      op     : in  std_logic;
      result : out std_logic_vector(31 downto 0)
    );
  end component posit_compare;

  component posit_bitwise is
    port (
      x      : in  std_logic_vector(31 downto 0);
      y      : in  std_logic_vector(31 downto 0);
      op     : in  std_logic_vector(1 downto 0);
      result : out std_logic_vector(31 downto 0)
    );
  end component posit_bitwise;

  -- Clock edges between the edge that latches `control` and the edge on which
  -- `result` is guaranteed settled.
  --
  -- `PositAdder` and `PositMult` are one pipeline stage deep. That stage fills
  -- on the `control` edge itself -- the operand registers cannot change on it,
  -- since only one register is written per edge, so they have been stable for
  -- a full cycle by then. What is left afterwards is the combinational tail of
  -- the arithmetic (normalizer, encoder) and the result multiplexer, which
  -- needs the rest of that cycle. So the answer is stable one edge later.
  --
  -- `posit_compare` and `posit_bitwise` are wholly combinational and settle
  -- sooner; the same edge covers them. Re-pipelining either FloPoCo unit means
  -- raising this to match.
  constant ALU_LATENCY : positive := 1;

  signal add_result : std_logic_vector(31 downto 0);
  signal mul_result : std_logic_vector(31 downto 0);
  signal cmp_result : std_logic_vector(31 downto 0);
  signal bit_result : std_logic_vector(31 downto 0);

  signal busy    : std_logic;
  signal elapsed : natural range 0 to ALU_LATENCY;

begin

  -- The Posit<32,2> adder and multiplier are one pipeline stage deep, unlike
  -- the Posit<16,2> pair they replace: FloPoCo puts a register in the adder's
  -- normalizer and in the multiplier's encoder. They are fed straight from the
  -- operand registers and run unconditionally, so that stage fills on the same
  -- edge that latches `control` -- one edge before `done` -- and the delay is
  -- hidden inside the acknowledgement that was already there.
  add_inst : component PositAdder
    port map (
      clk => clk,
      X   => opA,
      Y   => opB,
      R   => add_result
    );

  mul_inst : component PositMult
    port map (
      clk => clk,
      X   => opA,
      Y   => opB,
      R   => mul_result
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
    result <= add_result when OP_ADD,
              mul_result when OP_MULT,
              cmp_result when OP_EQ | OP_LT,
              bit_result when OP_AND | OP_OR | OP_XOR,
              (others => '0') when others;

  -- `done` reports completion, and it is a level rather than a pulse: it falls
  -- on the edge that accepts a new command and rises on the edge where that
  -- command's result is settled, then holds until the next command.
  --
  -- Both halves of that matter. A pulse can be missed -- software that polls
  -- `status` one cycle late would never see it and would wait forever. And a
  -- flag that is not cleared when the command is accepted reads back as 1 from
  -- the *previous* operation, so software polling straight after a write can
  -- be told the new result is ready before the arithmetic has run.
  --
  -- Reset leaves it low: nothing has been computed yet.
  process (clk, rst_n)
  begin
    if rst_n = '0' then
      busy    <= '0';
      elapsed <= 0;
      done    <= '0';
    elsif rising_edge(clk) then
      if start = '1' then
        -- The edge that writes `control` in aion_interface. `opcode` becomes
        -- the new command here, and the arithmetic's pipeline stage captures
        -- the operand registers on this same edge.
        busy    <= '1';
        done    <= '0';
        elapsed <= ALU_LATENCY;
      elsif busy = '1' then
        if elapsed <= 1 then
          busy <= '0';
          done <= '1';
        else
          elapsed <= elapsed - 1;
        end if;
      end if;
    end if;
  end process;

end architecture arch;
