# Release 0.0.16 round: verify_paired per suite (raw rows in this folder)

Meshpool arm: the product default (MH_CAPS cache hints only), 200-step cap, isolation as in swe7. Codex: codex-cli 0.171.0. Model: deepseek-v4.1-flash through llm.meshpool.ai. 2 reps per task. Gateway billing was 2.0x list price on this run (X-Litellm-Key-Spend); the usd columns are list price.

## swe_sympy (1c0b6df5)
```
tasks: MP 12, codex 12, paired 12
        MP: solved 22/24 (91.7%)  $ 0.1281  $/solved 0.00582  median s 80.0  errors 0
     codex: solved 22/24 (91.7%)  $ 0.1686  $/solved 0.00766  median s 112.8  errors 0
solve-rate diff +0.0 pts (95% CI +0.0 to +0.0)
cost per solved ratio 0.76x (95% CI 0.69-0.84)
median per-task time ratio 0.93x (95% CI 0.68-1.29)
BAR: cost PASS, quality PASS, time PASS -> ALL PASS
```

## v2_dev_core (1c0b6df5)
```
tasks: MP 21, codex 21, paired 21
        MP: solved 34/42 (81.0%)  $ 0.1556  $/solved 0.00458  median s 48.0  errors 0
     codex: solved 38/42 (90.5%)  $ 0.1889  $/solved 0.00497  median s 54.1  errors 1
solve-rate diff -9.5 pts (95% CI -21.4 to +2.4)
cost per solved ratio 0.92x (95% CI 0.71-1.20)
median per-task time ratio 1.15x (95% CI 1.00-1.57)
BAR: cost FAIL, quality FAIL, time FAIL -> NOT MET
```

## work (1c0b6df5)
```
tasks: MP 27, codex 27, paired 27
        MP: solved 45/54 (83.3%)  $ 0.0536  $/solved 0.00119  median s 13.9  errors 0
     codex: solved 44/54 (81.5%)  $ 0.1035  $/solved 0.00235  median s 18.0  errors 0
solve-rate diff +1.9 pts (95% CI -5.6 to +11.1)
cost per solved ratio 0.51x (95% CI 0.44-0.58)
median per-task time ratio 0.81x (95% CI 0.65-1.00)
BAR: cost PASS, quality FAIL, time PASS -> NOT MET
```

## final22_core (d92bf401)
```
tasks: MP 78, codex 78, paired 78
        MP: solved 143/156 (91.7%)  $ 0.2706  $/solved 0.00189  median s 17.5  errors 1
     codex: solved 145/156 (92.9%)  $ 0.4109  $/solved 0.00283  median s 22.1  errors 1
solve-rate diff -1.3 pts (95% CI -5.8 to +3.2)
cost per solved ratio 0.67x (95% CI 0.55-0.81)
median per-task time ratio 0.89x (95% CI 0.69-1.08)
BAR: cost PASS, quality FAIL, time PASS -> NOT MET
```

## office_1c0b6df5 O7 (1c0b6df5)
```
tasks: mp-work 7, codex 7, paired 7
   mp-work: solved 13/14 (92.9%)  $ 0.3944  $/solved 0.03034  median s 75.7  errors 0
     codex: solved 13/14 (92.9%)  $ 0.1478  $/solved 0.01137  median s 54.8  errors 0
solve-rate diff +0.0 pts (95% CI +0.0 to +0.0)
cost per solved ratio 2.67x (95% CI 0.81-4.87)
median per-task time ratio 1.65x (95% CI 0.72-2.25)
BAR: cost FAIL, quality PASS, time FAIL -> NOT MET
```

## office_1c0b6df5 spreadsheetbench (1c0b6df5)
```
tasks: mp-work 6, codex 6, paired 6
   mp-work: solved 9/12 (75.0%)  $ 0.0757  $/solved 0.00841  median s 24.6  errors 0
     codex: solved 12/12 (100.0%)  $ 0.0758  $/solved 0.00632  median s 25.2  errors 0
solve-rate diff -25.0 pts (95% CI -41.7 to -8.3)
cost per solved ratio 1.33x (95% CI 0.83-2.33)
median per-task time ratio 1.63x (95% CI 0.80-2.93)
BAR: cost FAIL, quality FAIL, time FAIL -> NOT MET
```

