# M4 engine calibration package

Status: prepared on CPU on 2026-11-05. Nothing here has run against an engine.
No vLLM install, model weights or GPU were used. This note explains what the
calibration run must establish, what the pinned source says about the switches
it depends on, and which local pieces exist. It extends the
[prefill measurement contract](prefill-measurement-contract.md) and
[pinned source paths](vllm-source-paths.md).

## What calibration must establish

Before any cache attribution, one run on one vLLM worker must show:

1. **Token parity.** For every probe, the engine's returned `prompt_token_ids`
   equal the local Qwen IDs element by element. Equal counts are not enough.
2. **Reset and cold behavior.** A successful prefix-cache reset is followed by a
   request that reports `cached_tokens` of 0. Missing details block hit claims.
3. **Warm repeat.** The same request again reports a positive hit count no larger
   than prompt length minus one (the final prompt token is not cache eligible).
4. **Mutation boundaries.** After an early or late change, the hit count stays
   within the locally computed exact-ID boundary, and the gap is explained by the
   engine's recorded block geometry rather than an assumed formula.
5. **History boundary.** A follow-up turn replaces the template's empty thinking
   cue with an assistant reply, so reuse must stop at that boundary.
6. **Direct versus proxy.** The same request through Suez returns the same IDs
   and cold behavior. Latency overhead needs repeated pairs, not one request.

## Source-checked switches at vLLM `2173d712`

Read-only inspection on 2026-11-05. Blob SHAs are in
[source-pin.json](../../experiments/cache-ground-truth/source-pin.json).

| Need | Source finding | Consequence |
| --- | --- | --- |
| Exact engine prompt IDs | [`return_token_ids`, protocol.py L438-447](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/openai/chat_completion/protocol.py#L438) adds IDs to the response; it is not a chat-template input | Accepted by the local renderer as observational |
| Thinking setting on the server | [`build_chat_params`, protocol.py L645-685](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/openai/chat_completion/protocol.py#L645) merges request `chat_template_kwargs` under the `add_generation_prompt` field; [`with_defaults`, params.py L117-136](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/renderers/params.py#L117) puts launch defaults beneath both; the [render path, online_renderer.py L802-808](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/renderers/online_renderer.py#L802) uses that merge | Probes state `enable_thinking: false` and `add_generation_prompt: true` themselves; launch defaults are recorded in the manifest |
| Cached-token counts | [`enable_prompt_tokens_details`, cli_args.py L156-157](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/common/cli_args.py#L156) defaults to false; without it `prompt_tokens_details` is absent | Required launch setting; absence is unknown, not zero |
| Engine timing | [`enable_per_request_metrics`, cli_args.py L158-159](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/common/cli_args.py#L158) defaults to false | Required launch setting; field boundaries as in the contract |
| Cache reset | [`POST /reset_prefix_cache`, dev cache router L20-45](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/serve/dev/cache/api_router.py#L20) returns `{"success": bool}` and fails while blocks are held; it is registered only when [`VLLM_SERVER_DEV_MODE`, routers.py L46-49](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/entrypoints/launchers/api_server/routers.py#L46) is set | Dev mode on a private loopback server only; check `success`, then a cold probe |

The render path also has a shortcut that feeds `kv_transfer_params` prompt IDs
straight to the engine for disaggregated serving. The local renderer rejects
that field, so calibration requests cannot take it.

Not yet source-checked: the launch defaults and runtime reporting of prefix
caching, block size, hash block size and hash algorithm. The manifest leaves
them as missing values to be read from the actual server, not from documentation.

## Local pieces

- `analysis/reconstruct.py` accepts `return_token_ids` (boolean only),
  `chat_template_kwargs` containing only `enable_thinking`, and
  `add_generation_prompt`, and rejects values that differ from the pin. Rows
  record whether each template setting was explicit in the request. Other vLLM
  extras such as `cache_salt`, `chat_template`, `echo` and
  `continue_final_message` stay rejected.
- [`probes.json`](../../experiments/cache-ground-truth/probes.json) defines five
  probe requests, the exact execution order with resets, acceptance criteria and
  stop rules. Every step is sequential with no retries.
- [`build_probes.py`](../../experiments/cache-ground-truth/build_probes.py) writes
  the exact request bytes and their local expected IDs to a new directory.
- [`run-manifest.template.json`](../../experiments/cache-ground-truth/run-manifest.template.json)
  lists hardware, install, model, cache, scheduling, telemetry and access values
  as nulls. [`check_manifest.py`](../../experiments/cache-ground-truth/check_manifest.py)
  reports which remain missing and which recorded values contradict the
  measurement model, such as more than one worker or disabled cache details.

## Local expectations from the first build

Built offline with the pinned tokenizer (`artifacts/m4-probes-20261105-01/`,
private and regenerable). These are local predictions only.

| Probe | Local prompt tokens |
| --- | --- |
| parity-short | 13 |
| base, early-mutation, late-mutation | 1091 each |
| turn-2 | 1124 |

| Comparison | Local exact-ID LCP | Where they diverge |
| --- | --- | --- |
| base vs early-mutation | 30 | `describe` vs `summarize` in rule 02 |
| base vs late-mutation | 1080 | `why` vs `when` at the end of the question |
| base vs turn-2 | 1087 | empty thinking cue vs the assistant reply |

The probes use a fixed assistant reply, not the model's generated output, so
the history probe tests the rendering boundary, not real agent cache state.

## Remaining before a run

1. A small stdlib calibration client that sends the built bodies byte for byte
   with an `X-Request-Id` header, follows the sequence and stop rules, stores
   raw responses and compares returned IDs and counts. Not yet written.
2. Actual hardware and access: a CUDA GPU with memory for Qwen3-8B, a vLLM install
   matching the source pin, and the model weights. These need your approval for
   the access method, time and spending ceiling before anything is rented,
   installed or downloaded.
