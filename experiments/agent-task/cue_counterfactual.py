"""Test whether keeping the thinking cue in assistant history lets replies be reused.

The pinned Qwen3 template renders `<think>\\n\\n</think>\\n\\n` after the final
`<|im_start|>assistant\\n` when generating with enable_thinking=false, but drops
it from earlier assistant messages. So each new prompt diverges from the
previous generation context at the cue, and the reply just generated is
prefilled again.

This replays one captured session in two arms, each after a cache reset:

- normal: the captured messages rendered exactly as the pinned template does.
- cue-kept: the same messages, with the cue kept before every history reply.

Both are rendered by hand, then sent as token-ID prompts to /v1/completions with
greedy decoding, so the engine generates each reply and holds its KV as it would
live. The hand renderer must reproduce the pinned template's normal render
exactly, or the script stops. History replies are always the captured text;
the counterfactual changes only the cue.

For each turn this records engine cached_tokens and generated IDs, and compares
them with the free-queue predictor given the actual generated IDs. It also
checks token identity: whether the next prompt really continues the previous
prompt plus the generated IDs, which re-tokenizing reply text does not
guarantee.

Run in the tokenizer environment with the CPU engine listening:
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project analysis/tokenizer --frozen \\
    --python 3.12 --no-python-downloads python -B experiments/agent-task/cue_counterfactual.py \\
    --capture artifacts/live-cpu-NEW/capture --engine http://127.0.0.1:18000 \\
    --num-blocks 585 --block-size 32 --out artifacts/live-cpu-NEW/cue-counterfactual
"""
import argparse
import json
from pathlib import Path
import sys
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "experiments/cache-eviction"), str(HERE)]

from live_attribution import ASSETS, PIN, details, load_session  # noqa: E402
from predict import FreeQueuePredictor  # noqa: E402
from reconstruct import _load_tokenizer, lcp, render  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402

CUE = "<think>\n\n</think>\n\n"


def manual_render(messages, keep_cue):
    """Render plain system/user/assistant messages in the pinned Qwen3 format."""
    text = ""
    for message in messages:
        content = message["content"]
        if message["role"] == "assistant":
            if "<think>" in content or "</think>" in content:
                raise ValueError("reply contains thinking tags; hand render does not model that template branch")
            if keep_cue:
                content = CUE + content
        text += "<|im_start|>%s\n%s<|im_end|>\n" % (message["role"], content)
    return text + "<|im_start|>assistant\n" + CUE


def run_arm(opener, engine, prompts, max_tokens, model_name, num_blocks, block):
    def post(path, body):
        req = Request(engine + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=900) as reply:
            return json.loads(reply.read())

    if post("/reset_prefix_cache", {}).get("success") is not True:
        raise RuntimeError("reset failed")
    model = FreeQueuePredictor(num_blocks, block)
    rows, previous = [], None
    for index, ids in enumerate(prompts):
        response = post("/v1/completions", {"model": model_name, "prompt": ids, "max_tokens": max_tokens,
                                            "temperature": 0, "n": 1, "stream": False, "return_token_ids": True})
        choice = response["choices"][0]
        generated = choice.get("token_ids")
        seen = details(response)
        predicted = model.request(ids, generated=list(generated))
        row = {"index": index, "prompt_tokens": len(ids), **seen, "finish_reason": choice.get("finish_reason"),
               # /v1/completions returns prompt IDs per choice; its
               # created_cache_tokens is null at this build, recorded as unknown.
               "engine_prompt_ids_equal": choice.get("prompt_token_ids") == ids,
               "generated_tokens": len(generated), "generated_text": choice.get("text"),
               "predicted_cached": predicted["cached_tokens"],
               "matches_prediction": seen["cached_tokens"] == predicted["cached_tokens"]}
        if previous is not None:
            prev_ids, prev_generated = previous
            row["lcp_with_previous_prompt"] = lcp(ids, prev_ids)
            # How far this prompt continues the previous generation context.
            row["lcp_with_previous_context"] = lcp(ids, prev_ids + prev_generated)
            row["previous_context_tokens"] = len(prev_ids) + len(prev_generated)
        rows.append(row)
        previous = (ids, list(generated))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--num-blocks", type=int, required=True)
    parser.add_argument("--block-size", type=int, required=True)
    parser.add_argument("--max-tokens", type=int, default=768)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    pin = load_pin(PIN)
    tokenizer = _load_tokenizer(ASSETS, pin)[0]
    rows = load_session(args.capture)
    arms = {"normal": [], "cue-kept": []}
    captured_replies = []
    for request, response in rows:
        text, ids, _ = render(tokenizer, request, pin)
        normal = manual_render(request["messages"], keep_cue=False)
        if normal != text or tokenizer.encode(normal, add_special_tokens=False) != ids:
            raise ValueError("hand render differs from the pinned template")
        arms["normal"].append(ids)
        arms["cue-kept"].append(tokenizer.encode(manual_render(request["messages"], keep_cue=True), add_special_tokens=False))
        captured_replies.append(response["choices"][0]["message"]["content"])
    opener = build_opener(ProxyHandler({}))
    args.out.mkdir(mode=0o700, parents=True)
    result = {"model": pin["model_id"], "arms": {}}
    for name, prompts in arms.items():
        result["arms"][name] = run_arm(opener, args.engine, prompts, args.max_tokens, pin["model_id"],
                                       args.num_blocks, args.block_size)
        for row, reply in zip(result["arms"][name], captured_replies):
            # The completion endpoint's text excludes the stop token, like the chat reply.
            row["generated_equals_captured_reply"] = row["generated_text"] == reply
    (args.out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({name: [{k: r.get(k) for k in ("index", "prompt_tokens", "cached_tokens", "predicted_cached",
                                                    "lcp_with_previous_prompt", "lcp_with_previous_context",
                                                    "previous_context_tokens", "generated_tokens",
                                                    "generated_equals_captured_reply", "engine_prompt_ids_equal")}
                             for r in rows_] for name, rows_ in result["arms"].items()}, indent=1))


if __name__ == "__main__":
    main()
