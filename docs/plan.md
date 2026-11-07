# Plan

Updated 2026-11-08. The original research plan and stage plan are in
[archive](archive/research-plan.md) ([stages](archive/next-stages-plan.md)).

| Stage | Status |
| --- | --- |
| 1. Novelty and framing | Done: [related work](related-work.md) |
| 2. Evidence that holds up | Done: seeded repetitions, threshold located, control template ([results](results.md)) |
| 3. Choose the finding | Done: main claim in [results](results.md) |
| 4. One intervention | Done: capture-guided scheduling ([results](results.md#scheduling-stage-4)) |
| 5. GPU confirmation | Open |
| 6. Write-up | Open |

## Open work

1. **GPU confirmation.** One short run on free GPU time (such as a Kaggle T4):
   replay one trace above and below its threshold with capture off and compare
   time to first token with recomputed tokens. Check first that the pinned vLLM
   build runs on the card. Needs a Kaggle account and approval of the run.
2. **Simulator comparison.** Run vLLM's native simulator (PR #47922) on the same
   captures. It needs `triton-cpu`, which the pinned requirements provide only
   on x86_64 Linux, so it cannot run on this Mac.
3. **Overlap replay.** Done 2026-11-08: the misses come from decode-block timing
   ([results](results.md#overlap-replay)).
4. **Scheduling under real overlap.** Repeat the scheduling test with live
   concurrent sessions and wall-clock delay rather than sequential replay.
5. **Write-up.** Figures regenerated from captures by script, and a short
   summary. Publication beyond this repository needs separate approval.

Housekeeping: record the CPU-first method change in the shared Malacca
`DECISIONS.md` (needs approval), and rename the local folder to `cachino`.
