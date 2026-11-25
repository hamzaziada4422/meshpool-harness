## D0 vs codex
```
rows 72  arms ['C171', 'D0', 'codex']
tasks: D0 12, codex 12, paired 12
        D0: solved 20/24 (83.3%)  $ 0.2922  $/solved 0.01461  median s 69.3  errors 0
     codex: solved 21/24 (87.5%)  $ 0.1650  $/solved 0.00785  median s 54.5  errors 0
solve-rate diff -4.2 pts (95% CI -12.5 to +0.0)
cost per solved ratio 1.86x (95% CI 0.92-3.49)
median per-task time ratio 1.02x (95% CI 0.85-3.49)
BAR: cost FAIL, quality FAIL, time FAIL -> NOT MET
```
## C171 vs codex
```
rows 72  arms ['C171', 'D0', 'codex']
tasks: C171 12, codex 12, paired 12
      C171: solved 22/24 (91.7%)  $ 0.1258  $/solved 0.00572  median s 65.0  errors 0
     codex: solved 21/24 (87.5%)  $ 0.1650  $/solved 0.00785  median s 54.5  errors 0
solve-rate diff +4.2 pts (95% CI +0.0 to +12.5)
cost per solved ratio 0.73x (95% CI 0.57-0.90)
median per-task time ratio 0.93x (95% CI 0.80-1.26)
BAR: cost PASS, quality PASS, time PASS -> ALL PASS
```

Commit 6a557338. swe_sympy, 12 tasks x 2 reps, one window, MH_CAPS cache hints, isolation as in swe6.
Gateway billing: X-Litellm-Key-Spend 74.5037 before, 75.6709 after, so $1.167 billed (including two 5-token probes) against $0.583 at list price ($0.15/$0.003/$0.60): 2.00x. Every call was deepseek-v4.1-flash.
