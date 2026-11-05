"""Explain captured request changes without treating JSON differences as tokens.

This offline view verifies trace integrity through report.py, fingerprints
ordered request sections and links tool results inside captured history. Text
and thinking contents stay in the private raw sidecars, not this summary.
"""

import argparse
import hashlib
import json
from pathlib import Path

from report import TraceError, body_bytes, inspect, read_events


def serialized(value, sort_keys=False):
    """Encode parsed JSON consistently; ordered fingerprints retain key order."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys).encode()


def fingerprint(value):
    """Describe a value without copying its text into the derived report."""
    data = serialized(value)
    result = {"json_type": type(value).__name__, "ordered_json_bytes": len(data),
              "ordered_json_sha256": hashlib.sha256(data).hexdigest()}
    if isinstance(value, str):
        result["characters"] = len(value)
    return result


def differences(before, after, limit=64):
    """Return bounded JSON-pointer value/order changes and a truncation marker.

    Paths follow captured request order, not a provider's rendering order. A
    mapping's key order is tracked separately from its parsed value changes.
    Fingerprints refer to parsed JSON values, never original byte substrings.
    """
    changes = []
    truncated = False

    def add(item):
        nonlocal truncated
        if len(changes) < limit:
            changes.append(item)
        else:
            truncated = True

    def visit(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            if list(left) != list(right):
                add({"path": path, "kind": "object_keys_or_order", "before_keys": list(left), "after_keys": list(right)})
            for key in dict.fromkeys([*left, *right]):
                child = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in left:
                    add({"path": child, "kind": "added", "after": fingerprint(right[key])})
                elif key not in right:
                    add({"path": child, "kind": "removed", "before": fingerprint(left[key])})
                else:
                    visit(left[key], right[key], child)
        elif isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                child = path + "/" + str(index)
                if index >= len(left):
                    add({"path": child, "kind": "added", "after": fingerprint(right[index])})
                elif index >= len(right):
                    add({"path": child, "kind": "removed", "before": fingerprint(left[index])})
                else:
                    visit(left[index], right[index], child)
        elif serialized(left, True) != serialized(right, True):
            add({"path": path, "kind": "value", "before": fingerprint(left), "after": fingerprint(right)})

    visit(before, after, "")
    return {"changes": changes, "truncated": truncated}


def block_shape(block, path):
    """Expose block type, structural metadata and fingerprints, excluding text."""
    shape = {"path": path, **fingerprint(block)}
    if isinstance(block, dict):
        shape["keys"] = list(block)
        for name in ("type", "name", "id", "tool_use_id", "cache_control", "is_error"):
            if name in block:
                shape[name] = block[name]
        if isinstance(block.get("text"), str):
            shape["text_characters"] = len(block["text"])
    return shape


def request_shape(request):
    """Summarize Messages/OpenAI request structure and within-history tool links.

    Links establish matching IDs in the submitted history, not semantic turn
    labels or proof about how an unobserved client operation executed.
    """
    messages, links, pending = [], [], {}
    for index, message in enumerate(request.get("messages", [])):
        path = "/messages/" + str(index)
        if not isinstance(message, dict):
            messages.append(block_shape(message, path))
            continue
        content = message.get("content")
        blocks = content if isinstance(content, list) else [content]
        shapes = []
        for number, block in enumerate(blocks):
            pointer = path + "/content" + ("/" + str(number) if isinstance(content, list) else "")
            shapes.append(block_shape(block, pointer))
            if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                pending[block["id"]] = {"path": pointer, "name": block.get("name")}
            if isinstance(block, dict) and block.get("type") == "tool_result":
                links.append({"result_path": pointer, "tool_use_id": block.get("tool_use_id"),
                              "matching_history_call": pending.get(block.get("tool_use_id")) if isinstance(block.get("tool_use_id"), str) else None})
        messages.append({"path": path, "role": message.get("role"), "blocks": shapes})
    system = request.get("system", [])
    system = system if isinstance(system, list) else [system]
    return {"top_level_keys": list(request),
            "system": [block_shape(block, "/system/" + str(i)) for i, block in enumerate(system)],
            "tools": [block_shape(tool, "/tools/" + str(i)) for i, tool in enumerate(request.get("tools", []))],
            "messages": messages, "tool_links": links,
            "thinking_parameter": request.get("thinking"),
            "thinking_parameter_present": "thinking" in request,
            "history_contains_thinking": any(b.get("type") == "thinking" for m in messages for b in m.get("blocks", []))}


def audit(root, encoding_override=None):
    """Audit verified request bodies, comparing only explicitly labeled sessions.

    Unsupported/incomplete requests remain rows and break comparison chains.
    Separate paths/protocols do not compare. A report is a structural diagnostic,
    not cache attribution, token reconstruction or a provider rendering oracle.
    """
    root = Path(root).resolve()
    report = inspect(root, encoding_override)
    records, _ = read_events(root)
    previous, rows = {}, []
    for record, summary in zip(records, report["requests"]):
        row = {key: summary[key] for key in ("request_id", "sequence", "session_id", "turn", "attempt", "path", "http_status", "state", "request_integrity", "response_integrity", "request_sha256", "protocol", "message_stop", "sse_done", "parse_error", "response_decode_error", "anthropic_usage", "response_content_encoding", "response_encoding_source")}
        session = row["session_id"]
        predecessor = previous.get(session) if session is not None else None
        if session is not None:
            previous[session] = None
        data, integrity = body_bytes(root, record, "request")
        row["status"] = "unsupported"
        try:
            if data is None:
                raise ValueError("request body is " + integrity)
            request = json.loads(data)
            if not isinstance(request, dict) or not isinstance(request.get("messages"), list):
                raise ValueError("request has no supported messages array")
            if not isinstance(request.get("tools", []), list):
                raise ValueError("tools is not an array")
        except (ValueError, UnicodeError) as error:
            row["reason"] = str(error)
            rows.append(row)
            continue
        row.update(status="audited", shape=request_shape(request))
        if predecessor is not None and predecessor[0]["path"] == row["path"]:
            old_row, old_request = predecessor
            components = []
            for name in dict.fromkeys([*old_request, *request]):
                present = name in old_request and name in request
                components.append({"field": name, "before_present": name in old_request, "after_present": name in request,
                                   "parsed_values_equal": present and serialized(old_request[name], True) == serialized(request[name], True),
                                   "ordered_values_equal": present and serialized(old_request[name]) == serialized(request[name])})
            row["comparison"] = {"previous_request_id": old_row["request_id"],
                                 "raw_request_bytes_equal": old_row["request_sha256"] == row["request_sha256"],
                                 "components": components, **differences(old_request, request)}
        if session is not None:
            previous[session] = (row, request)
        rows.append(row)
    return {"source_trace": str(root), "response_encoding_override": encoding_override,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "comparison_domain": "parsed request JSON, retaining mapping order; not model prompt order or token IDs",
            "warnings": report["warnings"], "requests": rows}


def markdown(result):
    """Render a compact audit with stable pointers into the raw request files."""
    lines = ["# Captured request audit", "", "Source: `" + result["source_trace"] + "`", "",
             "Comparison domain: " + result["comparison_domain"] + ".", "",
             "Native usage fields are retained without an inferred cache ratio. Text and thinking content stay in raw sidecars.", ""]
    for row in result["requests"]:
        lines += ["## Request %s" % row["sequence"], "", "Evidence: `%s/request.body`, path `%s`." % (row["request_id"], row["path"]), "",
                  "Status: %s; HTTP %s; request/response integrity %s/%s." % (row["status"], row["http_status"], row["request_integrity"], row["response_integrity"]), ""]
        if row["status"] != "audited":
            lines += [row["reason"], ""]
            continue
        shape = row["shape"]
        lines += ["Messages: " + " -> ".join(str(m.get("role", "unknown")) + " [" + ", ".join(str(b.get("type", b["json_type"])) for b in m.get("blocks", [])) + "]" for m in shape["messages"]), "",
                  "Thinking parameter present: %s. Thinking in submitted history: %s." % (shape["thinking_parameter_present"], shape["history_contains_thinking"]), "",
                  "Native usage: `" + json.dumps(row["anthropic_usage"]) + "`.", ""]
        comparison = row.get("comparison")
        if comparison:
            lines += ["Compared with `%s`:" % comparison["previous_request_id"], "", "| Field | Parsed values equal | Ordered values equal |", "| --- | --- | --- |"]
            for component in comparison["components"]:
                lines.append("| %s | %s | %s |" % (component["field"], component["parsed_values_equal"], component["ordered_values_equal"]))
            lines += ["", "Changed JSON pointers (before/after fingerprints in audit.json):", ""]
            lines += ["- `%s`: %s" % (c["path"], c["kind"]) for c in comparison["changes"]]
            lines += [""]
    lines += ["No token-prefix, cache-loss or latency-effect claim follows from these JSON differences.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_dir", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="new private derived directory")
    parser.add_argument("--response-encoding-override", choices=["identity", "zstd"])
    args = parser.parse_args()
    try:
        result = audit(args.trace_dir, args.response_encoding_override)
        args.out.mkdir(mode=0o700, parents=True)
        for name, text in (("audit.json", json.dumps(result, indent=2, ensure_ascii=False) + "\n"), ("audit.md", markdown(result))):
            with (args.out / name).open("x", encoding="utf-8") as file:
                file.write(text)
            (args.out / name).chmod(0o600)
        print("Request audit: " + str(args.out / "audit.md"))
    except (OSError, TraceError, ValueError, TypeError) as error:
        parser.exit(1, "request audit failed: %s\n" % error)


if __name__ == "__main__":
    main()
