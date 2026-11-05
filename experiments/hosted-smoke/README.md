# Hosted protocol smoke test

Stage: Mac CPU capture preparation. This sends two synthetic requests through
Suez to DeepSeek, one JSON and one SSE. It is not a cache experiment, real agent
session, token-identity validation, latency benchmark or GPU measurement.

Preview without a key or network access:

```sh
python3 analysis/hosted_smoke.py
```

## Live run

Requires API credit and a key. Running `--send` explicitly sends up to two
billable requests. There are no automatic retries or redirects. Each request
asks for at most 16 output tokens, with thinking disabled and a fixed short
prompt. The client reads at most 1 MiB per response and uses a 30-second socket
timeout, which is not a total wall-time or billing limit. Cancellation does not
guarantee that upstream work stops immediately.

In terminal 1, start a freshly built Suez with a new private capture directory:

```sh
go run ./cmd/suez -upstream https://api.deepseek.com \
  -trace-dir "traces/hosted-$(date -u +%Y%m%dT%H%M%SZ)"
```

In terminal 2, enter the API key without placing its value in shell history
(these input commands are for the Mac's zsh):

```sh
read -rs 'DEEPSEEK_API_KEY?DeepSeek API key: '
printf '\n'
export DEEPSEEK_API_KEY
python3 analysis/hosted_smoke.py --send
unset DEEPSEEK_API_KEY
```

Do not paste the key into chat. The client sends it in the Authorization header
to the local proxy, which forwards it upstream. Suez does not persist that
header. The client bypasses environment HTTP proxies and accepts only an
explicit `http://127.0.0.1:PORT` origin. Check terminal 1's upstream before sending.

After the client exits, stop Suez with Ctrl-C, then substitute its capture path:

```sh
python3 analysis/report.py traces/hosted-TIMESTAMP \
  --sqlite traces/hosted-TIMESTAMP/index.sqlite \
  > traces/hosted-TIMESTAMP/report.json
```

Inspect two completed integrity-verified captures, HTTP status, parse/error
flags and `[DONE]` on the stream. Missing cache counts stay null and are not a
failure of this smoke test. No positive cache hit is required for these short
requests. Any failed request stops dispatch; inspect the retained capture before
deciding whether to run again. A new invocation may incur additional charges.

Raw captures are ignored and private. Provider usage is a separate domain from
local Qwen reconstruction. Do not remap this smoke test to Qwen as evidence of
DeepSeek's tokens or serving internals.

Protocol references: [DeepSeek cache fields](https://api-docs.deepseek.com/guides/kv_cache/)
and [thinking toggle](https://api-docs.deepseek.com/guides/thinking_mode/).
