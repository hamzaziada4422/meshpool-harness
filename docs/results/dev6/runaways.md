# Code runaways (dev6 E, swe6 D0/EL): causes

Each run was read from its last request body under bodies/; call numbers are the assistant turns there. Codex call counts per rep are in brackets.

| Run | Calls | Where the excess went | Structural? |
|---|---|---|---|
| v2-te-slugify E rep1 | 57 [9/6] | It wrote 72 to 91 cases with guessed expectations, then fixed its own expectations for calls 5 to 49. Calls 31 to 48 went to debugging an inverted `allowed_extra` guard in its own invariant helper. Calls 50 and 53 were denied (`cd … && rm -f debug_slug.py && python3 …`), followed by retry variants. | Mostly model. Denials: yes. |
| v2-dev2-alg-leaderboard E rep1 | 94 [32/27] | Calls 5 to 13: apply_patch rejections from "Delete File + Add File" of lb.py in one patch, and a `'' is not a valid hunk header` error. Calls 14 to 83: debugging a self-invented bit-packed sort key against its own reference model (`_lb_check.py` run 18 times). Calls 85 to 93: perf tuning after it passed. Rep0 took 13 calls. | Model. apply_patch Delete+Add: minor. |
| v2-dev2-perf-nearest E rep0 | 64 [22/30] | It chased NaN, inf, -0.0 and 2^53 float exactness through three fuzzers ("exactly the same indices for every input"), with two full rewrites of nearest.py (calls 39 and 45). Rep1 took 19 calls. | Model. |
| v2-dev2-te-ttlcache E rep1 | 28 [7/12] | Mutation-testing its own tests. Call 17 was denied (`rm -rf $TMP` in the scratch dir). | Mostly model. Denial: yes. |
| v2-dev2-te-pager E 0/1 | 27/42 [15/10] | Rep1: calls 38, 40 and 41 were denied (`rm -rf __pycache__`, mktemp mutation check), plus retries. Rep0: call 23 `git status --porcelain && git diff --stat` was denied on the dev6 build; on d43ee0cd that command is not classed as destructive. | Partly: denials. |
| sympy-22456 D0 0/1 | 121/80 [40/45] | About 8 redesigns of String's `__new__`, `_construct_*` and `__eq__`, with scope creep into fnodes.py and cnodes.py. 10 to 20 "Meshpool re-ran your last check" injections each re-ran the codegen test suite after an edit; this cost time and tokens, but the model did not chase them. 7 shell calls put shell operators in an argv list (`rg … "|" head`, `"2>&1"`), which errored. | Mostly model. argv misuse: small. |
| sympy-24443 EL 0/1 | 86/95 [47/63] | Calls 1 to 67: diagnosis of homomorphism relator handling through ad-hoc DihedralGroup probes; the same pytest file ran 11 times. Passed at call 70; ended at call 76, so no tail after the pass. | Model. |

Structural causes that Codex does not share, measured across all bodies:

1. **Destructive-rule false positives, refused in headless runs.** permrules.DESTRUCTIVE (`rm\s+-[a-zA-Z]*[rf]…`) matches `rm -f one_file`, `rm -rf __pycache__` and `rm -rf $TMPDIR/x`. In auto mode a destructive command needs approval; headless has no approver, so the reply is "No one is available to approve this… Do not retry it". Codex (approval never) runs them. Denials: dev6 E 12 in 37 runs, dev6 R 4, dev5 P/V/PV 7/7/11, swe6 D0 1, EL 1. Each costs 1 to 3 retry calls and pushes the model toward workarounds. Suggested fix: don't class `rm` as destructive when it is non-recursive with named files, or recursive with every target inside the project or a temp dir (TMPDIR, mktemp, meshpool-scratch) and no glob that reaches above it. Alternatively, in headless auto mode only refuse targets outside the project or temp dirs.
2. **argv-list shell misuse** (D0 only): 7 calls in 23 runs where `|`, `;` or `2>&1` were separate argv elements. Suggested fix: when argv[0] isn't a shell and a bare operator token appears, run the call as `bash -lc` with the joined string, or say so in the error.
3. **apply_patch "Delete File" plus "Add File" of the same path in one patch** is rejected (leaderboard, perf-nearest call 39). Suggested fix: accept it as a full replace.
4. The re-check injection on 22456 D0 adds a full test run to each edit result. It is not chased, but it inflates tokens; EL doesn't have it.

Everything else is single-rep model variance: other reps of the same tasks take 13 to 19 calls. Fixes 1 to 3 together are worth a few calls per affected run, not the 40 to 80-call tails. The tails are where the run decides its approach is wrong and rebuilds; no Meshpool output caused them.
