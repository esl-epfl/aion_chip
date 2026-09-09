# AION_a21oi_nor2_1 vs reference_AION_a21oi_nor2_1

```
COMPARE: LOSS area -11.1% (14.515 vs 16.330 um2)  worst delay +0.2% (547.3 vs 546.3 ps)
```

Smaller: **yes** &nbsp; Faster: **no** &nbsp; Verdict: **LOSS**

Corner `typ`, read at slew 0.3294 ns and load 0.0648 pF.

## Metrics

| metric | candidate | baseline | unit | delta | change | better |
| --- | ---: | ---: | :--- | ---: | ---: | :---: |
| placement area | 14.5152 | 16.3296 | um2 | -1.814 | -11.1% | yes |
| worst arc delay | 547.32 | 546.29 | ps | +1.028 | +0.2% | no |
| mean arc delay | 458.02 | 457.63 | ps | +0.3851 | +0.1% | no |
| cell width | 3.8400 | 4.3200 | um | -0.48 | -11.1% | yes |
| row sites | 8 | 9 | sites | -1 | -11.1% | yes |
| leakage power | 146.090 | 146.090 | pW | +0 | +0.0% | no |
| worst input capacitance | 0.00301 | 0.00318 | pF | -0.0001685 | -5.3% | yes |

Worst arc: candidate `I2->O0 rise`, baseline `I2->O0 rise`.

## Per-arc delay

| arc | candidate (ns) | baseline (ns) | change |
| --- | ---: | ---: | ---: |
| `I0->O0 fall` | 0.42935 | 0.43002 | -0.2% |
| `I0->O0 rise` | 0.52268 | 0.52355 | -0.2% |
| `I1->O0 fall` | 0.42521 | 0.42580 | -0.1% |
| `I1->O0 rise` | 0.53144 | 0.53220 | -0.1% |
| `I2->O0 fall` | 0.33467 | 0.33289 | +0.5% |
| `I2->O0 rise` | 0.54732 | 0.54629 | +0.2% |
| `I3->O0 fall` | 0.33903 | 0.33696 | +0.6% |
| `I3->O0 rise` | 0.53443 | 0.53333 | +0.2% |

## Notes

- corner typ: candidate AION_a21oi_nor2_1_typ_1p20V_25C (AION_a21oi_nor2_1_typ_1p20V_25C.lib) vs baseline reference_AION_a21oi_nor2_1_typ_1p20V_25C (reference_AION_a21oi_nor2_1_typ_1p20V_25C.lib)
- both libraries are characterized at the same corner (voltage 1.2 V, temperature 25 C, process 1): candidate AION_a21oi_nor2_1_typ_1p20V_25C, baseline reference_AION_a21oi_nor2_1_typ_1p20V_25C
- both libraries share a 7x7 characterization grid; compared at its middle point, slew 0.3294 ns / load 0.0648 pF