## office_1c0b6df5 officebench (1c0b6df5)
```
tasks: mp-work 4, codex 4, paired 4
   mp-work: solved 8/8 (100.0%)  $ 0.0341  $/solved 0.00427  median s 13.6  errors 0
     codex: solved 8/8 (100.0%)  $ 0.0452  $/solved 0.00566  median s 25.4  errors 0
solve-rate diff +0.0 pts (95% CI +0.0 to +0.0)
cost per solved ratio 0.75x (95% CI 0.67-0.95)
median per-task time ratio 0.88x (95% CI 0.23-1.38)
BAR: cost PASS, quality PASS, time PASS -> ALL PASS
```

## office_1c0b6df5 pooled (O7 + SB + OB)
```
tasks: mp-work 17, codex 17, paired 17
   mp-work: solved 30/34 (88.2%)  $ 0.5042  $/solved 0.01681  median s 30.6  errors 0
     codex: solved 33/34 (97.1%)  $ 0.2689  $/solved 0.00815  median s 37.4  errors 0
solve-rate diff -8.8 pts (95% CI -17.6 to +0.0)
cost per solved ratio 2.06x (95% CI 0.89-3.70)
median per-task time ratio 1.38x (95% CI 0.74-2.16)
BAR: cost FAIL, quality FAIL, time FAIL -> NOT MET
```

## v2_dev_c171 (38f57ffb, MH_SMALL_REPO_STYLE=c171; codex rows from v2_dev_core)
```
tasks: MP 21, codex 21, paired 21
        MP: solved 39/42 (92.9%)  $ 0.1525  $/solved 0.00391  median s 41.0  errors 0
     codex: solved 38/42 (90.5%)  $ 0.1889  $/solved 0.00497  median s 54.1  errors 1
solve-rate diff +2.4 pts (95% CI -4.8 to +9.5)
cost per solved ratio 0.79x (95% CI 0.69-0.90)
median per-task time ratio 0.99x (95% CI 0.81-1.13)
BAR: cost PASS, quality PASS, time PASS -> ALL PASS
```

## final22_c171 (38f57ffb, MH_SMALL_REPO_STYLE=c171; codex rows from final22_core)
```
tasks: MP 78, codex 78, paired 78
        MP: solved 144/156 (92.3%)  $ 0.2899  $/solved 0.00201  median s 24.0  errors 0
     codex: solved 145/156 (92.9%)  $ 0.4109  $/solved 0.00283  median s 22.1  errors 1
solve-rate diff -0.6 pts (95% CI -5.1 to +3.8)
cost per solved ratio 0.71x (95% CI 0.65-0.78)
median per-task time ratio 1.03x (95% CI 0.96-1.20)
BAR: cost PASS, quality FAIL, time FAIL -> NOT MET
```

## office_38f57ffb pooled (mp-work; codex rows from office_1c0b6df5)
```
tasks: mp-work 17, codex 17, paired 17
   mp-work: solved 31/34 (91.2%)  $ 0.3015  $/solved 0.00973  median s 39.7  errors 0
     codex: solved 33/34 (97.1%)  $ 0.2689  $/solved 0.00815  median s 37.4  errors 0
solve-rate diff -5.9 pts (95% CI -17.6 to +5.9)
cost per solved ratio 1.19x (95% CI 0.92-1.55)
median per-task time ratio 1.02x (95% CI 0.58-1.22)
BAR: cost FAIL, quality FAIL, time FAIL -> NOT MET
```

## office_59e49c8b pooled (mp-work; codex rows from office_1c0b6df5)
```
tasks: mp-work 17, codex 17, paired 17
   mp-work: solved 30/34 (88.2%)  $ 0.3782  $/solved 0.01261  median s 46.2  errors 0
     codex: solved 33/34 (97.1%)  $ 0.2689  $/solved 0.00815  median s 37.4  errors 0
solve-rate diff -8.8 pts (95% CI -17.6 to +0.0)
cost per solved ratio 1.55x (95% CI 1.11-1.92)
median per-task time ratio 1.26x (95% CI 0.91-1.75)
BAR: cost FAIL, quality FAIL, time FAIL -> NOT MET
```

Billed through the gateway (X-Litellm-Key-Spend) for the whole round including swe7: $6.38.

## office_9cd021c0 pooled (final office run; mp-work; codex rows from office_1c0b6df5)
```
tasks: mp-work 17, codex 17, paired 17
   mp-work: solved 29/34 (85.3%)  $ 0.2981  $/solved 0.01028  median s 30.1  errors 0
     codex: solved 33/34 (97.1%)  $ 0.2689  $/solved 0.00815  median s 37.4  errors 0
solve-rate diff -11.8 pts (95% CI -29.4 to +2.9)
cost per solved ratio 1.26x (95% CI 0.93-1.64)
median per-task time ratio 0.84x (95% CI 0.70-1.49)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```

Billed through the gateway (X-Litellm-Key-Spend) for the whole round including swe7: $6.66.

