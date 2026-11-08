"""Attribute per-turn prefix-cache misses in a captured agent session.

For each captured request, in order, this renders the exact local token IDs
with the pinned strict renderer, finds the longest exact-ID prefix shared with
any earlier prompt, and compares three numbers:

- prompt bound: the shared prefix rounded down to whole blocks and capped at
  prompt length - 1. A miss down to this bound is caused by the prompt.
- predicted: the free-queue predictor's hit, which also models eviction and
  the blocks the previous replies occupied. bound - predicted is eviction.
- engine: cached_tokens as reported in the captured response.

By default the predictor gives generated tokens unique IDs, so engine >
predicted would mean the engine reused KV computed during generation.
--reply-ids instead models each reply as its re-tokenized text followed by the
end-of-turn token, which lets a later prompt reuse the reply's full blocks when
the template renders the history exactly as it was generated. Live captures
hold reply text, not generated IDs, so this assumes re-tokenization matches. The script also decodes
the tokens on each side of the first divergence from the previous prompt, so a
template boundary is shown rather than inferred.

On live requests the engine's created_cache_tokens was observed to count prompt
blocks only, while predicted_created also counts reply blocks; compare hits,
not created counts, for live rows.

--replay additionally resends every captured prompt, after a reset, with
return_token_ids and max_tokens 1, to check engine prompt IDs against the local
render. Replay prompts are the captured bodies with only those two fields
changed; the replay cache holds prompts only, which the predictor also models.

Run in the tokenizer environment:
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project analysis/tokenizer --frozen \
    --python 3.12 --no-python-downloads python -B analysis/live_attribution.py \
    --capture artifacts/live-cpu-NEW/capture --num-blocks 585 --block-size 32 --out artifacts/live-cpu-NEW/attribution
"""
import argparse
import json
from pathlib import Path
import sys
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

from predict import FreeQueuePredictor  # noqa: E402
from reconstruct import _load_tokenizer, lcp, render  # noqa: E402
from report import body_bytes, read_events  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402

PIN = ROOT / "experiments/tokenizer/qwen3-0.6b.json"
ASSETS = ROOT / "artifacts/qwen3-0.6b-tokenizer"


def load_session(capture):
    """Return [(request_json, response_json)] for completed 200 exchanges, in order."""
    capture = Path(capture).resolve()  # report.body_bytes checks containment on resolved paths
    records, warnings = read_events(capture)
    if warnings:
        raise ValueError("capture has warnings: %s" % warnings)
    rows = []
    for record in records:
        if record.get("http_status") != 200:
            continue
        pair = []
        for name in ("request", "response"):
            data, integrity = body_bytes(capture, record, name)
            if integrity != "verified":
                raise ValueError("unverified capture body")
            pair.append(json.loads(data))
        rows.append(tuple(pair))
    return rows


def details(response):
    usage = response.get("usage") or {}
    d = usage.get("prompt_tokens_details")
    return {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "cached_tokens": d.get("cached_tokens") if isinstance(d, dict) else None,
            "created_cache_tokens": d.get("created_cache_tokens") if isinstance(d, dict) else None,
            # Engine time from scheduling to first token; None when metrics are off.
            "engine_ttft_ms": (response.get("metrics") or {}).get("time_to_first_token_ms")}


