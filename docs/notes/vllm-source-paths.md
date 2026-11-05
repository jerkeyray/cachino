# vLLM observation paths for Suez M1

Inspected read-only on 2026-11-03 at commit
`2173d7121fc6e8d756c884f46691546c61b60c57` (main resolved once, then all
files read by that SHA). No checkout, installation, import or runtime test.
The [source inventory](../../experiments/cache-ground-truth/source-pin.json)
records Git blob SHAs and line counts. This candidate pin must be rechecked
against the eventual GPU environment before it becomes an execution pin.

## Prompt construction and exact input observation

- [`OnlineRenderer.render_chat`, lines 234-327](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/renderers/online_renderer.py#L234)
  validates tool parsing/template options, converts tool models to dictionaries
  and delegates to preprocessing. Request JSON is not the final token sequence.
- [`ChatCompletionRequest`, lines 413-447](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/openai/chat_completion/protocol.py#L413)
  defines caller request ID and `return_token_ids`. The local renderer now accepts
  `return_token_ids` as observational; see [the M4 calibration plan](m4-calibration-plan.md)
  for the template-control and launch-flag checks added on 2026-11-05.
- [`OpenAIServingChat`, lines 608-621 and 1147-1162](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/openai/chat_completion/serving.py#L608)
  returns prompt IDs on the first stream chunk, or in the JSON response. Generated
  IDs are available in choices; verify stop/parser behavior in runtime controls.

## Request correlation and cache counts

- [`EngineServing._base_request_id`, lines 133-142](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/serve/engine/serving.py#L133)
  prefers `X-Request-Id` over the default/body ID. The
  [chat endpoint, lines 320-346](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/openai/chat_completion/serving.py#L320)
  prefixes the base ID and can create subrequests. Start with `n=1` and record
  actual returned IDs; distinguish them from Suez's local ID.
- [`_make_prompt_tokens_details`, lines 95-114](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/openai/chat_completion/serving.py#L95)
  exposes cached tokens only when details are enabled. Final JSON usage is built
  at lines 1103-1125; streaming derives its cached count from the initial output.
  Missing details remain unknown, not zero. Effective launch flags need runtime
  preflight before the planned adapter can rely on these fields.

## Identity, lookup and eligibility

- [`generate_block_hash_extra_keys` / `hash_block_tokens`, lines 640-739](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/core/kv_cache_utils.py#L640)
  combine chained parent hash, exact token IDs and extra identity. Extra keys
  include LoRA, multimodal identity, salt and prompt embeddings. The first
  experiment excludes those features except a fixed explicit cache namespace.
- [`Request.get_max_cache_hit_length`, lines 329-354](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/request.py#L329)
  excludes the final token for logits and respects cache-read/logprob limits.
- [`KVCacheManager.get_computed_blocks`, lines 266-328](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/core/kv_cache_manager.py#L266)
  checks lookup eligibility and delegates the longest-hit search. Do not rely on
  its full-block comments alone: the selected manager implements finer behavior.
- [`FullAttentionManager.find_longest_cache_hit`, lines 739-838](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/core/single_type_kv_cache_manager.py#L739)
  scans chained full blocks, then conditionally extends into an interior hash
  boundary. Hash granularity, allocation size and alignment are different inputs.
  Runtime configuration determines whether that fine-grained path applies.

## Allocation, removal and clearing

- [`BlockPool.get_new_blocks`, lines 708-742](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/core/block_pool.py#L708)
  takes free blocks and removes cached identity as those blocks are reused.
  [`_maybe_evict_cached_block`, lines 771-792](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/core/block_pool.py#L771)
  removes hashes and emits removal events. Duplicate backing entries and
  partial hashes mean one removal is not automatically total loss of a prefix.
- [`BlockPool.reset_prefix_cache`, lines 873-912](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/core/block_pool.py#L873)
  can return false if blocks remain in use, otherwise clears hashes and can emit
  `AllBlocksCleared`. Always validate reset outcome and a cold probe.
- [`KVCacheEvent` definitions, lines 34-149](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/distributed/kv_events.py#L34)
  provide store/remove/clear events, group/hash identity and some token/extra-key
  metadata. Removal has no request ID. The
  [scheduler, lines 2626-2641](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/core/sched/scheduler.py#L2626)
  publishes batches. Publisher configuration and gap-free collection are M4
  preflight checks; no listener was implemented or exercised here.

## Accounting versus actual repeated work

- [`PrefillStats.set`, lines 308-346](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/metrics/stats.py#L308)
  computes prompt minus cache counts. It is not a GPU operation counter.
- [Scheduler lines 1250-1257](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/core/sched/scheduler.py#L1250)
  explicitly initialize first-prefill statistics before preemptions. Repeat
  prefills after preemption are excluded from that initialization. If later runs
  allow preemption, an execution ledger is needed to count repeated prompt work.
- [`FinishedRequestStats` and `RequestStateStats`, lines 258-304](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/metrics/stats.py#L258)
  retain request IDs, preemptions and timing. A
  [stat logger plugin, lines 54-98](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/metrics/loggers.py#L54)
  is a possible observation point if API fields are insufficient; its internal
  stats interfaces are explicitly unstable. No plugin was installed.

## Timing boundary mismatch

[`build_per_request_timing_metrics`, lines 45-97](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/generate/base/serving.py#L45)
computes the API field `time_to_first_token_ms` as first-token timestamp minus
scheduled timestamp. Queue time is separate. Suez should normalize that field
as `engine_schedule_to_first_token_ms`, not arrival-to-content TTFT.

The [finished-stats calculation, lines 598-613](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/v1/metrics/stats.py#L598)
defines prefill as an elapsed scheduled-to-first-token interval and explicitly
includes preemption. It cannot support a pure GPU-compute-time claim.

## Remaining runtime gates

Verify effective flags, exact ID parity, final-token/alignment cases, response
ID correlation, cache-counter semantics, event reliability, overhead, warm-up
and cold-reset behavior. A successful source read establishes candidate
instrumentation paths, not installed compatibility or experimental results.
