# Tokenizer pins

Each JSON file pins one model's Hugging Face revision and the chat-template
switches the study used. `analysis/tokenizer_assets.py` downloads only that
revision's tokenizer and config files (no weights) into a new directory with a
hash manifest, and `analysis/reconstruct.py` refuses snapshots that do not
match their pin.

| Pin | Model | Use |
| --- | --- | --- |
| `qwen3-0.6b.json` | Qwen3-0.6B at `c1899de2` | Calibration, eviction, replays |
| `qwen3-1.7b.json` | Qwen3-1.7B at `70d244cc` | Varied and concurrent sessions |
| `qwen2.5-0.5b-instruct.json` | Qwen2.5-0.5B-Instruct at `7ae55760` | Control template |
| `model.json` | Qwen3-8B at `b968826d` | Original pin; its tokenizer files equal the Qwen3-0.6B and 1.7B ones |

```sh
uv run --project analysis --offline --no-python-downloads python analysis/tokenizer_assets.py \
  --pin experiments/tokenizer/qwen3-0.6b.json --out artifacts/qwen3-0.6b-tokenizer
```

The tokenizer environment (`analysis/tokenizer`) fixes Transformers 4.57.6 and
Jinja2 3.1.6 in `uv.lock`; run it with `--frozen`.
