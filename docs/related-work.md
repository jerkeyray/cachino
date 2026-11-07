# Related work

A focused overlap check, not a survey; full papers were not reproduced.
Details and search records: [first check](archive/related-work-check-20261101.md),
[follow-up](archive/related-work-check-20261107.md).

| Our result | Closest work | Overlap |
| --- | --- | --- |
| Agent traffic capture and harness comparison | [TraceLab](https://arxiv.org/html/2606.30560v2), [The Cache Wars](https://agnt.gg/whitepapers/the-cache-wars-prompt-cache-efficiency-llm-agent-harnesses), [Agentic Coding in the Wild](https://arxiv.org/html/2608.00101v1) | Covered |
| Context policy, prefix reuse and quality | [The Physical Cost of Context](https://arxiv.org/html/2610.34763v1) | Covered |
| Qwen3 thinking-cue boundary breaks reuse | [Qwen3 issue #1826](https://github.com/QwenLM/Qwen3/issues/1826), [vLLM forum thread](https://discuss.vllm.ai/t/decode-kv-writeback-can-multi-turn-chat-preserve-cache-reuse-with-default-templates/2955) | Known since March 2026, with timings on mlx-lm; not with engine-validated token counts on vLLM |
| Eviction under concurrent agent load | [CacheWise](https://arxiv.org/abs/2606.16824), [Continuum](https://arxiv.org/abs/2511.02230), [Penelope](https://arxiv.org/abs/2611.00391) | Known; these build scheduling and eviction policies on it |
| Predicting reuse from traces | [vLLM native simulator PR](https://github.com/vllm-project/vllm/pull/47922) (open), [PrefixSim](https://github.com/cacheMon/PrefixSim) | Partly; no per-request agreement with a real engine was shown |

What we did not find elsewhere: per-request agreement with a real engine's
reported counts, and a per-request split of each loss into its causes from a
raw capture. Cachino is therefore a measurement and attribution tool with
validated reproductions, not a source of new mechanisms.
