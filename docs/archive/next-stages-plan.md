# Next stages plan

Written 2026-11-07, after the CPU calibration, eviction, live-session,
concurrency and cache-size work. It replaces section 7 of the
[research plan](research-plan.md). Stages are ordered work units, not calendar
commitments. Each lists its question, steps, done condition and what it needs
from the user.

## Where the evidence points

- **Method:** a free-queue model of the pinned vLLM build predicts per-request
  prefix reuse exactly for sequential traffic (every request checked so far),
  and to within one to three blocks under live overlap.
- **Single sessions:** recomputation is mostly new content. The avoidable part
  is the previous reply, re-prefilled because of the thinking-cue boundary,
  plus up to one block.
- **Shared load:** concurrent sessions evict each other once their combined
  working set exceeds the cache, with a cliff (0% to 43% of recomputation
  between 16k and 12k tokens on one fixed trace).

The working main claim is therefore about residency under shared load, with the
thinking-cue boundary as a secondary, template-specific finding. Stage 1 decides
whether that framing survives prior work.

## Stage 1: novelty and framing

Done 2026-11-07: see the [related work check](related-work-check-20261107.md).
All three findings have close prior work. The framing is now a measurement and
attribution tool with validated reproductions, and Stages 2 and 4 were adjusted
there.

Question: what of this is already known?

1. Compare with the closest work named in the research plan (TraceLab, CacheWise,
   The Physical Cost of Context), plus prefix-cache eviction and agent-traffic
   studies found by a fresh search.
2. Search vLLM and Qwen issue trackers and docs for the thinking-cue boundary
   and for reasoning content stripped from history.
3. Write the claim as one sentence for each finding, with what would falsify it.

Done: an updated related-work note with overlap stated per finding, and the main
claim chosen or reframed. Needs: nothing; read-only web search.

## Stage 2: evidence that holds up

Partly done 2026-11-08: see [stage 2 results](stage2-results-20261108.md).
Repetitions and threshold location are done. Simulator comparisons, overlap
replay and the control template remain.

Question: are the shares and the cliff stable, and which parts are general?

1. **Independent repetitions.** Vary the engine's `--seed` per run, which makes
   sampled sessions independent and still reproducible from a fresh start. Run
   at least three seeds for 2, 3 and 4 concurrent sessions at fixed cache size.
   Report ranges, not single values.
2. **Locate the cliff.** Use the predictor to compute the full reuse curve over
   cache size for each captured run, then confirm on the engine at the
   predicted threshold and one block either side.
3. **Close the overlap gap.** Replay captured runs with their recorded
   interleaving rather than sequentially, and refine the overlap predictor's
   admission timing until it matches the engine or the remaining gap is
   explained.
4. **A control template.** Repeat one single-session run with a model whose
   template has no thinking cue, to confirm the reply re-prefill disappears.
   Candidate: Qwen2.5-0.5B-Instruct (a download of about 1 GB).

Done: each headline number has a range over independent runs, the cliff is
located to within a block for one trace, and the cue effect is shown present
and absent. Needs: approval for the control model download.

## Stage 3: choose the finding

Done 2026-11-08: see [findings and claims](findings-20261108.md). Main claim:
Cachino predicts per-request prefix reuse from a capture and attributes each
loss to its cause.

Question: which result is the contribution?

Apply the research plan's stopping table to Stage 1 and 2 results. The leading
candidate: from a capture alone, the free-queue model predicts exactly how much
prefix reuse a mix of agent sessions loses to eviction at a given cache size.

Done: one main claim, its evidence table and the conditions under which it
would be withdrawn. Needs: nothing.

## Stage 4: one intervention

Chosen 2026-11-08: **capture-guided session scheduling.** On a captured
multi-session trace that evicts (the seed 2, four-session run), compare the
recorded interleaving with schedules that change only the order in which
session turns are served: finish a session before admitting the next, or pick
the next turn that minimizes predicted eviction. The prompts, and so model
behavior, are unchanged. Measure on the engine, by replay, the recomputed
tokens saved and the cost in turns delayed. CacheWise's prefix-aware
scheduling is the closest published approach; this evaluates the idea on our
traces, without claiming a new policy.

Question: does acting on the diagnosis reduce recomputation?

Recommended: a residency intervention, because it does not change what the model
sees, so task outcomes cannot change and token and latency metrics are enough.
Options, to choose after Stage 3:

- **Capture-based sizing:** predict the cache needed for a session mix, set it,
  and confirm zero eviction.
- **Admission control:** limit or stagger concurrent sessions when the predicted
  working set exceeds the cache, and measure total recomputation and queueing.

The cue-preserving template is the alternative. It changed the model's
behavior, so it needs a paired task-level test with tests actually run. That
requires command execution in an isolated task copy.

Done: paired before/after measurements over repeated runs, including the
cost (queueing or memory). Needs: for the cue option only, approval for
isolated command execution.

## Stage 5: GPU confirmation

Question: what do these token savings mean in time?

One short session on free GPU time, such as a Kaggle T4. Replay one captured
trace at two cache sizes, below and above the cliff, with capture off, and
compare engine time to first token against recomputed tokens. Check first that
the pinned vLLM build runs on the card.

Done: latency per recomputed token, and the cliff's cost in milliseconds, on one
GPU. Needs: a Kaggle account (no cost) and approval of the run.

## Stage 6: write-up and housekeeping

1. Figures: the eviction curve, per-turn recompute splits and predictor accuracy,
   regenerated from captures by script.
2. A results summary with every claim linked to its note and run.
3. Record the CPU-first method change in the shared Malacca `DECISIONS.md`.
4. Rename the local folder to `cachino` and refresh the local environments.

Done: a reader can reproduce each figure from the repository and the named
captures. Needs: approval for the decisions log entry; publication of anything
beyond this repository needs separate approval.

## Order and dependencies

Stage 1 first, since it can change what Stage 2 measures. Stages 2 and 3 follow.
Stage 4 depends on Stage 3, and Stage 5 can run any time after Stage 2. Stage 6
closes. Only Stage 5 uses a GPU.
