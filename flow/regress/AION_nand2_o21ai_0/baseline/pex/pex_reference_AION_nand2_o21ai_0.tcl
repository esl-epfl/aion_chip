crashbackups stop
drc off
gds read ../../../flow/regress/AION_nand2_o21ai_0/baseline/reference_AION_nand2_o21ai_0.gds
if {[lsearch [cellname list topcells] {reference_AION_nand2_o21ai_0}] < 0} {
    set _fp [open {/foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/pex/pex_reference_AION_nand2_o21ai_0.cellmismatch} w]
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
extract path /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/pex
ext2spice lvs
extresist threshold 10000
extresist mindelay 1
extresist minres 1000
extract do resistance
extract do unique
extract all
ext2spice extresist on
ext2spice cthresh 0.01
ext2spice -p /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/pex -o /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/baseline/pex/reference_AION_nand2_o21ai_0.pex.spice.tmp
quit -noprompt
