# Second harness: smolagents, 2026-11-08

Question: do the single-session findings hold for a harness with native tool
calls, and does the renderer reproduce tool-calling prompts exactly?
Summarized in [report 3.4](../report.md#34-a-second-harness).

smolagents 1.26.0 (`ToolCallingAgent`) ran the same task with Qwen3-1.7B and
read-only tools (list a directory, read lines, search) backed by the same
snapshot, through Cachino, with vLLM parsing Qwen3's tool calls (run
`smol-cpu-20261108-01`, `experiments/agent-task/smol_cpu.py`). The agent made
five tool calls in six steps and ended with a final answer.

- The renderer reproduced the engine's prompt IDs exactly for all five
  tool-calling requests (tool definitions, `tool_choice: required`, tool-call
  history), and the predictor was exact on every request.
- Its fixed opening, a system prompt plus tool definitions, is about 1,700
  tokens, twice mini-swe-agent's.
- Every turn again diverged at Qwen3's empty thinking cue (4 of 4 transitions).
- smolagents does not replay the model's tool call. It writes each call into
  history as a Python-style text dump that includes the server's random call
  ID: 52 to 66 tokens for a call the model generated in 19 to 36. So even with
  a template that kept the cue, this harness's replies could not be reused, and
  identical calls never produce identical history.

## Sampled sessions

Three more sessions sampled at temperature 0.7 under engine seeds 1 to 3 (run
`smol-matrix-20261108`) made 8, 5 and 5 requests. The predictor was exact on
all 18, and all 15 of their transitions dropped the cue. Recomputation was
2,543 to 2,618 tokens per session, almost all new content (block rounding 43 to
97 tokens, no eviction), so a longer session added little: the long fixed
opening was reused and each turn's new text was small.
