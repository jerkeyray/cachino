# Scheduling replay, 2026-11-08

Question: can reordering one evicting trace remove eviction without changing
any prompt? Summarized in [report 3.7](../report.md#37-scheduling-to-avoid-eviction).

The four-session run that evicted was replayed at its own cache size in five
orders (run `schedule-replay-20261108-01`, `analysis/schedule_replay.py`),
keeping each session's turns in order so prompts and behavior were unchanged.
Delay is how many positions later than recorded each request ran; it is not a
time.

| Order | Recomputed | Lost to eviction | Mean delay | Max delay |
| --- | --- | --- | --- | --- |
| Recorded interleaving | 26,363 | 4,352 | 0 | 0 |
| At most 3 sessions at once | 26,171 | 4,160 | 3.95 | 26 |
| At most 2 sessions at once | 22,011 | 0 | 4.62 | 18 |
| Greedy by predicted reuse | 22,011 | 0 | 4.33 | 23 |
| One session at a time | 22,011 | 0 | 5.67 | 26 |

Every order matched its prediction on all 39 requests. Two sessions at a time,
or a greedy order, removed eviction and cut recomputation by 16.5%.

A reordered replay cannot show effects on a live agent, whose replies would
change with the order; the live limit runs are in
[session-limits-20261108](session-limits-20261108.md).