def reply_ids(tokenizer, response):
    """Return the reply's re-tokenized IDs plus end of turn, or None if it did not stop."""
    choice = (response.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    if content is None or choice.get("finish_reason") != "stop":
        return None
    return tokenizer.encode(content, add_special_tokens=False) + [tokenizer.convert_tokens_to_ids("<|im_end|>")]


def attribute(rows, tokenizer, pin, num_blocks, block, generated=True, chat_template=None, use_reply_ids=False):
    """Return per-request attribution rows; generated=False models prompt-only traffic."""
    model = FreeQueuePredictor(num_blocks, block)
    earlier, out = [], []
    previous_reply = None  # (content, completion_tokens) of the previous response
    previous_prompt = 0  # length of the previous prompt, to tell whether its reply was matched
    for index, (request, response) in enumerate(rows):
        ids = render(tokenizer, request, pin, chat_template=chat_template)[1]
        seen = details(response)
        best = max((lcp(ids, other) for other in earlier), default=0)
        adjacent = lcp(ids, earlier[-1]) if earlier else 0
        bound = min(best, len(ids) - 1) // block * block
        # A reply of g tokens leaves KV for prompt + g - 1 tokens. generated=1
        # (also used for max_tokens-1 replay) therefore models the prompt alone.
        g = max(seen["completion_tokens"] or 1, 1) if generated else 1
        if generated and use_reply_ids:
            tail = reply_ids(tokenizer, response)
            # Use the text only when its length agrees with the engine's count.
            if tail is not None and len(tail) == g:
                g = tail
        predicted = model.request(ids, generated=g)
        row = {"index": index, "messages": len(request["messages"]), "prompt_tokens_local": len(ids),
               **seen, "best_lcp": best, "adjacent_lcp": adjacent, "prompt_bound": bound,
               "predicted_cached": predicted["cached_tokens"], "predicted_created": predicted["created_cache_tokens"],
               "eviction_tokens": bound - predicted["cached_tokens"]}
        if earlier:
            prev = earlier[-1]
            row["divergence"] = {"position": adjacent,
                                 "previous_next": tokenizer.decode(prev[adjacent:adjacent + 6], skip_special_tokens=False),
                                 "this_next": tokenizer.decode(ids[adjacent:adjacent + 6], skip_special_tokens=False)}
        # Split the recomputed tokens. The previous reply counts as re-prefilled
        # when it is the last assistant message here; its size is the reply's
        # completion_tokens, which equals its re-rendered length only if the
        # re-tokenized text matches the generated IDs (true where checked).
        assistants = [m["content"] for m in request["messages"] if m["role"] == "assistant"]
        reply_tokens = previous_reply[1] if previous_reply and assistants and assistants[-1] == previous_reply[0] else 0
        if use_reply_ids and best > previous_prompt:
            reply_tokens = 0  # the shared prefix runs into the previous reply's own KV
        row["split"] = {"recomputed": len(ids) - (seen["cached_tokens"] or 0),
                        "previous_reply": reply_tokens,
                        "new_content": len(ids) - best - reply_tokens,
                        # Includes the final prompt token, which is never served from cache.
                        "block_rounding": best - bound,
                        "eviction": bound - predicted["cached_tokens"]}
        choice = (response.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        previous_reply = (content, seen["completion_tokens"] or 0) if content is not None else None
        row["parity_count"] = seen["prompt_tokens"] == len(ids) if seen["prompt_tokens"] is not None else None
        row["matches_prediction"] = (seen["cached_tokens"] == predicted["cached_tokens"]) if seen["cached_tokens"] is not None else None
        out.append(row)
        # With reply IDs, the earlier sequence is everything the request left
        # in KV: the prompt and all but the last generated token.
        earlier.append(ids + g[:-1] if isinstance(g, list) else ids)
        previous_prompt = len(ids)
    return out


def replay(rows, tokenizer, pin, engine, chat_template=None):
    """Resend prompts with return_token_ids and max_tokens 1 after one reset."""
    opener = build_opener(ProxyHandler({}))

    def post(path, body):
        req = Request(engine + path, data=body, method="POST", headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=600) as reply:
            return json.loads(reply.read())

    if post("/reset_prefix_cache", b"").get("success") is not True:
        raise RuntimeError("reset failed")
    results = []
    for request, _ in rows:
        body = dict(request, max_tokens=1, return_token_ids=True)
        response = post("/v1/chat/completions", json.dumps(body, ensure_ascii=False).encode("utf-8"))
        ids = render(tokenizer, request, pin, chat_template=chat_template)[1]
        engine_ids = response.get("prompt_token_ids")
        results.append({"parity": engine_ids == ids, "first_mismatch": None if engine_ids == ids else lcp(engine_ids or [], ids),
                        **details(response)})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--num-blocks", type=int, required=True)
    parser.add_argument("--block-size", type=int, required=True)
    parser.add_argument("--replay", help="engine origin, http://127.0.0.1:PORT")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pin", type=Path, default=PIN, help="tokenizer pin naming the session's model")
    parser.add_argument("--assets", type=Path, default=ASSETS, help="tokenizer snapshot for that pin")
    parser.add_argument("--chat-template", type=Path, help="template file the server was launched with, if not the pinned one")
    parser.add_argument("--reply-ids", action="store_true", help="model replies as re-tokenized text so their blocks can be reused")
    args = parser.parse_args()
    pin = load_pin(args.pin)
    tokenizer = _load_tokenizer(args.assets, pin)[0]
    rows = load_session(args.capture)
    template = args.chat_template.read_text() if args.chat_template else None
    args.out.mkdir(mode=0o700, parents=True)
    live = attribute(rows, tokenizer, pin, args.num_blocks, args.block_size, generated=True, chat_template=template,
                     use_reply_ids=args.reply_ids)
    (args.out / "live.json").write_text(json.dumps(live, indent=2, ensure_ascii=False) + "\n")
    summary = {"requests": len(rows), "live": live}
    if args.replay:
        replayed = replay(rows, tokenizer, pin, args.replay, chat_template=template)
        expected = attribute([(req, {"usage": {"prompt_tokens": r["prompt_tokens"], "completion_tokens": 1,
                                               "prompt_tokens_details": {"cached_tokens": r["cached_tokens"],
                                                                         "created_cache_tokens": r["created_cache_tokens"]}}})
                              for (req, _), r in zip(rows, replayed)], tokenizer, pin, args.num_blocks, args.block_size,
                             generated=True, chat_template=template)
        for row, r in zip(expected, replayed):
            row["engine_parity"] = r["parity"]
            row["first_mismatch"] = r["first_mismatch"]
        (args.out / "replay.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False) + "\n")
        summary["replay"] = expected
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
