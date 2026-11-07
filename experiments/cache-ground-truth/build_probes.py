"""Build exact M4 probe request bodies and their local expected token IDs.

Reads probes.json, expands its fixed text into chat requests, and writes each
request body as the exact bytes a calibration client will later send unchanged.
Each body is rendered with the pinned local Qwen tokenizer through
analysis/reconstruct.py, so the expected IDs come from the same strict renderer
used for captured traffic. The output is a local prediction for comparison with
engine-returned prompt_token_ids. It says nothing about what the engine renders,
caches or reuses until a run shows parity.

The spec's model must equal the tokenizer pin's model, so a probe set never
pairs one model's request with another model's tokenizer snapshot.

Run with the tokenizer environment, offline:
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 analysis/tokenizer/.venv/bin/python -B \
    experiments/cache-ground-truth/build_probes.py --out artifacts/m4-probes-NEW
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis"))

from reconstruct import _load_tokenizer, lcp, render  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402

# M4 runs on the CPU backend with Qwen3-0.6B (docs/notes/m4-cpu-build-plan.md).
DEFAULT_PIN = ROOT / "experiments/tokenizer/qwen3-0.6b.json"
DEFAULT_ASSETS = ROOT / "artifacts/qwen3-0.6b-tokenizer"


def expand(spec):
    """Return {probe_name: request_dict} with all text placeholders filled.

    Placeholders are filled by exact string replacement on message content,
    not by format(), so user-visible braces in the text cannot be reinterpreted.
    The early mutation must change exactly one occurrence in one rule line.
    """
    text = spec["text"]
    lines = [text["rule_line"].replace("{index:02d}", f"{i:02d}") for i in range(1, text["rule_count"] + 1)]
    early = list(lines)
    change = text["early_mutation"]
    target = early[change["rule_index"] - 1]
    if target.count(change["find"]) != 1:
        raise ValueError("early mutation must match exactly once in its rule line")
    early[change["rule_index"] - 1] = target.replace(change["find"], change["replace"])
    values = {"{rules}": "\n".join(lines), "{rules_early}": "\n".join(early)}
    for key in ("base_question", "late_question", "assistant_reply", "followup"):
        values["{" + key + "}"] = text[key]
    probes = {}
    for name, probe in spec["probes"].items():
        messages = []
        for message in probe["messages"]:
            raw = message["content"]
            if raw.startswith("{") and raw.endswith("}") and raw not in values:
                raise ValueError("unknown placeholder " + raw + " in " + name)
            messages.append({"role": message["role"], "content": values.get(raw, raw)})
        probes[name] = dict(spec["common_request"], messages=messages)
    return probes


def body_bytes(request):
    """Serialize one request deterministically; these bytes are the probe."""
    return json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build(spec_path, assets, out, pin_path=DEFAULT_PIN):
    """Write bodies, local IDs and a summary to a new directory.

    The output directory must not exist, so an earlier probe set is never
    overwritten. Comparisons report the first differing position and the decoded
    tokens on each side, which is where an engine hit count must stop if the
    engine renders the same IDs.
    """
    spec_bytes = Path(spec_path).read_bytes()
    spec = json.loads(spec_bytes)
    pin = load_pin(pin_path)
    if spec["common_request"]["model"] != pin["model_id"]:
        raise ValueError("spec model %s differs from tokenizer pin %s" % (spec["common_request"]["model"], pin["model_id"]))
    tokenizer, manifest, libraries = _load_tokenizer(Path(assets), pin)
    out = Path(out)
    out.mkdir(mode=0o700, parents=True)
    rendered, rows = {}, {}
    for name, request in expand(spec).items():
        data = body_bytes(request)
        _, ids, digest = render(tokenizer, json.loads(data), pin)
        (out / (name + ".request.json")).write_bytes(data)
        (out / (name + ".tokens.json")).write_text(json.dumps(ids) + "\n")
        rendered[name] = ids
        rows[name] = {"request_sha256": hashlib.sha256(data).hexdigest(), "prompt_tokens": len(ids),
                      "token_ids_sha256": digest, "max_cache_eligible_tokens": len(ids) - 1}
    comparisons = []
    for left, right in spec["comparisons"]:
        shared = lcp(rendered[left], rendered[right])
        def tail(ids):
            return tokenizer.decode(ids[shared:shared + 4], skip_special_tokens=False) if shared < len(ids) else None
        comparisons.append({"left": left, "right": right, "local_lcp_tokens": shared,
                            "left_next_text": tail(rendered[left]), "right_next_text": tail(rendered[right])})
    summary = {"schema_version": 1, "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(), "pin": pin,
               "assets": manifest, "libraries": libraries,
               "template_sha256": hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
               "engine_validated": False, "probes": rows, "comparisons": comparisons}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", default=Path(__file__).with_name("probes.json"), type=Path)
    parser.add_argument("--pin", default=DEFAULT_PIN, type=Path)
    parser.add_argument("--assets", default=DEFAULT_ASSETS, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    summary = build(args.spec, args.assets, args.out, args.pin)
    print(json.dumps({"probes": summary["probes"], "comparisons": summary["comparisons"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
