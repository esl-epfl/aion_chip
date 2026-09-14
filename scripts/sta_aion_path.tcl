# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Created:                   2026-09-14
#  Description:               OpenSTA: the timing path through an AION cell
#                             of the design step 7 saved in flow/7_pnr/.
#
#  Run in the container, from the project root:
#      sta -no_splash -exit scripts/sta_aion_path.tcl
#  Environment:
#      INST=<instance>   the path through that instance, e.g. INST=_AION_560_
#                        (default: the worst path through any AION instance)
#      OUT=<file>        the report (default flow/7_pnr/reports/aion_path.rpt)
# ================================================================

set root /foss/designs/aion_chip
set pnr  $root/flow/7_pnr
set out  [expr {[info exists ::env(OUT)] ? $::env(OUT) : "$pnr/reports/aion_path.rpt"}]

# Typical corner: the AION cells are characterized at typ only.
read_liberty /foss/pdks/ihp-sg13g2/libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell_typ_1p20V_25C.lib
foreach lib [glob $root/implementation/cells/*/*.lib] { read_liberty $lib }

read_verilog $pnr/nl/tt_um_aion.nl.v
link_design tt_um_aion
read_sdc $pnr/sdc/tt_um_aion.sdc
set_propagated_clock [all_clocks]
read_spef $pnr/spef/nom/tt_um_aion.nom.spef

if {[info exists ::env(INST)]} {
    set through [get_cells $::env(INST)]
} else {
    set through {}
    foreach cell [get_cells *] {
        if {[string match AION_* [get_property $cell ref_name]]} { lappend through $cell }
    }
}
puts "through [llength $through] AION instance(s)"

set fields {slew cap input_pins nets fanout}
report_checks -path_delay max -through $through -format full_clock_expanded \
    -fields $fields -digits 3 > $out
report_checks -path_delay min -through $through -format full_clock_expanded \
    -fields $fields -digits 3 >> $out
puts "wrote $out"
