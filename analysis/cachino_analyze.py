"""One entry point for Cachino's analysis commands.

  cachino_analyze.py <command> [options]

Commands, each the script of the same purpose in this directory:

  attribute   per-turn split of one session (live_attribution.py)
  concurrent  cross-session attribution of a multi-session run (concurrent_attribution.py)
  template    template-boundary check of a capture (template_check.py)
  curve       predicted reuse and eviction over cache sizes (reuse_curve.py)
  sweep       engine replay of a run at one cache size (cache_sweep.py)
  schedule    scheduling orders, predicted and on the engine (schedule_replay.py)
  overlap     engine replay with recorded timing (overlap_replay.py)
  summary     summary of a seed by session-count matrix (matrix_summary.py)

`cachino_analyze.py <command> -h` shows a command's options. Commands that
render token IDs need the tokenizer environment (see docs/reproduce.md).
"""
import importlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

COMMANDS = {
    "attribute": "live_attribution",
    "concurrent": "concurrent_attribution",
    "template": "template_check",
    "curve": "reuse_curve",
    "sweep": "cache_sweep",
    "schedule": "schedule_replay",
    "overlap": "overlap_replay",
    "summary": "matrix_summary",
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help") or sys.argv[1] not in COMMANDS:
        print(__doc__.strip())
        return 0 if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help") else 2
    name = sys.argv[1]
    module = importlib.import_module(COMMANDS[name])
    # Each command keeps its own argument parser; present it the remaining
    # arguments under the command's name.
    sys.argv = ["cachino_analyze.py " + name] + sys.argv[2:]
    return module.main() or 0


if __name__ == "__main__":
    sys.exit(main())
