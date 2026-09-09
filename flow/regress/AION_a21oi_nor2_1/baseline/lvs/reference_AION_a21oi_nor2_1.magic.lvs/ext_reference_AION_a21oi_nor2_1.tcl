crashbackups stop
drc off
gds read /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/baseline/reference_AION_a21oi_nor2_1.gds
if {[lsearch [cellname list topcells] {reference_AION_a21oi_nor2_1}] < 0} {
    set _fp [open {/foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/baseline/lvs/reference_AION_a21oi_nor2_1.magic.lvs/ext_reference_AION_a21oi_nor2_1.cellmismatch} w]
    puts $_fp [cellname list topcells]
    close $_fp
    quit -noprompt
}
load reference_AION_a21oi_nor2_1
select top cell
flatten reference_AION_a21oi_nor2_1_flat
load reference_AION_a21oi_nor2_1_flat
cellname delete reference_AION_a21oi_nor2_1
cellname rename reference_AION_a21oi_nor2_1_flat reference_AION_a21oi_nor2_1
select top cell
extract path /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/baseline/lvs/reference_AION_a21oi_nor2_1.magic.lvs
extract no capacitance
extract no coupling
extract no resistance
extract no length
extract all
ext2spice lvs
ext2spice -p /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/baseline/lvs/reference_AION_a21oi_nor2_1.magic.lvs -o /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/baseline/lvs/reference_AION_a21oi_nor2_1.magic.lvs/reference_AION_a21oi_nor2_1.ext.spc
quit -noprompt
