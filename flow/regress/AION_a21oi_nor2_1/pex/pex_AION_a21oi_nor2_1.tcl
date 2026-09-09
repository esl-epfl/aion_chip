crashbackups stop
drc off
gds read ../../../flow/regress/AION_a21oi_nor2_1/AION_a21oi_nor2_1.gds
if {[lsearch [cellname list topcells] {AION_a21oi_nor2_1}] < 0} {
    set _fp [open {/foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/pex/pex_AION_a21oi_nor2_1.cellmismatch} w]
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
extract path /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/pex
ext2spice lvs
extresist threshold 10000
extresist mindelay 1
extresist minres 1000
extract do resistance
extract do unique
extract all
ext2spice extresist on
ext2spice cthresh 0.01
ext2spice -p /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/pex -o /foss/designs/aion_chip/flow/regress/AION_a21oi_nor2_1/pex/AION_a21oi_nor2_1.pex.spice.tmp
quit -noprompt