## office_eab7e2b3 (mp-work; codex rows from office_1c0b6df5)
### O7
```
   mp-work: solved 13/14 (92.9%)  $ 0.1316  $/solved 0.01012  median s 41.6  errors 0
     codex: solved 13/14 (92.9%)  $ 0.1478  $/solved 0.01137  median s 54.8  errors 0
solve-rate diff +0.0 pts (95% CI -21.4 to +21.4)
cost per solved ratio 0.89x (95% CI 0.66-1.15)
median per-task time ratio 0.50x (95% CI 0.40-1.01)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```
### spreadsheetbench
```
   mp-work: solved 10/12 (83.3%)  $ 0.0606  $/solved 0.00606  median s 23.6  errors 0
     codex: solved 12/12 (100.0%)  $ 0.0758  $/solved 0.00632  median s 25.2  errors 0
solve-rate diff -16.7 pts (95% CI -50.0 to +0.0)
cost per solved ratio 0.96x (95% CI 0.59-1.95)
median per-task time ratio 1.46x (95% CI 0.48-1.63)
BAR: cost FAIL, quality FAIL, time FAIL -> NOT MET
```
### officebench
```
   mp-work: solved 7/8 (87.5%)  $ 0.0316  $/solved 0.00452  median s 16.1  errors 0
     codex: solved 8/8 (100.0%)  $ 0.0452  $/solved 0.00566  median s 25.4  errors 0
solve-rate diff -12.5 pts (95% CI -37.5 to +0.0)
cost per solved ratio 0.80x (95% CI 0.43-1.46)
median per-task time ratio 0.94x (95% CI 0.19-1.33)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```
### pooled
```
   mp-work: solved 30/34 (88.2%)  $ 0.2237  $/solved 0.00746  median s 23.1  errors 0
     codex: solved 33/34 (97.1%)  $ 0.2689  $/solved 0.00815  median s 37.4  errors 0
solve-rate diff -8.8 pts (95% CI -23.5 to +2.9)
cost per solved ratio 0.92x (95% CI 0.73-1.19)
median per-task time ratio 0.81x (95% CI 0.44-1.33)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```

Billed through the gateway (X-Litellm-Key-Spend) for the whole round including swe7: $6.86.

## office_final (1812e40d, release commit; mp-work; codex rows from office_1c0b6df5)
### O7
```
   mp-work: solved 14/14 (100.0%)  $ 0.1758  $/solved 0.01256  median s 46.9  errors 0
     codex: solved 13/14 (92.9%)  $ 0.1478  $/solved 0.01137  median s 54.8  errors 0
solve-rate diff +7.1 pts (95% CI +0.0 to +21.4)
cost per solved ratio 1.10x (95% CI 0.90-1.33)
median per-task time ratio 0.72x (95% CI 0.65-1.50)
BAR: cost FAIL, quality PASS, time PASS -> NOT MET
```
### spreadsheetbench
```
   mp-work: solved 11/12 (91.7%)  $ 0.0566  $/solved 0.00514  median s 23.7  errors 0
     codex: solved 12/12 (100.0%)  $ 0.0758  $/solved 0.00632  median s 25.2  errors 0
solve-rate diff -8.3 pts (95% CI -25.0 to +0.0)
cost per solved ratio 0.81x (95% CI 0.58-1.18)
median per-task time ratio 1.15x (95% CI 0.67-1.87)
BAR: cost FAIL, quality FAIL, time FAIL -> NOT MET
```
### officebench
```
   mp-work: solved 7/8 (87.5%)  $ 0.0372  $/solved 0.00532  median s 15.6  errors 0
     codex: solved 8/8 (100.0%)  $ 0.0452  $/solved 0.00566  median s 25.4  errors 0
solve-rate diff -12.5 pts (95% CI -37.5 to +0.0)
cost per solved ratio 0.94x (95% CI 0.61-1.28)
median per-task time ratio 0.86x (95% CI 0.19-1.95)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```
### pooled
```
   mp-work: solved 32/34 (94.1%)  $ 0.2696  $/solved 0.00843  median s 29.7  errors 0
     codex: solved 33/34 (97.1%)  $ 0.2689  $/solved 0.00815  median s 37.4  errors 0
solve-rate diff -2.9 pts (95% CI -14.7 to +5.9)
cost per solved ratio 1.03x (95% CI 0.86-1.21)
median per-task time ratio 0.86x (95% CI 0.66-1.50)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```

Billed through the gateway (X-Litellm-Key-Spend) for the whole round including swe7: $7.11.

