* NGSPICE file created from reference_AION_nand2_o21ai_0.ext - technology: ihp-sg13g2

.subckt reference_AION_nand2_o21ai_0 I0 I1 I2 I3 O0 VDD VSS
X0 VSS I2 a_406_110# VSS sg13_lv_nmos ad=0.1406p pd=1.12u as=0.2516p ps=2.16u w=0.74u l=0.15u
X1 a_504_432# I2 VDD VDD sg13_lv_pmos ad=0.2128p pd=1.5u as=0.3808p ps=2.92u w=1.12u l=0.15u
X2 O0 a_154_412# a_406_110# VSS sg13_lv_nmos ad=0.2516p pd=2.16u as=0.1406p ps=1.12u w=0.74u l=0.15u
X3 a_154_118# I1 VSS VSS sg13_lv_nmos ad=0.1406p pd=1.12u as=0.2516p ps=2.16u w=0.74u l=0.13u
X4 a_154_412# I0 a_154_118# VSS sg13_lv_nmos ad=0.2516p pd=2.16u as=0.1406p ps=1.12u w=0.74u l=0.13u
X5 VDD a_154_412# O0 VDD sg13_lv_pmos ad=0.3808p pd=2.92u as=0.2128p ps=1.5u w=1.12u l=0.15u
X6 a_154_412# I1 VDD VDD sg13_lv_pmos ad=0.2128p pd=1.5u as=0.3808p ps=2.92u w=1.12u l=0.13u
X7 VDD I0 a_154_412# VDD sg13_lv_pmos ad=0.3808p pd=2.92u as=0.2128p ps=1.5u w=1.12u l=0.13u
X8 a_406_110# I3 VSS VSS sg13_lv_nmos ad=0.1406p pd=1.12u as=0.1406p ps=1.12u w=0.74u l=0.15u
X9 O0 I3 a_504_432# VDD sg13_lv_pmos ad=0.2128p pd=1.5u as=0.2128p ps=1.5u w=1.12u l=0.15u
.ends

