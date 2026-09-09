crashbackups stop
drc off
gds read ../../../flow/regress/AION_nand2_o21ai_0/AION_nand2_o21ai_0.gds
if {[lsearch [cellname list topcells] {AION_nand2_o21ai_0}] < 0} {
    set _fp [open {/foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/pex/pex_AION_nand2_o21ai_0.cellmismatch} w]
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
extract path /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/pex
ext2spice lvs
extresist threshold 10000
extresist mindelay 1
extresist minres 1000
extract do resistance
extract do unique
extract all
ext2spice extresist on
ext2spice cthresh 0.01
ext2spice -p /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/pex -o /foss/designs/aion_chip/flow/regress/AION_nand2_o21ai_0/pex/AION_nand2_o21ai_0.pex.spice.tmp
quit -noprompt
