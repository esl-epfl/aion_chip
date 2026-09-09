* NGSPICE file created from AION_a21oi_nor2_1.ext - technology: ihp-sg13g2

.subckt AION_a21oi_nor2_1 I2 I3 I0 I1 O0 VDD VSS
X0 O0 a_134_118# VSS VSS sg13_lv_nmos ad=0.1406p pd=1.12u as=0.3589p ps=1.71u w=0.74u l=0.13u
X1 a_558_118# I0 O0 VSS sg13_lv_nmos ad=0.1406p pd=1.12u as=0.1406p ps=1.12u w=0.74u l=0.13u
X2 a_456_415# a_134_118# O0 VDD sg13_lv_pmos ad=0.2128p pd=1.5u as=0.4256p ps=3u w=1.12u l=0.13u
X3 a_134_118# I2 VSS VSS sg13_lv_nmos ad=0.1406p pd=1.12u as=0.2812p ps=2.24u w=0.74u l=0.13u
X4 VSS I3 a_134_118# VSS sg13_lv_nmos ad=0.3589p pd=1.71u as=0.1406p ps=1.12u w=0.74u l=0.13u
X5 VDD I0 a_456_415# VDD sg13_lv_pmos ad=0.2128p pd=1.5u as=0.2128p ps=1.5u w=1.12u l=0.13u
X6 VSS I1 a_558_118# VSS sg13_lv_nmos ad=0.2812p pd=2.24u as=0.1406p ps=1.12u w=0.74u l=0.13u
X7 a_134_415# I2 VDD VDD sg13_lv_pmos ad=0.2128p pd=1.5u as=0.4256p ps=3u w=1.12u l=0.13u
X8 a_134_118# I3 a_134_415# VDD sg13_lv_pmos ad=0.4256p pd=3u as=0.2128p ps=1.5u w=1.12u l=0.13u
X9 a_456_415# I1 VDD VDD sg13_lv_pmos ad=0.4256p pd=3u as=0.2128p ps=1.5u w=1.12u l=0.13u
.ends

