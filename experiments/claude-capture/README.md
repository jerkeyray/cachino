# Bounded Claude Code capture

Native forwarding, trace capture and session admission limits are implemented.
Installed CLI checked: 2.1.172. This is a compatibility capture, not a completed
real-agent research evaluation.

Preview the fixed task and limits without credentials or requests:

```sh
python3 experiments/claude-capture/run.py
```

The opt-in live command builds Suez, starts a separate proxy, loads the local
`.env` key, runs Claude in the dedicated task directory and stops its children:

```sh
python3 experiments/claude-capture/run.py --send
```

It permits at most four upstream calls (including retries, failed calls and
token counting), 128 KiB of input body per call, and requested `max_tokens` at
most 512, with model exactly `deepseek-flash`. Invalid requests are rejected
before forwarding. Accepted bytes are preserved. Client timeout: 120 seconds.
No automatic rerun occurs. These bounds are not a verified provider dollar cap.

Claude gets only Read with a permission rule for `fixture.py` and instructions
to inspect missing-cache-metric handling without edits, commands or delegation.
This is a tool restriction, not an OS sandbox. The fixture is deliberate and
synthetic; it does not establish task success on real repositories.

The runner uses safe mode, empty setting sources and strict empty MCP config.
It disables retries and nonessential traffic, maps auxiliary models to Flash,
and requests output/thinking limits via environment. Proxy admission remains the
enforced boundary if the CLI ignores a setting. Captured requests establish
what actually went upstream.

Manifest includes commands, task/runner hashes, bounds and CLI version without
the credential value. CLI result, stderr, proxy log, capture and derived report
are saved privately under a new ignored run directory. Review failures before
rerunning: each fresh run resets quota and can incur more charges.
New runs also copy an explicit source/task allowlist into `source/` and record
its hashes plus the built binary hash. The runner refuses dispatch if source
files change during the build. No dotenv or trace files enter that snapshot.

To explain an existing capture without another model call:

```sh
python3.14 analysis/audit.py traces/YOUR-RUN/capture --out artifacts/YOUR-AUDIT
```

For the legacy 2026-10-29 run only, add `--response-encoding-override zstd`.
See [observations and limits](../../docs/notes/claude-capture-audit.md).

## Request flow

Claude Code -> local Suez `/v1/messages` -> DeepSeek
`/anthropic/v1/messages` -> original response stream back to Claude Code.
Suez preserves request bytes, API headers and SSE framing; it does not translate
requests between API formats. Token-count requests use the same prefix.

Start a separate proxy with a new capture directory:

```sh
go run ./cmd/suez -listen 127.0.0.1:18081 \
  -upstream https://api.deepseek.com -messages-prefix /anthropic \
  -trace-dir "traces/claude-$(date -u +%Y%m%dT%H%M%SZ)" \
  -session claude-deepseek
```

That command starts only a proxy and does not send a model request. Stop it with
Ctrl-C when finished. Never reuse an existing capture directory.

Once the key is loaded locally from `.env`, process-scoped Claude configuration
will use `ANTHROPIC_BASE_URL=http://127.0.0.1:18081`,
`ANTHROPIC_AUTH_TOKEN=$DEEPSEEK_API_KEY`, and `deepseek-flash` as the explicit model.
Set default Opus/Sonnet/Haiku model mappings explicitly to Flash for that process
so auxiliary requests cannot silently select the more expensive Pro mapping.
Do not change the user's saved login or global settings. Existing settings can
override environment behavior; validate effective configuration before a run.

## Next run requirements

Freeze a dedicated task directory and prompt, restrict tools to those required,
disable unrelated integrations, and record exact CLI flags/environment names
without secrets. The runner starts with read-only code inspection and enforces
request/output limits at the proxy boundary. Claude's cost estimate alone is
not a verified DeepSeek billing cap. CLI retries
and auxiliary calls can exceed a simple count of conversation turns.

Check captured endpoint/model/status, stream `message_stop`, complete body
integrity and native usage. `anthropic_usage` is retained separately; Messages
requests are not passed through the Qwen reconstruction pipeline. A successful
capture does not prove provider rendering identity or serving-engine cache reuse.

Claude Code is useful for an initial compatibility capture. The research plan's
two open-source harnesses and source-level prompt attribution are still pending;
this integration does not fulfill that requirement.

No GPU is needed for this capture stage. Controlled serving-engine cache and
performance validation remains the later GPU gate.

Sources: [DeepSeek Claude Code integration](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/),
[Messages compatibility](https://api-docs.deepseek.com/guides/anthropic_api/),
[Messages stream events](https://platform.claude.com/docs/en/build-with-claude/streaming).
