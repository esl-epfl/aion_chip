# AION_nand2_o21ai_0 vs reference_AION_nand2_o21ai_0

```
COMPARE: WIN  area -11.1% (14.515 vs 16.330 um2)  worst delay -0.2% (621.3 vs 622.6 ps)
```

Smaller: **yes** &nbsp; Faster: **yes** &nbsp; Verdict: **WIN**

Corner `typ`, read at slew 0.3294 ns and load 0.0648 pF.

## Metrics

| metric | candidate | baseline | unit | delta | change | better |
| --- | ---: | ---: | :--- | ---: | ---: | :---: |
| placement area | 14.5152 | 16.3296 | um2 | -1.814 | -11.1% | yes |
| worst arc delay | 621.31 | 622.60 | ps | -1.29 | -0.2% | yes |
| mean arc delay | 468.39 | 469.90 | ps | -1.51 | -0.3% | yes |
| cell width | 3.8400 | 4.3200 | um | -0.48 | -11.1% | yes |
| row sites | 8 | 9 | sites | -1 | -11.1% | yes |
| leakage power | 155.878 | 155.878 | pW | +0 | +0.0% | no |
| worst input capacitance | 0.00337 | 0.00355 | pF | -0.0001878 | -5.3% | yes |

Worst arc: candidate `I3->O0 rise`, baseline `I3->O0 rise`.

## Per-arc delay

| arc | candidate (ns) | baseline (ns) | change |
| --- | ---: | ---: | ---: |
| `I0->O0 fall` | 0.43298 | 0.43452 | -0.4% |
| `I0->O0 rise` | 0.39724 | 0.39933 | -0.5% |
| `I1->O0 fall` | 0.44475 | 0.44649 | -0.4% |
| `I1->O0 rise` | 0.39796 | 0.40016 | -0.6% |
| `I2->O0 fall` | 0.42351 | 0.42443 | -0.2% |
| `I2->O0 rise` | 0.61379 | 0.61531 | -0.2% |
| `I3->O0 fall` | 0.41556 | 0.41632 | -0.2% |
| `I3->O0 rise` | 0.62131 | 0.62260 | -0.2% |

## Notes

- corner typ: candidate AION_nand2_o21ai_0_typ_1p20V_25C (AION_nand2_o21ai_0_typ_1p20V_25C.lib) vs baseline reference_AION_nand2_o21ai_0_typ_1p20V_25C (reference_AION_nand2_o21ai_0_typ_1p20V_25C.lib)
- both libraries are characterized at the same corner (voltage 1.2 V, temperature 25 C, process 1): candidate AION_nand2_o21ai_0_typ_1p20V_25C, baseline reference_AION_nand2_o21ai_0_typ_1p20V_25C
- both libraries share a 7x7 characterization grid; compared at its middle point, slew 0.3294 ns / load 0.0648 pF
