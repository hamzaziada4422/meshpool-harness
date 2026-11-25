# Independent check: final20-22 (lean core vs Codex 0.171, 52 tasks)

Recomputed from the raw rows with testkit/verify_paired.py (paired bootstrap over tasks, reps kept together, 4000 draws, seed 7). Per-run cost sums equal the request meter for every arm. Pairing complete: 52 tasks, 6 runs per task per arm, no reruns, no missing runs.

## final20

```
rows 208  arms ['codex', 'core']
tasks: core 52, codex 52, paired 52
      core: solved 98/104 (94.2%)  $ 0.2146  $/solved 0.00219  median s 17.5  errors 0
     codex: solved 95/104 (91.3%)  $ 0.2887  $/solved 0.00304  median s 19.8  errors 1
solve-rate diff +2.9 pts (95% CI -5.8 to +12.5)
cost per solved ratio 0.72x (95% CI 0.56-0.91)
median per-task time ratio 0.81x (95% CI 0.69-0.99)
BAR: cost PASS, quality FAIL, time PASS -> NOT MET
```

## final21

```
rows 208  arms ['codex', 'core']
tasks: core 52, codex 52, paired 52
      core: solved 98/104 (94.2%)  $ 0.2845  $/solved 0.00290  median s 15.1  errors 0
     codex: solved 91/104 (87.5%)  $ 0.2956  $/solved 0.00325  median s 21.0  errors 0
solve-rate diff +6.7 pts (95% CI +0.0 to +14.4)
cost per solved ratio 0.89x (95% CI 0.65-1.17)
median per-task time ratio 0.85x (95% CI 0.66-1.04)
BAR: cost FAIL, quality PASS, time PASS -> NOT MET
```

## final22

```
rows 208  arms ['codex', 'core']
tasks: core 52, codex 52, paired 52
      core: solved 98/104 (94.2%)  $ 0.2324  $/solved 0.00237  median s 13.2  errors 0
     codex: solved 94/104 (90.4%)  $ 0.3218  $/solved 0.00342  median s 17.9  errors 0
solve-rate diff +3.8 pts (95% CI -1.9 to +10.6)
cost per solved ratio 0.69x (95% CI 0.55-0.89)
median per-task time ratio 0.82x (95% CI 0.65-0.97)
BAR: cost PASS, quality PASS, time PASS -> ALL PASS
```

## Pooled final20+21+22

```
rows 624  arms ['codex', 'core']
tasks: core 52, codex 52, paired 52
      core: solved 294/312 (94.2%)  $ 0.7315  $/solved 0.00249  median s 15.2  errors 0
     codex: solved 280/312 (89.7%)  $ 0.9061  $/solved 0.00324  median s 19.0  errors 1
solve-rate diff +4.5 pts (95% CI +0.0 to +9.6)
cost per solved ratio 0.77x (95% CI 0.64-0.89)
median per-task time ratio 0.74x (95% CI 0.68-0.90)
BAR: cost PASS, quality PASS, time PASS -> ALL PASS
```
