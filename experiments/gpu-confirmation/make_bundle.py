"""Write a token-ID replay bundle from one captured multi-session run.

The bundle holds, per captured request in recorded start order: session label,
turn, start offset in seconds, exact prompt token IDs and recorded completion
length. It contains no raw capture bytes, but the IDs decode back to the prompt
text with the public tokenizer, so sharing the bundle discloses that text.
The predictor and replay script are copied in, so the bundle runs without this
repository. Run in the tokenizer environment.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

from concurrent_attribution import load_requests  # noqa: E402
from reconstruct import _load_tokenizer  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--pin", type=Path, required=True, help="tokenizer pin of the captured model")
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--replay-model", required=True, help="model id the GPU replay serves")
    parser.add_argument("--replay-revision", required=True)
    parser.add_argument("--out", type=Path, required=True, help="new bundle directory")
    args = parser.parse_args()
    pin = load_pin(args.pin)
    rows = load_requests(args.run, _load_tokenizer(args.assets, pin)[0], pin)
    base = rows[0]["start"]
    requests = [{"session": r["session"], "turn": r["turn"], "offset_s": round(r["start"] - base, 3),
                 "prompt_ids": r["ids"], "completion_tokens": r["completion_tokens"]} for r in rows]
    bundle = {"schema_version": 1, "source_run": args.run.name, "captured_model": pin["model_id"],
              "captured_revision": pin["revision"], "replay_model": args.replay_model,
              "replay_revision": args.replay_revision, "block_size": 32, "requests": requests}
    args.out.mkdir(mode=0o700, parents=True)
    data = json.dumps(bundle, separators=(",", ":")).encode()
    (args.out / "requests.json").write_bytes(data)
    for source in (ROOT / "analysis/predict.py", HERE / "gpu_replay.py"):
        shutil.copy(source, args.out / source.name)
    manifest = {name: hashlib.sha256((args.out / name).read_bytes()).hexdigest()
                for name in ("requests.json", "predict.py", "gpu_replay.py")}
    (args.out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"requests": len(requests), "prompt_tokens": sum(len(r["prompt_ids"]) for r in requests),
                      "files": manifest}, indent=1))


if __name__ == "__main__":
    main()
