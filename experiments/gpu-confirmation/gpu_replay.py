"""Replay a token-ID bundle on a pinned vLLM at several cache sizes and time it.

Self-contained: standard library plus predict.py from the same directory. For
each cache size it starts `vllm serve` on loopback with the measurement flags,
reads the block count from the startup log, predicts every request's cached
tokens with the free-queue model, then replays the bundle's prompts in
recorded order as token-ID completions with max_tokens 1, after a cache reset.
Capture is not involved, so timing is engine and loopback only.

Per request it records engine cached_tokens, prompt-ID parity, the engine's
per-request metrics (time_to_first_token_ms is measured from scheduling) and
the client round trip. The summary gives reuse, eviction, predictor agreement
and a least-squares fit of time to first token against recomputed tokens,
which turns token savings into time on this hardware.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from predict import FreeQueuePredictor  # noqa: E402

OPENER = build_opener(ProxyHandler({}))


def lcp(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def post(base, path, body, timeout=900):
    req = Request(base + path, data=json.dumps(body).encode(), method="POST",
                  headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=timeout) as reply:
        return json.loads(reply.read())


def start_server(args, bundle, kv_bytes, log_path):
    command = [args.vllm, "serve", bundle["replay_model"], "--revision", bundle["replay_revision"],
               "--tokenizer-revision", bundle["replay_revision"], "--host", "127.0.0.1", "--port", str(args.port),
               "--dtype", args.dtype, "--max-model-len", str(args.max_model_len),
               "--kv-cache-memory-bytes", str(kv_bytes), "--enable-prefix-caching",
               "--block-size", str(bundle["block_size"]), "--prefix-caching-hash-algo", "sha256",
               "--default-chat-template-kwargs", '{"enable_thinking": false}',
               "--enable-prompt-tokens-details", "--enable-per-request-metrics"] + args.extra
    env = dict(os.environ, VLLM_SERVER_DEV_MODE="1")
    log = open(log_path, "w")
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
    base = "http://127.0.0.1:%d" % args.port
    deadline = time.monotonic() + args.start_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("server exited; see " + str(log_path))
        try:
            with OPENER.open(base + "/v1/models", timeout=2):
                break
        except (URLError, OSError):
            time.sleep(2)
    else:
        process.terminate()
        raise TimeoutError("server not ready; see " + str(log_path))
    match = re.search(r"KV cache size: ([\d,]+) tokens", Path(log_path).read_text())
    if not match:
        process.terminate()
        raise RuntimeError("KV cache size not found in log")
    return process, base, int(match.group(1).replace(",", "")) // bundle["block_size"]


def predictions(requests, num_blocks, block):
    model, out = FreeQueuePredictor(num_blocks, block), []
    for i, r in enumerate(requests):
        ids, n = r["prompt_ids"], len(r["prompt_ids"])
        same = [lcp(ids, o["prompt_ids"]) for o in requests[:i] if o["session"] == r["session"]]
        out.append({"predicted": model.request(ids, generated=1)["cached_tokens"],
                    "session_bound": min(max(same, default=0), n - 1) // block * block})
    return out


def fit(points):
    """Least squares y = a + b x over (x, y) points; returns (a, b)."""
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    b = sum((x - mx) * (y - my) for x, y in points) / sxx if sxx else 0.0
    return my - b * mx, b


def environment():
    info = {}
    for name, command in (("nvidia_smi", ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]),
                          ("versions", [sys.executable, "-c", "import vllm, torch; print(vllm.__version__, torch.__version__, torch.version.cuda, torch.cuda.is_available())"])):
        try:
            info[name] = subprocess.run(command, capture_output=True, text=True, timeout=120).stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as error:
            info[name] = "unavailable: %s" % error
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", type=Path, default=HERE)
    parser.add_argument("--sizes", type=int, nargs="+", required=True, help="cache sizes in blocks")
    parser.add_argument("--kv-bytes-per-token", type=int, default=114688, help="Qwen3-0.6B/1.7B in FP16")
    parser.add_argument("--rounds", type=int, default=2, help="replays per size, each after a reset")
    parser.add_argument("--vllm", default="vllm")
    parser.add_argument("--dtype", default="float16")
    parser.add_argument("--max-model-len", type=int, default=10240)
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--start-timeout", type=int, default=1200)
    parser.add_argument("--extra", nargs=argparse.REMAINDER, default=[], help="extra vllm serve arguments")
    parser.add_argument("--out", type=Path, required=True, help="new result directory")
    args = parser.parse_args()
    bundle = json.loads((args.bundle / "requests.json").read_text())
    requests, block = bundle["requests"], bundle["block_size"]
    args.out.mkdir(parents=True)
    result = {"environment": environment(), "bundle": bundle["source_run"], "model": bundle["replay_model"],
              "sizes": []}
    points = []
    for size in args.sizes:
        process, base, num_blocks = start_server(args, bundle, size * block * args.kv_bytes_per_token,
                                                 args.out / ("vllm-%d.log" % size))
        try:
            expected = predictions(requests, num_blocks, block)
            rounds = []
            for _ in range(args.rounds):
                if post(base, "/reset_prefix_cache", {}).get("success") is not True:
                    raise RuntimeError("reset failed")
                rows = []
                for r, e in zip(requests, expected):
                    started = time.perf_counter()
                    response = post(base, "/v1/completions", {"model": bundle["replay_model"], "prompt": r["prompt_ids"],
                                                              "max_tokens": 1, "temperature": 0, "return_token_ids": True})
                    rtt = (time.perf_counter() - started) * 1000
                    usage = response.get("usage") or {}
                    metrics = response.get("metrics") or {}
                    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                    rows.append({"prompt_tokens": len(r["prompt_ids"]), "cached": cached, **e,
                                 "parity": response["choices"][0].get("prompt_token_ids") == r["prompt_ids"],
                                 "ttft_ms": metrics.get("time_to_first_token_ms"),
                                 "queue_ms": metrics.get("queue_time_ms"), "client_ms": rtt})
                    if cached is not None and metrics.get("time_to_first_token_ms") is not None:
                        points.append((len(r["prompt_ids"]) - cached, metrics["time_to_first_token_ms"]))
                rounds.append(rows)
        finally:
            process.terminate()
            process.wait(timeout=60)
        first = rounds[0]
        prompt = sum(r["prompt_tokens"] for r in first)
        cached = sum(r["cached"] or 0 for r in first)
        result["sizes"].append({
            "requested_blocks": size, "num_blocks": num_blocks, "requests": len(first), "prompt_tokens": prompt,
            "cached_tokens": cached, "recomputed_tokens": prompt - cached,
            "eviction_tokens": sum(max(r["session_bound"] - (r["cached"] or 0), 0) for r in first),
            "predictor_exact": sum(r["cached"] == r["predicted"] for r in first),
            "parity": sum(r["parity"] for r in first),
            "ttft_ms_total_by_round": [round(sum(r["ttft_ms"] or 0 for r in rows), 1) for rows in rounds],
            "rounds": rounds})
    a, b = fit(points) if points else (None, None)
    result["ttft_fit"] = {"intercept_ms": a, "ms_per_recomputed_token": b, "points": len(points)}
    (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"environment": result["environment"], "ttft_fit": result["ttft_fit"],
                      "sizes": [{k: v for k, v in s.items() if k != "rounds"} for s in result["sizes"]]}, indent=1))


if __name__ == "__main__":
    main()
