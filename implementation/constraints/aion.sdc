# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-04
#  Description:               AION SoC - Timing constraints
#
#  Used for both PNR_SDC_FILE and SIGNOFF_SDC_FILE. LibreLane sets
#  OPENLANE_SDC_IDEAL_CLOCKS=1 for pre-CTS steps and 0 for signoff STA,
#  so the same file serves both.
# ================================================================

# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
proc aion_env {name default} {
  if { [info exists ::env($name)] && $::env($name) ne "" } {
    return $::env($name)
  }
  return $default
}

set clk_port     [lindex [aion_env CLOCK_PORT clk] 0]
set clk_period   [aion_env CLOCK_PERIOD 20.0]
set io_pct       [aion_env IO_DELAY_CONSTRAINT 20]
set io_min_pct   [aion_env IO_MIN_DELAY_CONSTRAINT 0]
set uncertainty  [aion_env CLOCK_UNCERTAINTY_CONSTRAINT 0.25]
set clk_tran     [aion_env CLOCK_TRANSITION_CONSTRAINT 0.15]
set derate_pct   [aion_env TIME_DERATING_CONSTRAINT 5]
set max_fanout   [aion_env MAX_FANOUT_CONSTRAINT 10]
set cap_load_ff  [aion_env OUTPUT_CAP_LOAD 6.0]
set drive_cell   [aion_env SYNTH_DRIVING_CELL sg13g2_buf_4/X]
set clk_drive    [aion_env SYNTH_CLK_DRIVING_CELL $drive_cell]

# ----------------------------------------------------------------
# Clock
#
# Single 50 MHz clock. The TinyTapeout harness drives `clk` directly,
# so it is treated as an ideal source and propagated after CTS.
# ----------------------------------------------------------------
set clk_pin [get_ports $clk_port]
create_clock -name $clk_port -period $clk_period $clk_pin
set clk [get_clocks $clk_port]

puts "\[AION SDC] clock '$clk_port' period ${clk_period}ns"

set_clock_uncertainty $uncertainty $clk
set_clock_transition  $clk_tran    $clk

if { [aion_env OPENLANE_SDC_IDEAL_CLOCKS 0] } {
  unset_propagated_clock [all_clocks]
} else {
  set_propagated_clock [all_clocks]
}

# ----------------------------------------------------------------
# I/O timing
#
# Every port other than clk is a synchronous, single-cycle interface to
# whatever drives the tile. Budget IO_DELAY_CONSTRAINT percent of the
# period on each side, leaving the rest for internal logic.
#
# Max and min are different questions and need different numbers:
#
#   max is a BUDGET -- how much of the period the outside world may spend
#       before the data has to be inside this tile. 20% is a choice.
#   min is a FACT about the driver -- how soon after the edge the bus can
#       start moving. The harness launches ui_in/uio_in from its own flops
#       on this same clock, so the answer is one external clock-to-Q, not
#       io_delay. IO_MIN_DELAY_CONSTRAINT = 0 is the conservative reading
#       of that: the bus may change on the edge itself.
#
# A bare `set_input_delay $io_delay` sets both, and that is the trap: it
# tells STA the inputs sit still for the first 10ns of every cycle, so
# every input-to-register hold check passes by inspection and the resizer
# is given nothing to repair. The shortest of those paths is one input
# buffer plus one gate into a flop's D -- under 0.5ns -- against a clock
# tree about that deep, so some of those flops really do see the next
# byte before their own clock edge arrives. Nothing reports it: the
# checkers see a clean design, and Icarus implements no timing checks, so
# the post-PnR simulation latches the wrong value in silence. With a real
# min, hold repair after CTS buffers the offenders instead.
#
# The same asymmetry applies on the way out: min is what the receiver
# needs held, not what the wire costs.
# ----------------------------------------------------------------
set io_delay     [expr {$clk_period * $io_pct     / 100.0}]
set io_min_delay [expr {$clk_period * $io_min_pct / 100.0}]

set data_inputs [all_inputs]
set clk_index   [lsearch $data_inputs $clk_pin]
if { $clk_index >= 0 } {
  set data_inputs [lreplace $data_inputs $clk_index $clk_index]
}

puts "\[AION SDC] input/output delay max ${io_delay}ns (${io_pct}% of period),\
 min ${io_min_delay}ns (${io_min_pct}%)"

set_input_delay  -max $io_delay     -clock $clk $data_inputs
set_input_delay  -min $io_min_delay -clock $clk $data_inputs
set_output_delay -max $io_delay     -clock $clk [all_outputs]
set_output_delay -min $io_min_delay -clock $clk [all_outputs]

# ----------------------------------------------------------------
# Drive and load
#
# Inputs are driven by a buffer of the same library; outputs see the
# PDK's nominal external load.
# ----------------------------------------------------------------
set_driving_cell \
  -lib_cell [lindex [split $drive_cell "/"] 0] \
  -pin      [lindex [split $drive_cell "/"] 1] \
  $data_inputs

set_driving_cell \
  -lib_cell [lindex [split $clk_drive "/"] 0] \
  -pin      [lindex [split $clk_drive "/"] 1] \
  $clk_pin

set_load [expr {$cap_load_ff / 1000.0}] [all_outputs]

# ----------------------------------------------------------------
# Design rule constraints
# ----------------------------------------------------------------
set_max_fanout $max_fanout [current_design]

if { [info exists ::env(MAX_TRANSITION_CONSTRAINT)] } {
  set_max_transition $::env(MAX_TRANSITION_CONSTRAINT) [current_design]
}
if { [info exists ::env(MAX_CAPACITANCE_CONSTRAINT)] } {
  set_max_capacitance $::env(MAX_CAPACITANCE_CONSTRAINT) [current_design]
}

set_timing_derate -early [expr {1 - $derate_pct / 100.0}]
set_timing_derate -late  [expr {1 + $derate_pct / 100.0}]

# ----------------------------------------------------------------
# Asynchronous reset
#
# rst_n is an asynchronous, active-low reset applied by the harness while
# the clock is stopped. Recovery/removal against a free-running clock is
# therefore not a real constraint; cut the path so it does not dominate
# the reports. Remove this if reset is ever made synchronous.
# ----------------------------------------------------------------
if { [llength [get_ports -quiet rst_n]] > 0 } {
  puts "\[AION SDC] cutting timing on asynchronous reset 'rst_n'"
  set_false_path -from [get_ports rst_n]
}

# ----------------------------------------------------------------
# Multicycle paths
#
# posit_alu raises `done` two cycles after `start`, but every datapath
# stage inside positadder/positmult is still single-cycle, so there is no
# safe multicycle exception to declare today. If the adder/multiplier are
# ever given an explicit multi-cycle handshake, relax them here, e.g.:
#
#   set_multicycle_path 2 -setup -through [get_pins {*mul_inst*}]
#   set_multicycle_path 1 -hold  -through [get_pins {*mul_inst*}]
# ----------------------------------------------------------------
