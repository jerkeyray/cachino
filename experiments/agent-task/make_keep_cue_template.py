"""Derive a Qwen3 chat template that keeps the empty thinking cue in history.

With enable_thinking false, the pinned Qwen3 template ends the generation prompt
with an empty `<think></think>` cue but renders earlier assistant replies
without it, so each new prompt diverges from the previous generation context.
This writes a copy of the pinned template in which assistant replies without
reasoning are rendered with the same cue when thinking is off. Nothing else
changes. The edit is exact string replacement and fails if the template text
differs from the pinned one.
"""
import argparse
import hashlib
import json
from pathlib import Path

PLAIN = "{{- '<|im_start|>' + message.role + '\\n' + content }}"
WITH_CUE = ("{%- if enable_thinking is defined and enable_thinking is false and not reasoning_content %}"
            "{{- '<|im_start|>' + message.role + '\\n<think>\\n\\n</think>\\n\\n' + content }}"
            "{%- else %}" + PLAIN + "{%- endif %}")


def keep_cue(template):
    """Return the template with both history renderings keeping the cue."""
    if template.count(PLAIN) != 2:
        raise ValueError("unexpected template: history rendering not found exactly twice")
    return template.replace(PLAIN, WITH_CUE)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--assets", type=Path, required=True, help="pinned Qwen3 tokenizer snapshot")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    template = json.loads((args.assets / "tokenizer_config.json").read_text())["chat_template"]
    modified = keep_cue(template)
    args.out.write_text(modified)
    print(json.dumps({"source_sha256": hashlib.sha256(template.encode()).hexdigest(),
                      "modified_sha256": hashlib.sha256(modified.encode()).hexdigest()}))


if __name__ == "__main__":
    main()
