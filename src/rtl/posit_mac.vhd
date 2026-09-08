-- ================================================================
--  SPDX-FileCopyrightText:    2026 Filippo Quadri
--  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
--  Created:                   2026-09-08
--  Description:               Posit MAC - one lane, and an array of them
--
--  Why this exists
--  ---------------
--  The AION cell library is mined from recurring subgraphs of the gate-level
--  netlist, so its value is bounded by how many sites one drawn cell covers.
--  The measured run covered 6% of the logic with two drawn cells out of 78
--  selected patterns: the mining is not the bottleneck, the drawing is. N
--  identical lanes multiply the site count of every pattern without adding a
--  single new cell type to draw, which is the one lever that moves coverage
--  and drawing cost in opposite directions.
--
--  So the lanes must be *identical* and they must not be shared away.  Two
--  design choices follow from that, and neither is arbitrary:
--
--  * every lane owns its operand registers.  Broadcasting one operand pair to
--    all lanes would leave N multipliers computing the same product, and
--    SYNTH_SHARE_RESOURCES is on -- yosys would collapse them into one and
--    take the repetition with it.  Loading each lane separately and firing
--    them together is also what a dot-product engine actually does, and it is
--    what makes all N lanes switch at once, which is the only way the power
--    difference between the two hardenings is measurable on a bench.
--
--  * every lane carries the bypass multiplexers, including the lanes that
--    never use them.  Feeding lanes 1..N-1 a constant instead would let yosys
--    optimise their multiplexers away, and N-1 lanes that differ structurally
--    from lane 0 are N-1 lanes that no longer amortise lane 0's cells.
--
--  The bypass path is what keeps the design the size it was.  A lane is a
--  PositMult (19,781 um2) and a PositAdder (8,850 um2); a lane bolted next to
--  the existing ALU would have doubled the design.  Instead the ALU's add and
--  multiply are served *by* lane 0 through those multiplexers, so LANES=1 is
--  the old design plus about 64 multiplexers, and every further lane is the
--  honest ~28,900 um2 it costs.
-- ================================================================

library ieee;
  use ieee.std_logic_1164.all;
  use ieee.numeric_std.all;

library work;

-- ----------------------------------------------------------------
--  One lane: acc <- acc + x * y, in two pipeline stages.
--
--  The multiply and the add are both purely combinational and together are
--  the longest path in the chip, so they are never in series in one cycle:
--  `run` registers the product, and the cycle after it the sum lands in the
--  accumulator.  The critical path stays max(multiply, add) rather than
--  their sum, which is what keeps the MAC from halving the clock.
--
--  That is also why `done` needs no change: posit_alu already raises it two
--  cycles after `start`, and the accumulator settles on exactly that edge.
-- ----------------------------------------------------------------

entity posit_mac_lane is
  port (
    clk    : in  std_logic;
    rst_n  : in  std_logic;
    -- '1' routes the bus straight through the arithmetic, which is how the
    -- ALU's plain add and multiply opcodes are served without a second
    -- adder and multiplier existing anywhere in the design.
    bypass : in  std_logic;
    bus_x  : in  std_logic_vector(15 downto 0);
    bus_y  : in  std_logic_vector(15 downto 0);
    load   : in  std_logic;                      -- latch bus_x/bus_y as this lane's operands
    run    : in  std_logic;                      -- start one accumulate
    clear  : in  std_logic;                      -- zero the accumulator
    prod   : out std_logic_vector(15 downto 0);  -- combinational product
    sum    : out std_logic_vector(15 downto 0);  -- combinational sum
    acc    : out std_logic_vector(15 downto 0)
  );
end entity posit_mac_lane;

architecture arch of posit_mac_lane is

  component PositAdder is
    port (
      clk : in  std_logic;
      x   : in  std_logic_vector(15 downto 0);
      y   : in  std_logic_vector(15 downto 0);
      r   : out std_logic_vector(15 downto 0)
    );
  end component PositAdder;

  component PositMult is
    port (
      clk : in  std_logic;
      x   : in  std_logic_vector(15 downto 0);
      y   : in  std_logic_vector(15 downto 0);
      r   : out std_logic_vector(15 downto 0)
    );
  end component PositMult;

  signal x_reg    : std_logic_vector(15 downto 0);
  signal y_reg    : std_logic_vector(15 downto 0);
  signal prod_reg : std_logic_vector(15 downto 0);
  signal acc_reg  : std_logic_vector(15 downto 0);
  signal run_d    : std_logic;

  signal mul_x : std_logic_vector(15 downto 0);
  signal mul_y : std_logic_vector(15 downto 0);
  signal add_a : std_logic_vector(15 downto 0);
  signal add_b : std_logic_vector(15 downto 0);
  signal prod_i : std_logic_vector(15 downto 0);
  signal sum_i  : std_logic_vector(15 downto 0);

