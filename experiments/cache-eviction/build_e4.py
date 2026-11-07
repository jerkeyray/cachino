"""Build E4 request bodies, local expected IDs and free-queue predictions.

Prompt A and A-late are taken unchanged from the M4 probe expansion, so their
bytes and IDs equal the calibrated probes. Each flood prompt gets the smallest
number of filler lines that reaches the target length with a token count that
is not a block multiple; the predictor relies on that so the one generated
token cannot change the block count. Predictions are written before any
request is sent, for the block count and block size given here; the runner
refuses a manifest that records different values.

Run with the tokenizer environment, offline:
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project analysis/tokenizer --frozen \
    --python 3.12 --no-python-downloads python -B experiments/cache-eviction/build_e4.py \
    --num-blocks 96 --block-size 32 --out artifacts/e4-probes-NEW
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(ROOT / "experiments/cache-ground-truth"))

from build_probes import DEFAULT_ASSETS, DEFAULT_PIN, body_bytes, expand  # noqa: E402
from predict import predict  # noqa: E402
from reconstruct import _load_tokenizer, lcp, render  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402


def flood_request(spec, number, lines):
    """Return the chat request for one flood prompt with the given line count."""
    text = spec["flood"]
    rows = [text["system_line"].replace("{flood:02d}", f"{number:02d}").replace("{line:03d}", f"{i:03d}").replace("{line}", str(i))
            for i in range(1, lines + 1)]
    return dict(spec["common_request"], messages=[{"role": "system", "content": "\n".join(rows)},
                                                  {"role": "user", "content": text["user"]}])


def build(spec_path, out, num_blocks, block_size, pin_path=DEFAULT_PIN, assets=DEFAULT_ASSETS):
    """Write bodies, IDs, predictions and a summary to a new directory."""
    spec_bytes = Path(spec_path).read_bytes()
    spec = json.loads(spec_bytes)
    m4 = json.loads((Path(spec_path).parent / spec["m4_spec"]).read_text(encoding="utf-8"))
    if m4["common_request"] != spec["common_request"]:
        raise ValueError("E4 and M4 common requests differ")
    pin = load_pin(pin_path)
    if spec["common_request"]["model"] != pin["model_id"]:
        raise ValueError("spec model differs from tokenizer pin")
    tokenizer, manifest, libraries = _load_tokenizer(Path(assets), pin)
    m4_requests = expand(m4)
    bodies, tokens = {}, {}
    for name, source in spec["probes"].items():
        if "from_m4" in source:
            data = body_bytes(m4_requests[source["from_m4"]])
            ids = render(tokenizer, json.loads(data), pin)[1]
        else:
            lines = 1
            while True:
                data = body_bytes(flood_request(spec, source["flood"], lines))
                ids = render(tokenizer, json.loads(data), pin)[1]
                if len(ids) >= spec["flood"]["target_min_tokens"] and len(ids) % block_size:
                    break
                lines += 1
        if len(ids) % block_size == 0:
            raise ValueError(name + " length is a block multiple")
        bodies[name], tokens[name] = data, ids
    # Floods must share no full block with any other prompt, or a "flood" could
    # produce hits and the eviction count would no longer be the variable.
    names = list(tokens)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            if (left.startswith("flood") or right.startswith("flood")) and lcp(tokens[left], tokens[right]) >= block_size:
                raise ValueError(f"{left} and {right} share a full block")
    predictions = predict(spec["sequence"], tokens, num_blocks, block_size)
    out = Path(out)
    out.mkdir(mode=0o700, parents=True)
    rows = {}
    for name in names:
        (out / (name + ".request.json")).write_bytes(bodies[name])
        (out / (name + ".tokens.json")).write_text(json.dumps(tokens[name]) + "\n")
        rows[name] = {"request_sha256": hashlib.sha256(bodies[name]).hexdigest(), "prompt_tokens": len(tokens[name]),
                      "token_ids_sha256": hashlib.sha256(json.dumps(tokens[name], separators=(",", ":")).encode()).hexdigest()}
    summary = {"schema_version": 1, "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(), "pin": pin,
               "assets": manifest, "libraries": libraries,
               "template_sha256": hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
               "engine_validated": False, "probes": rows,
               "prediction_params": {"num_gpu_blocks": num_blocks, "block_size_tokens": block_size},
               "predictions": predictions,
               "local_lcp_tokens": {"a|a-late": lcp(tokens["a"], tokens["a-late"])}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", default=HERE / "e4.json", type=Path)
    parser.add_argument("--num-blocks", required=True, type=int, help="engine num_gpu_blocks, including the null block")
    parser.add_argument("--block-size", required=True, type=int)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    summary = build(args.spec, args.out, args.num_blocks, args.block_size)
    print(json.dumps({"probes": summary["probes"], "predictions": summary["predictions"]}, indent=2))


if __name__ == "__main__":
    main()
