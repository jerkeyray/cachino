# Live session limits, 2026-11-08

Question: does limiting concurrent sessions remove eviction in live traffic,
and what does it cost? Summarized in
[report 3.8](../report.md#38-a-live-concurrency-limit).

## Fixed cap

Cachino's proxy can cap active sessions (`-max-active-sessions`; a session stays
active between its turns for an idle period, and new sessions wait for a free
slot). Four concurrent Qwen3-1.7B sessions shared one proxy and one 438-block
cache, with the cap off and at 2, over five engine seeds (run
`limit-matrix-20261108`). For seeds 1 to 3, runs without the cap reproduced
the earlier seeded runs exactly.

| Seed | Cap | Requests | Lost to eviction | Engine time to first token | Wall clock | Waits |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | off | 40 | 0 | 77.1 s | 201.8 s | none |
| 1 | 2 | 30 | 0 | 55.5 s | 189.3 s | 101 s, 117 s |
| 2 | off | 39 | 4,640 | 337.8 s | 387.5 s | none |
| 2 | 2 | 40 | 0 | 212.2 s | 373.7 s | 197 s, 217 s |
| 3 | off | 40 | 0 | 139.2 s | 241.6 s | none |
| 3 | 2 | 37 | 0 | 191.0 s | 303.6 s | 120 s, 140 s |
| 4 | off | 38 | 66,528 | 1,137.7 s | 1,242.8 s | none |
| 4 | 2 | 40 | 0 | 146.3 s | 299.0 s | 182 s, 186 s |
| 5 | off | 40 | 256 | 202.6 s | 306.0 s | none |
| 5 | 2 | 40 | 0 | 93.8 s | 233.4 s | 72 s, 146 s |

- The cap removed eviction in all three seeds whose sessions outgrew the cache.
- In seed 4 the uncapped sessions grew long: eviction caused 70% of all
  recomputation, reuse fell to 43%, and one session was rejected when its
  prompt passed the 12,288-token context. With the cap, wall clock fell from
  1,243 s to 299 s.
- Where nothing would have been evicted, the cap only made two sessions wait,
  and total wall clock rose by up to 26%.
- The predictor was exact on 370 of 384 requests, all misses in evicting runs.

Changing the interleaving changes which sampled replies are generated, so
trajectories, request counts and recomputed totals differ between paired runs;
eviction is the comparable measure. Timing is on the Mac CPU and compares
conditions only.

## Token budget

A size-aware limit was tried next (`-session-token-budget`, set to the 14,016
tokens of the cache). The proxy estimates each session's footprint from its
latest request's size (0.25 tokens per byte, measured on these captures). A
turn proceeds if it fits after the sessions admitted before it; otherwise the
session is suspended between turns. The first version let an older turn start
beside younger requests already in flight, and on seed 2 it evicted as much as
no limit (4,576 tokens): the oldest session's prompt jumped to 10,492 tokens
while three younger requests were running. The second version (runs
`s*-n4-drain`) makes such a turn wait for those requests to finish; the table
shows that version.

| Seed | Off: lost | Off: wall clock | Cap 2: wait | Cap 2: wall clock | Budget: lost | Budget: wait | Budget: wall clock |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0 | 201.8 s | 218 s | 189.3 s | 0 | none | 197.7 s |
| 2 | 4,640 | 387.5 s | 414 s | 373.7 s | 0 | 125 s | 339.5 s |
| 3 | 0 | 241.6 s | 259 s | 303.6 s | 0 | none | 228.2 s |
| 4 | 66,528 | 1,242.8 s | 368 s | 299.0 s | 3,456 | 258 s | 579.6 s |
| 5 | 256 | 306.0 s | 217 s | 233.4 s | 3,616 | 136 s | 376.8 s |

- Where nothing would have been evicted (seeds 1 and 3) the budget never made
  a session wait, unlike the fixed cap.
- On seed 2 it removed eviction with a third of the cap's waiting.
- On seeds 4 and 5 it lost 3,456 and 3,616 tokens, all from sessions it had
  suspended: a suspended session's prefix is free to evict while it waits. On
  seed 5 that was more than no limit lost.
- The fixed cap holds sessions before they start, so it never pays that cost.

The predictor was exact on 304 of 315 requests in these budget runs, both
versions included.
