# Three concurrent agent sessions on one cache, 2026-11-07

Run `live-concurrent-20261107-01`. Three independent mini-swe-agent sessions ran
at the same time against one vLLM CPU server, so they shared one prefix cache.
Each had its own Cachino proxy and capture (`experiments/agent-task/live_concurrent.py`,
base spec `live-cpu-concurrent.json`).

## Setup

- Qwen3-1.7B at `70d244cc`, FP16, temperature 0.7 with no seed, so the sessions
  share the system and task prompt and then diverge. Step limit 10.
- vLLM CPU build at `2173d712`, `--max-model-len 12288`, 1.5 GiB KV cache
  (14,016 tokens = 438 blocks of 32). The cache was reset before the run.
- Read-only snapshot observations with the pinned harness's output truncation.
  Nothing was executed.
- Analysis: `experiments/agent-task/concurrent_attribution.py`. It orders all 30
  requests on one timeline from capture start times and durations, and replays
  it with the free-queue predictor. An in-flight request holds its blocks, and a
  request that does not fit waits for a release, as in the scheduler's queue.

All three sessions ran 10 calls and stopped at their step limit. All 30
requests were HTTP 200 with integrity verified. Their trajectories differed:
one mostly tried heredoc edits (refused by the emulator), one read `more.py` and
then repeated a `grep -r`, and one alternated `cat` and `nl` of the source and
test files. Session 1's prompts grew to 10,055 tokens.

## Findings

Over all 30 requests: 110,271 prompt tokens, 87,808 reused, 22,463 recomputed.

1. **Sessions evicted each other.** For the first three minutes, every
   request's reuse equalled its session's own prompt bound. Once session 1
   passed about 9,000 tokens and the other two kept growing, the three
   sessions' working sets exceeded the 14,016-token cache. From then on, 7
   requests across all three sessions reused less than their own session's
   prefix allowed. Eviction cost 5,536 tokens in total, **25% of all
   recomputation** (1,376, 1,376 and 2,784 by session). For example, session 0's
   turn 5 reused 832 of an available 1,408 tokens, and session 2's turn 9
   reused 3,712 of 5,472.
2. **Sessions also helped each other.** The second and third sessions to start
   reused the first's system and task prompt (832 tokens each). In total,
   1,824 reused tokens came from another session.
3. **The overlap predictor matched 26 of 30 requests exactly.** The 4 misses all
   came during the eviction phase, and each was off by one to three blocks (32
   to 96 tokens), with the engine reusing slightly more than predicted. The
   predictor takes all of a request's blocks at its proxy start time. The
   engine allocates decode blocks as it generates and schedules requests itself,
   so exact block timing under pressure is beyond this model. A sequential
   predictor that ignores overlap matched 25 of 30.
4. **Once pressure ended, reuse recovered.** Session 0's last three turns ran
   alone and matched their bounds again.

## Repetitions

Two more runs used the same spec, each after a cache reset.

- **Run `-02` replicated run `-01` exactly.** Its 30 request bodies equal
  run `-01`'s byte for byte, with sessions 1 and 2 swapped by thread start
  order. Both runs started on a freshly launched server, and the pinned vLLM
  uses a global seed of 0 when no per-request seed is sent
  (`vllm/config/model.py` L215; the startup log reports `seed=0`). So sampled
  traffic is reproducible from a fresh start, and `-02` is a replica, not an
  independent repetition.
- **Run `-03` ran on the already-used server and diverged.** Two of its
  sessions ended early: one at the 12,288-token context limit (HTTP 400 on its
  sixth request), one when a reply reached the 768-token output limit. With 19
  completed requests and a smaller combined working set, it showed **no
  eviction**. The cache served 69% of prompt tokens, cross-session reuse gave
  1,792 tokens, and the overlap predictor matched 17 of 19 requests. Its 2
  misses came where the replay judged the cache full and held a request that
  the engine served at once, so the replay over-reserves blocks under load.

| Run | Requests | Reused | Eviction share of recomputed | Overlap predictor |
| --- | --- | --- | --- | --- |
| `-01` | 30 | 80% | 25% | 26 / 30 |
| `-02` (replica of `-01`) | 30 | 80% | 25% | 26 / 30 |
| `-03` | 19 | 69% | 0% | 17 / 19 |

Live sampled sessions do not hold load constant: how much eviction appears
depends on how long the sessions run and how large they grow. The eviction
share is a property of load relative to cache size, so it needs a controlled
sweep, not more live repetitions.

## Compared with sequential sessions

| Session type | Reused | Eviction share of recomputed |
| --- | --- | --- |
| One session, cache large enough (four runs) | 79% to 95% | 0% |
| Three concurrent sessions, 14k-token cache | 80% | 25% |

In single sessions, the avoidable waste was the re-prefilled reply and block
rounding. With three sessions sharing a cache sized for about one, eviction
became the largest avoidable cause. That points the study toward cache
residency under shared load, as the plan's pivot rule anticipated.

## Limits

- Two independent runs (plus one exact replica), three sessions, one machine,
  CPU token counts only. The size of the
  eviction share depends directly on the cache size relative to the sessions'
  combined working sets, which was chosen small here.
- Sampling without a per-request seed is reproducible only from a fresh
  server start. Load varies between independent runs, so no single eviction
  share is typical.
- Proxy start times stand in for engine admission times.
