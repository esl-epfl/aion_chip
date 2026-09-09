crashbackups stop
gds read ../../../flow/regress/AION_nand2_o21ai_0/baseline/reference_AION_nand2_o21ai_0.gds
if {[lsearch [cellname list topcells] {reference_AION_nand2_o21ai_0}] < 0} {
    set _fp [open {/foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/drc/reference_AION_nand2_o21ai_0.magic.drc/drc_reference_AION_nand2_o21ai_0.cellmismatch} w]
    puts $_fp [cellname list topcells]
    close $_fp
    quit -noprompt
}
load reference_AION_nand2_o21ai_0
set drc_rpt_path /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/drc/reference_AION_nand2_o21ai_0.magic.drc/reference_AION_nand2_o21ai_0.magic.drc.rpt
set fout [open $drc_rpt_path w]
set oscale [cif scale out]
set cell_name reference_AION_nand2_o21ai_0
select top cell
drc euclidean on
drc style drc(full)
drc check
set drcresult [drc listall why]
set count 0
puts $fout "$cell_name"
puts $fout "----------------------------------------"
foreach {errtype coordlist} $drcresult {
  puts $fout $errtype
  puts $fout "----------------------------------------"
  foreach coord $coordlist {
    set bllx [expr {$oscale * [lindex $coord 0]}]
    set blly [expr {$oscale * [lindex $coord 1]}]
    set burx [expr {$oscale * [lindex $coord 2]}]
    set bury [expr {$oscale * [lindex $coord 3]}]
    set coords [format " %.3fum %.3fum %.3fum %.3fum" $bllx $blly $burx $bury]
    puts $fout "$coords"
    set count [expr {$count + 1} ]
  }
  puts $fout "----------------------------------------"
}
puts $fout "\[INFO\] COUNT: $count"
puts $fout "\[INFO\] Should be divided by 3 or 4"
puts $fout ""
close $fout
quit -noprompt
