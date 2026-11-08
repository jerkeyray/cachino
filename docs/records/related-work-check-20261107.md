# Related work for the CPU findings

Done 2026-11-07. It
extends the [first related-work check](related-work-check-20261101.md), which
already found that request capture, harness comparisons and prefix-reuse cost
are studied. This check compares each CPU finding with current work. It is a
focused search, not a survey. Authors' results are reported as theirs; none was
reproduced here.

**Read directly** means the page itself was fetched and read. **Search only**
means only a search-engine summary was seen; those entries are leads, not
evidence.

## Finding A: the thinking-cue boundary

Our result: with Qwen3 and `enable_thinking=false`, each turn's prompt diverges
from the previous generation context at the empty `<think></think>` cue, so the
previous reply is prefilled again. Keeping the cue lets vLLM reuse the reply.

| Source | Status | What it covers |
| --- | --- | --- |
| [QwenLM/Qwen3 issue #1826](https://github.com/QwenLM/Qwen3/issues/1826), opened 2026-03-03, still open | Read directly | The same mechanism, traced to the template's `loop.index0 > ns.last_query_index` condition. Reported for Qwen3 and Qwen3.5 on mlx-lm, llama.cpp, vLLM, SGLang and LM Studio. Community timings on Apple hardware with mlx-lm (for example, turn-2 time to first token from about 5 s to 0.2 s after a template fix). No maintainer response. |
| [vLLM forum: decode KV writeback](https://discuss.vllm.ai/t/decode-kv-writeback-can-multi-turn-chat-preserve-cache-reuse-with-default-templates/2955), 2026-09-10 | Read directly | The same question for vLLM; the only answer is from an automated account, with no measurements in the page text. |
| [Hermes agent issue #56004](https://github.com/NousResearch/hermes-agent/issues/56004), community fixed templates on Hugging Face | Search only | Reasoning stripped on replay; claimed template fixes. |

**Verdict: known.** The mechanism is publicly reported, with timings, but not
with engine-validated token counts on vLLM.

What we add, if anything:
- Exact token-level confirmation on vLLM `2173d712`: the engine's reuse stops at
  the cue on every turn, and with the cue kept the next prompt continues the
  previous prompt plus the generated IDs exactly, and the engine reuses the
  reply's KV.
- Its size in agent sessions: 7% to 19% of recomputed tokens, shrinking as
  observations grow. The issue's dramatic speedups come from long-history chat
  turns with small additions, which is where the share is largest.
- A caveat the issue does not raise: keeping the cue changed the model's next
  action, so a template fix needs a task-level check.

## Finding B: eviction under shared agent load

Our result: concurrent sessions evict each other once their combined working
set exceeds the cache; on one fixed trace, eviction went from 0% to 43% of
recomputation as the cache shrank from 16k to 12k tokens. vLLM evicts each
prompt from its tail.

| Source | Status | What it covers |
| --- | --- | --- |
| [CacheWise](https://arxiv.org/abs/2606.16824), Tiwari et al., June 2026 | Read directly (abstract) | Coding-agent traces put sustained pressure on the KV cache; prefix-aware scheduling with reuse-aware eviction in vLLM; up to 2 to 2.6 times fewer evictions and 3.5 times faster sessions. The abstract does not show a cache-size breakdown. |
| [Continuum](https://arxiv.org/abs/2511.02230), Li et al., Nov 2025 (revised Sep 2026) | Read directly (abstract) | Engines evict a finished request's KV while others wait, breaking reuse across tool calls; time-to-live pinning; Llama, Gemma and GLM on SWE-Bench and BFCL. |
| [Penelope](https://arxiv.org/abs/2611.00391), Fan et al., Oct 2026 | Read directly (abstract) | States that existing systems evict from the ends of sequences, consistent with our tail-first measurement, and argues for evicting the start of the remaining history instead; SGLang, Qwen3, real agent traces. |
| KVFlow, AgentKV, IntentKV; [vLLM RFC #37003](https://github.com/vllm-project/vllm/issues/37003); [llm-d issue #2584](https://github.com/llm-d/llm-d/issues/2584) | Search only | Mutual eviction in multi-agent workflows, phase-aware eviction, priority retention and session pinning proposals. |
| PrefixBench-H100 ([arXiv 2609.19657](https://arxiv.org/pdf/2609.19657)) | Search only | Reportedly, capping KV capacity sharply reduced hit rate on vLLM and TensorRT-LLM, the same kind of capacity cliff. |

**Verdict: known.** That agent sessions evict each other under load, and that
reuse falls steeply with capacity, is the premise of several systems papers.
Our single trace and three sessions add no new phenomenon.

## Finding C: predicting engine reuse from a capture

Our result: replaying the pinned vLLM free-queue rules on exact token IDs from
a capture reproduced the engine's `cached_tokens` exactly on every sequential
request checked (including eviction and five cache sizes), and on 26 of 30
requests under live overlap.

| Source | Status | What it covers |
| --- | --- | --- |
| [vLLM PR #47922, native KV cache simulator](https://github.com/vllm-project/vllm/pull/47922), opened 2026-07-07, still open | Read directly | Runs vLLM's own scheduler and KV cache manager without weights (`--simulate-forward`), replaying caller-supplied token IDs. If merged, it reproduces engine cache behavior by construction. CPU-only prototype. |
| [PrefixSim](https://github.com/cacheMon/PrefixSim) | Read directly | Replays block-hash traces through 46 eviction policies with compute-weighted savings; ships an agentic production trace (freeinference). Its page shows no validation against engine-reported hits. |
| [prefix-cache-simulator](https://github.com/UranusSeven/prefix-cache-simulator) | Search only | Replays tokenized requests against an LRU prefix cache. |

**Verdict: partly known.** Trace-driven prefix-cache simulation exists, and
vLLM's own simulator would supersede a hand-written model. What we did not find
elsewhere: per-request agreement with a real engine's reported counts, and a
per-request split of each miss into prompt change, block rounding, eviction and
cross-session reuse from a raw capture.

## Decision

Per the study's stopping rule ("closest work covers the result:
position it as reproduction and tooling"), Cachino is a **measurement and
attribution tool with validated reproductions**, not a source of new
mechanisms.

Working claim:

> From a raw capture of agent traffic, Cachino attributes every request's lost
> prefix reuse to prompt changes, block rounding, eviction or cross-session
> sharing, with predictions that match vLLM's reported counts exactly for
> sequential traffic. Applied to coding-agent sessions, it shows that the
> known Qwen3 template boundary is a small share of recomputation once
> observations grow, while eviction dominates when sessions share a cache.

It would be withdrawn or narrowed if:
- vLLM's native simulator is merged and gives the same attribution with less
  effort (Cachino then becomes a thin attribution layer over it);
- exact agreement fails on independent runs or another engine build;
- independent repetitions put the template share or the eviction cliff
  outside the ranges reported.

## Search record

Searches on 2026-11-07: CacheWise by arXiv ID; KV retention and eviction for
multi-turn agents under concurrency; trace-based prefix-cache hit-rate
simulation; Qwen3 template thinking removal and prefix caching. Primary pages
fetched: Qwen3 issue #1826, vLLM forum thread 2955, vLLM PR #47922, arXiv
2606.16824, 2511.02230 and 2611.00391 abstract pages, and the PrefixSim README.
Full papers were not read. Version strings such as "Qwen3.6" in search
summaries were not verified.
