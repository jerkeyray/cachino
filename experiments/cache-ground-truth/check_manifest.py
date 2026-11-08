"""Report which M4 calibration gates a run manifest still leaves open.

The manifest records the actual machine, engine install, model and launch
configuration of one calibration run. This checker does not contact a server or
verify the values; it only reports which fields are still unknown and which
recorded values contradict the first measurement model in
docs/records/prefill-measurement-contract.md. A clean result means the manifest is
complete enough to run the probes, not that the engine behaves as recorded.
"""
import argparse
import json
from pathlib import Path
import sys

# Values the first calibration model depends on. Each comes from the pinned
# vLLM source read recorded in docs/records/m4-calibration-plan.md:
# cached-token details and per-request timing are off unless enabled, the reset
# route exists only in dev mode, and one worker keeps a single cache namespace.
REQUIRED = {
    ("engine", "installed_matches_source_pin"): True,
    ("topology", "tensor_parallel_size"): 1,
    ("topology", "pipeline_parallel_size"): 1,
    ("topology", "data_parallel_size"): 1,
    ("cache", "prefix_caching_enabled"): True,
    ("cache", "cpu_offload_or_external_connector"): False,
    ("scheduling", "speculative_decoding"): False,
    ("telemetry", "enable_prompt_tokens_details"): True,
    ("telemetry", "enable_per_request_metrics"): True,
    ("telemetry", "vllm_server_dev_mode"): True,
}

# KV events are needed for later residency work, not for parity, reset or warm
# probes. Recording "disabled" is acceptable; leaving it unknown is not.
EXPLICIT_DISABLED_OK = {("telemetry", "kv_events_config")}


def check(manifest):
    """Return (missing, conflicts) lists of dotted field paths with reasons.

    Missing fields are nulls. Conflicts are recorded values that differ from a
    requirement, plus launch template defaults other than enable_thinking.
    Request chat_template_kwargs override launch defaults at the inspected pin,
    so a default enable_thinking is overridden by the probe's explicit value;
    other default keys could change rendering and need their own validation.
    """
    missing, conflicts = [], []
    for section, fields in manifest.items():
        if not isinstance(fields, dict):
            continue
        for key, value in fields.items():
            if value is None:
                missing.append(section + "." + key)
    for (section, key), expected in REQUIRED.items():
        value = manifest.get(section, {}).get(key)
        if value is not None and value != expected:
            conflicts.append(f"{section}.{key}: recorded {value!r}, requires {expected!r}")
    for section, key in EXPLICIT_DISABLED_OK:
        value = manifest.get(section, {}).get(key)
        if value is not None and value != "disabled" and not isinstance(value, dict):
            conflicts.append(f"{section}.{key}: use an object or the string 'disabled'")
    defaults = manifest.get("rendering", {}).get("default_chat_template_kwargs")
    if defaults is not None and not isinstance(defaults, dict):
        conflicts.append("rendering.default_chat_template_kwargs: must be an object, {} when unset")
    elif isinstance(defaults, dict) and set(defaults) - {"enable_thinking"}:
        conflicts.append("rendering.default_chat_template_kwargs: unvalidated keys " + ", ".join(sorted(set(defaults) - {"enable_thinking"})))
    return missing, conflicts


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    missing, conflicts = check(json.loads(args.manifest.read_text(encoding="utf-8")))
    print(json.dumps({"ready": not missing and not conflicts, "missing": missing, "conflicts": conflicts}, indent=2))
    return 0 if not missing and not conflicts else 1


if __name__ == "__main__":
    sys.exit(main())
