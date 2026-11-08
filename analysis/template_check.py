"""Flag consecutive agent prompts that diverge at a chat-template boundary.

Each captured prompt ends with the template's generation prompt (for example
`<|im_start|>assistant\\n`, plus an empty thinking cue for Qwen3 with thinking
off). When the next prompt keeps the previous one as an exact prefix, history
is append-only and the previous reply's KV can be reused. When it diverges
inside that generation suffix, the template renders history differently from
how it prompted generation, so the reply just generated is prefilled again.
When it diverges there because the harness did not append the reply at all
(for example, a reply rejected for its format), the reply was dropped, not
re-rendered. When it diverges earlier, history itself changed: an edited
message, compaction or reordering.

The generation suffix is measured, not assumed: each request is rendered with
and without the generation prompt, and the extra tokens are the suffix. Works
on one capture directory with any number of session labels.

Run in the tokenizer environment:
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project analysis/tokenizer --frozen \\
    --python 3.12 --no-python-downloads python -B analysis/template_check.py \\
    --capture artifacts/RUN/capture --pin experiments/tokenizer/qwen3-0.6b.json \\
    --assets artifacts/qwen3-0.6b-tokenizer
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

APPEND_ONLY = "append-only"
GENERATION_PROMPT_DROPPED = "generation-prompt-dropped"
REPLY_DROPPED = "reply-dropped"
HISTORY_CHANGED = "history-changed"


def lcp(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def classify(previous, generation_start, current, reply_kept=True):
    """Classify one transition from previous to current prompt token IDs.

    generation_start is where the previous prompt's generation suffix begins;
    reply_kept says whether the current prompt's messages include the reply to
    the previous prompt. Returns the category, the divergence position and how
    many of the previous prompt's tokens the current prompt does not share.
    """
    shared = lcp(previous, current)
    if shared == len(previous):
        category = APPEND_ONLY
    elif shared >= generation_start:
        category = GENERATION_PROMPT_DROPPED if reply_kept else REPLY_DROPPED
    else:
        category = HISTORY_CHANGED
    return {"category": category, "divergence": shared, "previous_unshared": len(previous) - shared}


def summarize(transitions):
    """Count transitions and unshared previous-prompt tokens per category."""
    out = {}
    for t in transitions:
        entry = out.setdefault(t["category"], {"transitions": 0, "previous_unshared_tokens": 0})
        entry["transitions"] += 1
        entry["previous_unshared_tokens"] += t["previous_unshared"]
    return out


def generation_suffix_start(tokenizer, request, pin, ids):
    """Return where the generation prompt starts in ids, by rendering without it."""
    from reconstruct import prompt_inputs
    messages, tools = prompt_inputs(request, pin["model_id"])
    options = {"add_generation_prompt": False, "enable_thinking": pin["enable_thinking"]}
    if tools is not None:
        options["tools"] = tools
    without = tokenizer.apply_chat_template(messages, tokenize=True, **options)
    if ids[:len(without)] != without:
        raise ValueError("prompt without generation suffix is not a prefix of the full prompt")
    return len(without)


def check_capture(capture, tokenizer, pin):
    """Return per-transition rows for every session in one capture directory."""
    from reconstruct import render
    from report import body_bytes, read_events
    capture = Path(capture).resolve()
    records, warnings = read_events(capture)
    if warnings:
        raise ValueError("capture has warnings")
    previous, rows = {}, []
    for record in records:
        if record.get("http_status") != 200:
            continue
        data, integrity = body_bytes(capture, record, "request")
        if integrity != "verified":
            raise ValueError("unverified request body")
        request = json.loads(data)
        ids = render(tokenizer, request, pin)[1]
        start = generation_suffix_start(tokenizer, request, pin, ids)
        session = record.get("session_id")
        messages = request["messages"]
        if session in previous:
            prev_ids, prev_start, turn, prev_count = previous[session]
            # The reply was kept if the message right after the previous prompt's
            # history is an assistant message.
            kept = len(messages) > prev_count and messages[prev_count].get("role") == "assistant"
            row = {"session": session, "turn": turn + 1, **classify(prev_ids, prev_start, ids, kept)}
            at = row["divergence"]
            row["previous_next"] = tokenizer.decode(prev_ids[at:at + 6], skip_special_tokens=False)
            row["current_next"] = tokenizer.decode(ids[at:at + 6], skip_special_tokens=False)
            rows.append(row)
            previous[session] = (ids, start, turn + 1, len(messages))
        else:
            previous[session] = (ids, start, 0, len(messages))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--pin", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--rows", action="store_true", help="print every transition, not just the summary")
    args = parser.parse_args(argv)
    from reconstruct import _load_tokenizer
    from tokenizer_assets import load_pin
    pin = load_pin(args.pin)
    rows = check_capture(args.capture, _load_tokenizer(args.assets, pin)[0], pin)
    result = {"transitions": len(rows), "summary": summarize(rows)}
    if args.rows:
        result["rows"] = rows
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
