crashbackups stop
drc off
gds read /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/reference_AION_nand2_o21ai_0.gds
if {[lsearch [cellname list topcells] {reference_AION_nand2_o21ai_0}] < 0} {
    set _fp [open {/foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/lvs/reference_AION_nand2_o21ai_0.magic.lvs/ext_reference_AION_nand2_o21ai_0.cellmismatch} w]
    puts $_fp [cellname list topcells]
    close $_fp
    quit -noprompt
}
load reference_AION_nand2_o21ai_0
select top cell
flatten reference_AION_nand2_o21ai_0_flat
load reference_AION_nand2_o21ai_0_flat
cellname delete reference_AION_nand2_o21ai_0
cellname rename reference_AION_nand2_o21ai_0_flat reference_AION_nand2_o21ai_0
select top cell
extract path /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/lvs/reference_AION_nand2_o21ai_0.magic.lvs
extract no capacitance
extract no coupling
extract no resistance
extract no length
extract all
ext2spice lvs
ext2spice -p /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/lvs/reference_AION_nand2_o21ai_0.magic.lvs -o /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/lvs/reference_AION_nand2_o21ai_0.magic.lvs/reference_AION_nand2_o21ai_0.ext.spc
quit -noprompt
