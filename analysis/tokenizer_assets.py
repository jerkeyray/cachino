"""Fetch only the pinned tokenizer assets; never model weights or remote code."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import quote
from urllib.request import urlopen

DEFAULT_PIN = Path(__file__).resolve().parent.parent / "experiments/tokenizer/model.json"
FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt")


def load_pin(path=DEFAULT_PIN):
    """Load an immutable model revision and explicit template switches."""
    pin = json.loads(Path(path).read_text())
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", pin["model_id"]) or not re.fullmatch(r"[0-9a-f]{40}", pin["revision"]):
        raise ValueError("model ID and full revision SHA required")
    for key in ("add_generation_prompt", "enable_thinking"):
        if type(pin[key]) is not bool:
            raise ValueError("explicit boolean template flags required")
    return pin


def fetch(out, pin):
    """Create a tokenizer-only snapshot and hash manifest at a new private path.

    The fixed allowlist excludes all weight files. Failed downloads remove only
    the directory created by this call; existing paths are never overwritten.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir(mode=0o700)
    try:
        hashes = {}
        for name in FILES:
            url = "https://huggingface.co/%s/resolve/%s/%s" % (quote(pin["model_id"], safe="/"), pin["revision"], name)
            digest, size = hashlib.sha256(), 0
            path = out / name
            with urlopen(url, timeout=30) as response, path.open("xb") as file:
                path.chmod(0o600)
                while True:
                    block = response.read(1 << 20)
                    if not block:
                        break
                    size += len(block)
                    if size > 32 << 20:
                        raise ValueError("tokenizer asset exceeds 32 MiB limit")
                    file.write(block)
                    digest.update(block)
            hashes[name] = digest.hexdigest()
        manifest = {"schema_version": 1, "model_id": pin["model_id"], "revision": pin["revision"], "files": hashes}
        path = out / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        path.chmod(0o600)
    except BaseException:
        shutil.rmtree(out)
        raise
    return manifest


def verify_assets(root, pin):
    """Verify a local snapshot's revision and every allowed file before loading it."""
    root = Path(root).resolve()
    # AutoTokenizer may load optional files such as chat_template.jinja or
    # added_tokens.json. Reject anything outside the manifest's exact snapshot
    # so the loader cannot consume an unrecorded template/token override.
    expected_files = set(FILES) | {"manifest.json"}
    entries = list(root.iterdir())
    if {path.name for path in entries} != expected_files or any(path.is_symlink() or not path.is_file() for path in entries):
        raise ValueError("tokenizer snapshot contains missing, untracked or nonregular files")
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("schema_version") != 1 or any(manifest.get(key) != pin[key] for key in ("model_id", "revision")):
        raise ValueError("tokenizer snapshot does not match the model pin")
    if set(manifest["files"]) != set(FILES):
        raise ValueError("tokenizer snapshot has an unexpected file inventory")
    for name, expected in manifest["files"].items():
        path = root / name
        if root not in path.resolve().parents or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("tokenizer asset integrity mismatch: " + name)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pin", type=Path, default=DEFAULT_PIN)
    args = parser.parse_args()
    try:
        manifest = fetch(args.out, load_pin(args.pin))
        print("Downloaded %d tokenizer assets at revision %s; no weights." % (len(manifest["files"]), manifest["revision"]))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, "tokenizer setup failed: %s\n" % error)


if __name__ == "__main__":
    main()
