# What is new here

[← Documentation](README.md) · [Report 4, related work](report.md#4-related-work)

Both mechanisms this study measures were reported before it. This page
subtracts what was already known and states what remains. Search records:
[first check](records/related-work-check-20261101.md),
[follow-up](records/related-work-check-20261107.md).

## Already known

Descriptions follow the search records; "abstract" means only the abstract was
read.

| Topic | Prior work | What it already covers |
| --- | --- | --- |
| Qwen3's template boundary | [Qwen3 issue #1826](https://github.com/QwenLM/Qwen3/issues/1826), opened March 2026, still open | The same mechanism, traced to a condition in the template; reported on mlx-lm, llama.cpp, vLLM, SGLang and LM Studio, with community timings on Apple hardware |
| Eviction under agent load | [CacheWise](https://arxiv.org/abs/2606.16824) (abstract), [Continuum](https://arxiv.org/abs/2511.02230) (abstract), [Penelope](https://arxiv.org/abs/2611.00391) (abstract) | Coding-agent traces pressure the KV cache; engines evict a finished request's KV while others wait; prefix-aware scheduling, time-to-live pinning and alternative eviction orders are proposed and evaluated |
| Agent traffic and harnesses | [Agentic Coding in the Wild](https://arxiv.org/html/2608.00101v1), [TraceLab](https://arxiv.org/html/2606.30560v2), [The Cache Wars](https://agnt.gg/whitepapers/the-cache-wars-prompt-cache-efficiency-llm-agent-harnesses) | Production cache degradation at turn boundaries, model switches and compaction; collected Claude Code and Codex sessions; six harnesses compared through provider accounting and logging proxies |
| Context policy and reuse | [The Physical Cost of Context](https://arxiv.org/html/2610.34763v1) | Context policies, prefix reuse, uncached prefill and task quality measured together on local vLLM and SGLang |
| Predicting reuse from traces | [vLLM PR #47922](https://github.com/vllm-project/vllm/pull/47922) (open), [PrefixSim](https://github.com/cacheMon/PrefixSim) | The PR runs vLLM's own scheduler and cache manager without weights, so if merged it reproduces engine cache behavior by construction; PrefixSim replays block-hash traces through 46 eviction policies, with no validation against engine-reported hits on its page |

## What remains

1. **An independent model of the cache, checked against the engine request by
   request.** A predictor written from the pinned vLLM source, outside the
   engine, matched vLLM's reported cached tokens on every sequential request on
   CPU and GPU, and on 710 of 738 under live concurrency. The native simulator
   PR would match by construction once merged; Cachino's predictor is a separate
   check that the rules are understood, and it runs on any machine. The two have
   not been compared (it needs x86_64 Linux).
2. **Attribution from a raw capture.** Each recomputed token of each request is
   assigned to one cause (new content, a re-prefilled reply, block rounding,
   eviction, or reuse from another session), starting from the bytes the
   harness sent, through exact token IDs. TraceLab and The Cache Wars infer
   backend behavior from provider accounting or external logs.
3. **Evidence that modeling residency is what matters.** A baseline that
   assumes matching blocks stay cached has 24 times the error, and its error is
   exactly the reuse lost to eviction.
4. **A detector and a measured trade-off.** `analysis/template_check.py` finds a
   template boundary in any capture by rendering each prompt with and without
   its generation prompt; restoring reuse at that boundary also changed the
   model's next action. The measurements recorded from the issue are timings.

## What this is not

- Not a new cache policy or scheduler. The session cap and token budget in 3.8
  are probes of a known mechanism, and the first budget version failed.
- Not a comparison with vLLM's native simulator, which could not run on the Mac.
- Not evidence about production traffic, larger models or other engines.