begin

  -- Bypass feeds the bus to both units; otherwise the lane multiplies its own
  -- operands and accumulates the product it registered last cycle.
  mul_x <= bus_x when bypass = '1' else x_reg;
  mul_y <= bus_y when bypass = '1' else y_reg;
  add_a <= bus_x when bypass = '1' else prod_reg;
  add_b <= bus_y when bypass = '1' else acc_reg;

  mul_inst : component PositMult
    port map (
      clk => clk,
      x   => mul_x,
      y   => mul_y,
      r   => prod_i
    );

  add_inst : component PositAdder
    port map (
      clk => clk,
      x   => add_a,
      y   => add_b,
      r   => sum_i
    );

  prod <= prod_i;
  sum  <= sum_i;
  acc  <= acc_reg;

  process (clk, rst_n) is
  begin

    if (rst_n = '0') then
      x_reg    <= (others => '0');
      y_reg    <= (others => '0');
      prod_reg <= (others => '0');
      acc_reg  <= (others => '0');
      run_d    <= '0';
    elsif rising_edge(clk) then
      run_d <= run;

      if (load = '1') then
        x_reg <= bus_x;
        y_reg <= bus_y;
      end if;

      -- Stage 1: the product of this lane's own operands.
      if (run = '1') then
        prod_reg <= prod_i;
      end if;

      -- Stage 2, one cycle later, so the multiply and the add never share a
      -- cycle. Clear wins, so a clear issued during an accumulate leaves the
      -- lane at zero rather than at half a result.
      if (clear = '1') then
        acc_reg <= (others => '0');
      elsif (run_d = '1') then
        acc_reg <= sum_i;
      end if;
    end if;

  end process;

end architecture arch;

-- ----------------------------------------------------------------
--  LANES lanes, loaded one at a time and fired together.
--
--  `sel` picks which lane `load` writes and which accumulator is read back.
--  It is three bits because it comes from reg_control(6 downto 4), which is
--  where the register map had room; LANES may be smaller than 8, so a `sel`
--  past the last lane reads zero rather than running off the array.
-- ----------------------------------------------------------------

library ieee;
  use ieee.std_logic_1164.all;
  use ieee.numeric_std.all;

library work;

entity posit_mac_array is
  generic (
    LANES : positive := 1
  );
  port (
    clk    : in  std_logic;
    rst_n  : in  std_logic;
    bypass : in  std_logic;
    sel    : in  std_logic_vector(2 downto 0);
    bus_x  : in  std_logic_vector(15 downto 0);
    bus_y  : in  std_logic_vector(15 downto 0);
    load   : in  std_logic;                      -- into lane `sel`
    run    : in  std_logic;                      -- every lane, together
    clear  : in  std_logic;                      -- every lane, together
    prod0  : out std_logic_vector(15 downto 0);  -- lane 0, for the ALU's multiply
    sum0   : out std_logic_vector(15 downto 0);  -- lane 0, for the ALU's add
    acc    : out std_logic_vector(15 downto 0)   -- accumulator of lane `sel`
  );
end entity posit_mac_array;

architecture arch of posit_mac_array is

  component posit_mac_lane is
    port (
      clk    : in  std_logic;
      rst_n  : in  std_logic;
      bypass : in  std_logic;
      bus_x  : in  std_logic_vector(15 downto 0);
      bus_y  : in  std_logic_vector(15 downto 0);
      load   : in  std_logic;
      run    : in  std_logic;
      clear  : in  std_logic;
      prod   : out std_logic_vector(15 downto 0);
      sum    : out std_logic_vector(15 downto 0);
      acc    : out std_logic_vector(15 downto 0)
    );
  end component posit_mac_lane;

  type word_array is array (natural range <>) of std_logic_vector(15 downto 0);

  signal lane_prod : word_array(0 to LANES - 1);
  signal lane_sum  : word_array(0 to LANES - 1);
  signal lane_acc  : word_array(0 to LANES - 1);
  signal lane_load : std_logic_vector(0 to LANES - 1);

begin

  lanes_gen : for i in 0 to LANES - 1 generate

    -- The only per-lane signal. Everything else is broadcast, so the lanes
    -- stay structurally identical and one mined cell covers all of them.
    lane_load(i) <= load when to_integer(unsigned(sel)) = i else '0';

    lane_inst : component posit_mac_lane
      port map (
        clk    => clk,
        rst_n  => rst_n,
        bypass => bypass,
        bus_x  => bus_x,
        bus_y  => bus_y,
        load   => lane_load(i),
        run    => run,
        clear  => clear,
        prod   => lane_prod(i),
        sum    => lane_sum(i),
        acc    => lane_acc(i)
      );

  end generate lanes_gen;

  prod0 <= lane_prod(0);
  sum0  <= lane_sum(0);

  -- Read-back multiplexer, written as a loop so a `sel` beyond the last lane
  -- reads zero instead of indexing past the end of the array.
  read_mux : process (sel, lane_acc) is
  begin

    acc <= (others => '0');

    for i in 0 to LANES - 1 loop
      if (to_integer(unsigned(sel)) = i) then
        acc <= lane_acc(i);
      end if;
    end loop;

  end process read_mux;

end architecture arch;
