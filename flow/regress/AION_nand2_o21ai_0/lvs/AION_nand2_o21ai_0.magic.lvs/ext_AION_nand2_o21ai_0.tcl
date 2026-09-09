crashbackups stop
drc off
gds read /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/AION_nand2_o21ai_0.gds
if {[lsearch [cellname list topcells] {AION_nand2_o21ai_0}] < 0} {
    set _fp [open {/foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/lvs/AION_nand2_o21ai_0.magic.lvs/ext_AION_nand2_o21ai_0.cellmismatch} w]
    puts $_fp [cellname list topcells]
    close $_fp
    quit -noprompt
}
load AION_nand2_o21ai_0
select top cell
flatten AION_nand2_o21ai_0_flat
load AION_nand2_o21ai_0_flat
cellname delete AION_nand2_o21ai_0
cellname rename AION_nand2_o21ai_0_flat AION_nand2_o21ai_0
select top cell
extract path /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/lvs/AION_nand2_o21ai_0.magic.lvs
extract no capacitance
extract no coupling
extract no resistance
extract no length
extract all
ext2spice lvs
ext2spice -p /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/lvs/AION_nand2_o21ai_0.magic.lvs -o /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/lvs/AION_nand2_o21ai_0.magic.lvs/AION_nand2_o21ai_0.ext.spc
quit -noprompt
