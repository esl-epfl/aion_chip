-- ================================================================
--  SPDX-FileCopyrightText:    2026 Filippo Quadri
--  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
--  Created:                   2026-09-01
--  Description:               Posit ALU - add/multiply/compare/bitwise,
--                             at Posit<32,2> or Posit<16,2>
-- ================================================================

library ieee;
  use ieee.std_logic_1164.all;

library work;

entity posit_alu is
  port (
    clk       : in  std_logic;
    rst_n     : in  std_logic;
    opA       : in  std_logic_vector(31 downto 0);
    opB       : in  std_logic_vector(31 downto 0);
    opcode    : in  std_logic_vector(3 downto 0);  -- see the table below
    precision : in  std_logic;                     -- see PREC_P32 / PREC_P16
    start     : in  std_logic;
    result    : out std_logic_vector(31 downto 0);
    done      : out std_logic
  );
end entity posit_alu;

architecture arch of posit_alu is

  -- The opcode map, in one place. `precision` is orthogonal to it: every
  -- opcode means the same operation at either width, which is why the width
  -- is a separate bit rather than a second half of the opcode table.
  constant OP_ADD  : std_logic_vector(3 downto 0) := "0000";
  constant OP_MULT : std_logic_vector(3 downto 0) := "0001";
  constant OP_EQ   : std_logic_vector(3 downto 0) := "0010";
  constant OP_LT   : std_logic_vector(3 downto 0) := "0011";
  constant OP_AND  : std_logic_vector(3 downto 0) := "0100";
  constant OP_OR   : std_logic_vector(3 downto 0) := "0101";
  constant OP_XOR  : std_logic_vector(3 downto 0) := "0110";

  -- Operand width. At PREC_P16 only bits 15:0 of `opA`/`opB` are a number;
  -- the upper half of the operand registers is whatever the last Posit<32,2>
  -- command left there, and every block below is fed accordingly.
  constant PREC_P32 : std_logic := '0';
  constant PREC_P16 : std_logic := '1';

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

  component PositAdder16 is
    port (
      clk : in  std_logic;
      X   : in  std_logic_vector(15 downto 0);
      Y   : in  std_logic_vector(15 downto 0);
      R   : out std_logic_vector(15 downto 0)
    );
  end component PositAdder16;

  component PositMult16 is
    port (
      clk : in  std_logic;
      X   : in  std_logic_vector(15 downto 0);
      Y   : in  std_logic_vector(15 downto 0);
      R   : out std_logic_vector(15 downto 0)
    );
  end component PositMult16;

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
  -- `PositAdder16`, `PositMult16`, `posit_compare` and `posit_bitwise` are
  -- wholly combinational and settle sooner; the same edge covers them. The
  -- narrow pair therefore costs no extra latency and none is charged for it:
  -- one handshake serves both widths. Re-pipelining any FloPoCo unit means
  -- raising this to match the deepest one.
  constant ALU_LATENCY : positive := 1;

  signal add_result   : std_logic_vector(31 downto 0);
  signal mul_result   : std_logic_vector(31 downto 0);
  signal add16_result : std_logic_vector(15 downto 0);
  signal mul16_result : std_logic_vector(15 downto 0);
  signal cmp_result   : std_logic_vector(31 downto 0);
  signal bit_result   : std_logic_vector(31 downto 0);

  -- Add and multiply after the width multiplexer, in result-register shape.
  signal add_sel : std_logic_vector(31 downto 0);
  signal mul_sel : std_logic_vector(31 downto 0);

  -- Operands as the compare and bitwise units need to see them; see below.
  signal cmp_opA : std_logic_vector(31 downto 0);
  signal cmp_opB : std_logic_vector(31 downto 0);
  signal bit_opA : std_logic_vector(31 downto 0);
  signal bit_opB : std_logic_vector(31 downto 0);

  signal opA_sign16 : std_logic_vector(15 downto 0);
  signal opB_sign16 : std_logic_vector(15 downto 0);

  signal busy    : std_logic;
  signal elapsed : natural range 0 to ALU_LATENCY;

begin

  -- The Posit<32,2> adder and multiplier are one pipeline stage deep, unlike
  -- the Posit<16,2> pair beside them: FloPoCo puts a register in the adder's
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

  -- The Posit<16,2> pair. Both widths compute on every command and the answer
  -- is picked afterwards, exactly as add and multiply already were: gating one
  -- pair off would buy no power at this size -- the operand registers hold
  -- still between commands, so the idle pair does not toggle anyway -- and
  -- would put an enable on the critical path of the one that is running.
  add16_inst : component PositAdder16
    port map (
      clk => clk,
      X   => opA(15 downto 0),
      Y   => opB(15 downto 0),
      R   => add16_result
    );

  mul16_inst : component PositMult16
    port map (
      clk => clk,
      X   => opA(15 downto 0),
      Y   => opB(15 downto 0),
      R   => mul16_result
    );

  -- `posit_compare` and `posit_bitwise` stay 32 bits wide at both precisions;
  -- what changes is what they are handed. Two different extensions, because
  -- the two units want opposite things from the upper half.
  --
  -- Compare is a signed compare on the raw encoding, so a Posit<16,2> operand
  -- has to be SIGN-extended: 0xFFFF is -minpos and must order below zero, and
  -- zero-extending it would make it the largest value in the word instead.
  -- Sign extension preserves the whole ordering, so one 32-bit comparator
  -- answers for both widths and the narrow mode costs no second one.
  opA_sign16 <= (others => opA(15));
  opB_sign16 <= (others => opB(15));

  cmp_opA <= opA_sign16 & opA(15 downto 0) when precision = PREC_P16 else opA;
  cmp_opB <= opB_sign16 & opB(15 downto 0) when precision = PREC_P16 else opB;

  -- Bitwise wants the opposite: the upper half MASKED, not extended. Bits
  -- 31:16 of the operand registers are stale -- the interface writes bytes and
  -- nothing clears them when the precision changes -- so an unmasked AND in
  -- Posit<16,2> mode would return whatever a previous Posit<32,2> command left
  -- above bit 15. Masking first makes the answer the same shape as every other
  -- Posit<16,2> result: the value in the low half, zeros above it.
  bit_opA <= x"0000" & opA(15 downto 0) when precision = PREC_P16 else opA;
  bit_opB <= x"0000" & opB(15 downto 0) when precision = PREC_P16 else opB;

  cmp_inst : component posit_compare
    port map (
      x      => cmp_opA,
      y      => cmp_opB,
      op     => opcode(0),
      result => cmp_result
    );

  bit_inst : component posit_bitwise
    port map (
      x      => bit_opA,
      y      => bit_opB,
      op     => opcode(1 downto 0),
      result => bit_result
    );

  -- A Posit<16,2> answer occupies the low half of `result` and reads back as
  -- zero above it, so software can read all four result bytes at either
  -- precision and never be handed a stale byte it has to know to discard.
  add_sel <= add_result when precision = PREC_P32 else x"0000" & add16_result;
  mul_sel <= mul_result when precision = PREC_P32 else x"0000" & mul16_result;

  with opcode select
    result <= add_sel    when OP_ADD,
              mul_sel    when OP_MULT,
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
        -- The edge that writes `control` in aion_interface. `opcode` and
        -- `precision` become the new command here, and the arithmetic's
        -- pipeline stage captures the operand registers on this same edge.
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