## office_rc (326cb618; mp-work; codex rows from office_1c0b6df5)
### O7
```
   mp-work: solved 12/14 (85.7%)  $ 0.1407  $/solved 0.01172  median s 38.7  errors 0
     codex: solved 13/14 (92.9%)  $ 0.1478  $/solved 0.01137  median s 54.8  errors 0
solve-rate diff -7.1 pts (95% CI -28.6 to +14.3)
cost per solved ratio 1.03x (95% CI 0.74-1.34)
median per-task time ratio 0.65x (95% CI 0.54-0.88)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```
### spreadsheetbench
```
   mp-work: solved 8/12 (66.7%)  $ 0.0766  $/solved 0.00957  median s 17.6  errors 0
     codex: solved 12/12 (100.0%)  $ 0.0758  $/solved 0.00632  median s 25.2  errors 0
solve-rate diff -33.3 pts (95% CI -66.7 to -8.3)
cost per solved ratio 1.51x (95% CI 0.81-2.84)
median per-task time ratio 0.98x (95% CI 0.48-3.54)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```
### officebench
```
   mp-work: solved 7/8 (87.5%)  $ 0.0310  $/solved 0.00443  median s 16.1  errors 0
     codex: solved 8/8 (100.0%)  $ 0.0452  $/solved 0.00566  median s 25.4  errors 0
solve-rate diff -12.5 pts (95% CI -37.5 to +0.0)
cost per solved ratio 0.78x (95% CI 0.53-1.13)
median per-task time ratio 0.98x (95% CI 0.31-2.09)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```
### pooled
```
   mp-work: solved 27/34 (79.4%)  $ 0.2483  $/solved 0.00919  median s 29.7  errors 0
     codex: solved 33/34 (97.1%)  $ 0.2689  $/solved 0.00815  median s 37.4  errors 0
solve-rate diff -17.6 pts (95% CI -35.3 to -2.9)
cost per solved ratio 1.13x (95% CI 0.87-1.47)
median per-task time ratio 0.88x (95% CI 0.56-0.99)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```

Billed through the gateway (X-Litellm-Key-Spend) for the whole round including swe7: $7.37.

## office_final6 — decisive run (f133d912): mp-work 6 reps; codex 6 reps = reps 1-2 from office_1c0b6df5 + reps 3-6 run in this window

Pre-registered gate: cost per solved < 1.0x vs Codex on both O7 and SB.
### O7
```
   mp-work: solved 42/42 (100.0%)  $ 0.4193  $/solved 0.00998  median s 35.7  errors 0
     codex: solved 41/42 (97.6%)  $ 0.4345  $/solved 0.01060  median s 48.5  errors 0
solve-rate diff +2.4 pts (95% CI +0.0 to +7.1)
cost per solved ratio 0.94x (95% CI 0.72-1.18)
median per-task time ratio 0.76x (95% CI 0.61-1.08)
BAR: cost FAIL, quality PASS, time PASS -> NOT MET
```
### spreadsheetbench
```
   mp-work: solved 31/36 (86.1%)  $ 0.2335  $/solved 0.00753  median s 23.6  errors 0
     codex: solved 35/36 (97.2%)  $ 0.2524  $/solved 0.00721  median s 26.8  errors 0
solve-rate diff -11.1 pts (95% CI -27.8 to +5.6)
cost per solved ratio 1.04x (95% CI 0.68-1.54)
median per-task time ratio 0.96x (95% CI 0.71-1.25)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```
### officebench
```
   mp-work: solved 21/24 (87.5%)  $ 0.0930  $/solved 0.00443  median s 14.6  errors 0
     codex: solved 24/24 (100.0%)  $ 0.1285  $/solved 0.00535  median s 17.5  errors 0
solve-rate diff -12.5 pts (95% CI -25.0 to +0.0)
cost per solved ratio 0.83x (95% CI 0.60-1.06)
median per-task time ratio 0.87x (95% CI 0.67-1.16)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```
### pooled
```
   mp-work: solved 94/102 (92.2%)  $ 0.7458  $/solved 0.00793  median s 25.1  errors 0
     codex: solved 100/102 (98.0%)  $ 0.8153  $/solved 0.00815  median s 36.6  errors 0
solve-rate diff -5.9 pts (95% CI -13.7 to +1.0)
cost per solved ratio 0.97x (95% CI 0.77-1.19)
median per-task time ratio 0.89x (95% CI 0.69-1.08)
BAR: cost FAIL, quality FAIL, time PASS -> NOT MET
```

Gate: O7 0.94x (met), SB 1.04x (not met). Billed through the gateway (X-Litellm-Key-Spend) for the whole round including swe7: $8.55.
