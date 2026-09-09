crashbackups stop
drc off
gds read /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/AION_a21oi_nor2_1.gds
if {[lsearch [cellname list topcells] {AION_a21oi_nor2_1}] < 0} {
    set _fp [open {/foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/lvs/AION_a21oi_nor2_1.magic.lvs/ext_AION_a21oi_nor2_1.cellmismatch} w]
    puts $_fp [cellname list topcells]
    close $_fp
    quit -noprompt
}
load AION_a21oi_nor2_1
select top cell
flatten AION_a21oi_nor2_1_flat
load AION_a21oi_nor2_1_flat
cellname delete AION_a21oi_nor2_1
cellname rename AION_a21oi_nor2_1_flat AION_a21oi_nor2_1
select top cell
extract path /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/lvs/AION_a21oi_nor2_1.magic.lvs
extract no capacitance
extract no coupling
extract no resistance
extract no length
extract all
ext2spice lvs
ext2spice -p /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/lvs/AION_a21oi_nor2_1.magic.lvs -o /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/lvs/AION_a21oi_nor2_1.magic.lvs/AION_a21oi_nor2_1.ext.spc
quit -noprompt
