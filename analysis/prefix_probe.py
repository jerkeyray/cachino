"""Measure local token-prefix changes on controlled CPU-only prompt fixtures.

This prepares inputs for later engine experiments. No inference request is sent;
shared-prefix length is not an observation of cache hits, residency or latency.
"""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path

from reconstruct import UnsupportedRequest, _load_tokenizer, lcp, render
from tokenizer_assets import DEFAULT_PIN, load_pin


def build_cases(pin, lines=128):
    """Build fixed requests with named baselines and one declared change each.

    Mutation locations are character offsets in the user message; actual token
    divergence is measured after rendering rather than assumed from those offsets.
    """
    if lines < 3:
        raise ValueError("at least three lines are needed for distinct mutation positions")
    paragraphs = ["record %04d: alpha beta gamma delta epsilon zeta eta theta iota kappa.\n" % number for number in range(lines)]
    text = "".join(paragraphs) + "Reply with OK."
    base = {"model": pin["model_id"], "messages": [
        {"role": "system", "content": "Run label: ALPHA. Read the supplied text."},
        {"role": "user", "content": text}], "max_tokens": 1, "stream": False}
    cases = []

    def add(name, request, baseline=None, change=None, profile=None, unsupported=False):
        cases.append({"name": name, "request": request, "baseline": baseline,
                      "change": change, "profile": profile or pin, "expected_unsupported": unsupported})

    add("plain_base", base)
    add("identical_repeat", copy.deepcopy(base), "plain_base", "exact repeat")
    controls = copy.deepcopy(base)
    controls.update(stream=True, temperature=0.7, max_tokens=8)
    add("generation_controls", controls, "plain_base", "change stream, temperature and max_tokens only")
    early = copy.deepcopy(base)
    early["messages"][0]["content"] = early["messages"][0]["content"].replace("ALPHA", "BRAVO")
    add("early_system_label", early, "plain_base", "replace ALPHA with BRAVO in system message")
    for name, position in (("user_start", 0), ("user_middle", lines // 2), ("user_end", lines - 1)):
        variant = copy.deepcopy(base)
        offset = sum(len(line) for line in paragraphs[:position]) + paragraphs[position].index("alpha")
        variant["messages"][1]["content"] = text[:offset] + "omega" + text[offset + len("alpha"):]
        add(name, variant, "plain_base", {"message": 1, "character_offset": offset, "old": "alpha", "new": "omega"})
    reordered_json = {key: base[key] for key in reversed(base)}
    reordered_json["messages"] = [{key: message[key] for key in reversed(message)} for message in base["messages"]]
    add("request_json_key_order", reordered_json, "plain_base", "reverse request/message JSON key order")
    thinking = dict(pin, enable_thinking=True)
    add("thinking_flag", copy.deepcopy(base), "plain_base", "change explicit enable_thinking from false to true", profile=thinking)
    tools = [
        {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
        {"type": "function", "function": {"name": "list_files", "description": "List files in a directory.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}}, "required": ["directory"]}}}]
    tool_base = copy.deepcopy(base)
    tool_base["tools"] = tools
    add("tools_base", tool_base)
    reordered_tools = copy.deepcopy(tool_base)
    reordered_tools["tools"].reverse()
    add("tool_order", reordered_tools, "tools_base", "reverse function definition order")
    schema_keys = copy.deepcopy(tool_base)
    function = schema_keys["tools"][0]["function"]
    schema_keys["tools"][0]["function"] = {key: function[key] for key in reversed(function)}
    add("tool_schema_key_order", schema_keys, "tools_base", "reverse keys inside the first function schema")
    history = copy.deepcopy(tool_base)
    history["messages"].extend([
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"README.md"}'}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "Cachino measures prompt structure."}])
    add("tool_history_base", history)
    spaced_arguments = copy.deepcopy(history)
    spaced_arguments["messages"][2]["tool_calls"][0]["function"]["arguments"] = '{ "path" : "README.md" }'
    add("tool_argument_whitespace", spaced_arguments, "tool_history_base", "change whitespace in equivalent JSON argument string")
    null_content = copy.deepcopy(history)
    null_content["messages"][2]["content"] = None
    add("unsupported_null_content", null_content, "tool_history_base", "null assistant content needs normalization support", unsupported=True)
    return cases


def _write(path, data):
    """Create private evidence without overwriting an existing fixture file."""
    with path.open("x", encoding="utf-8") as file:
        path.chmod(0o600)
        file.write(data)


def run(assets, out, pin, lines=128):
    """Save literal inputs, rendered prompts, token IDs and baseline comparisons.

    Assertions cover invariant controls and mutation-position ordering. Other
    mutations are observations: we do not force equal lengths or assumed BPE
    boundaries. All outputs remain explicitly unvalidated against an engine.
    """
    tokenizer, manifest, libraries = _load_tokenizer(assets, pin)
    cases = build_cases(pin, lines)
    out = Path(out)
    out.mkdir(parents=True, mode=0o700)
    rows, sequences = [], {}
    for case in cases:
        name, profile = case["name"], case["profile"]
        request_text = json.dumps(case["request"], ensure_ascii=False, indent=2) + "\n"
        _write(out / (name + ".request.json"), request_text)
        row = {"case": name, "baseline": case["baseline"], "change": case["change"],
               "profile": profile, "status": "unsupported", "request_sha256": hashlib.sha256(request_text.encode()).hexdigest(),
               "prompt_tokens": None, "baseline_tokens": None, "shared_prefix_tokens": None,
               "tokens_after_first_divergence": None,
               "baseline_tokens_after_first_divergence": None, "identical_token_ids": None}
        try:
            text, ids, digest = render(tokenizer, case["request"], profile)
        except UnsupportedRequest as error:
            if not case["expected_unsupported"]:
                raise
            row["reason"] = str(error)
            rows.append(row)
            continue
        if case["expected_unsupported"]:
            raise ValueError("expected an unsupported fixture to be rejected")
        _write(out / (name + ".prompt.txt"), text)
        _write(out / (name + ".tokens.json"), json.dumps(ids) + "\n")
        sequences[name] = ids
        row.update(status="rendered", prompt_tokens=len(ids), token_ids_sha256=digest)
        if case["baseline"] is not None:
            previous = sequences[case["baseline"]]
            shared = lcp(previous, ids)
            # These are structural suffix lengths, not inferred recomputation.
            row.update(baseline_tokens=len(previous), shared_prefix_tokens=shared,
                       tokens_after_first_divergence=len(ids) - shared,
                       baseline_tokens_after_first_divergence=len(previous) - shared,
                       identical_token_ids=ids == previous,
                       first_different_ids={"baseline": previous[shared] if shared < len(previous) else None,
                                            "variant": ids[shared] if shared < len(ids) else None})
        rows.append(row)
    by_name = {row["case"]: row for row in rows}
    for name in ("identical_repeat", "generation_controls", "request_json_key_order"):
        if not by_name[name]["identical_token_ids"]:
            raise ValueError("control changed token IDs: " + name)
    positions = [by_name[name]["shared_prefix_tokens"] for name in ("user_start", "user_middle", "user_end")]
    if not positions[0] < positions[1] < positions[2]:
        raise ValueError("measured token divergences do not follow mutation-position ordering")
    if by_name["early_system_label"]["identical_token_ids"]:
        raise ValueError("system mutation did not alter token IDs")
    result = {"schema_version": 1, "domain": "local_cpu_prefix_probe", "engine_validated": False,
              "inference_requests_sent": 0, "cached_tokens": None, "cache_granularity": None,
              "pin": pin, "assets": manifest, "libraries": libraries,
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "template_sha256": hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
              "fixture_lines": lines, "cases": rows}
    _write(out / "report.json", json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    with (out / "report.csv").open("x", newline="") as file:
        (out / "report.csv").chmod(0o600)
        fields = ("case", "baseline", "status", "prompt_tokens", "baseline_tokens", "shared_prefix_tokens", "tokens_after_first_divergence", "baseline_tokens_after_first_divergence", "identical_token_ids")
        writer = csv.DictWriter(file, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pin", type=Path, default=DEFAULT_PIN)
    parser.add_argument("--lines", type=int, default=128)
    args = parser.parse_args()
    try:
        result = run(args.assets, args.out, load_pin(args.pin), args.lines)
    except (OSError, ValueError, KeyError, ImportError) as error:
        parser.exit(1, "prefix probe failed: %s\n" % error)
    print("case,prompt_tokens,shared_prefix_tokens")
    for row in result["cases"]:
        print("%s,%s,%s" % (row["case"], row["prompt_tokens"], row["shared_prefix_tokens"]))
    print("CPU prefix checks passed. Inference requests: 0; actual cached tokens: unknown.")


if __name__ == "__main__":
    main()
