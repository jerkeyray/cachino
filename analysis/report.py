"""Inspect immutable Suez trace runs; no tokenizer or cache inference is performed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


class TraceError(ValueError):
    """A committed trace violates the format or its stored byte evidence."""

    pass


def read_events(root):
    """Return lifecycle snapshots in acceptance order, plus recovery warnings.

    Finish records replace their matching starts; missing finishes remain
    visible. Only an unterminated final line is recoverable without an error.
    """
    records, started, sequences, warnings = {}, set(), set(), []
    data = (root / "events.jsonl").read_bytes()
    lines = data.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.endswith(b"\n"):
            warnings.append("ignored unterminated final event; run may have been interrupted")
            break
        try:
            event = json.loads(line)
            record = event["record"]
            request_id = record["request_id"]
            sequence = record["sequence"]
            kind = event["event"]
            if record["schema_version"] != 1 or not re.fullmatch(r"[0-9a-f]{32}", request_id):
                raise ValueError("unsupported schema or request ID")
            if type(sequence) is not int or sequence < 1:
                raise ValueError("invalid sequence")
            if kind == "started":
                if request_id in started or sequence in sequences or record["state"] != "in_progress":
                    raise ValueError("duplicate start/sequence or invalid start state")
                started.add(request_id)
                sequences.add(sequence)
            elif kind == "finished":
                previous = records.get(request_id)
                if previous is None or previous["state"] != "in_progress":
                    raise ValueError("finish without start or duplicate finish")
                if record["state"] not in {"completed", "cancelled", "aborted", "downstream_error"}:
                    raise ValueError("invalid finish state")
                # A finish updates lifecycle evidence, not request identity.
                # Check labels too so corrupt logs cannot silently regroup turns.
                for key in ("sequence", "session_id", "turn", "attempt", "started_at", "method", "path"):
                    if record.get(key) != previous.get(key):
                        raise ValueError("start/finish identity mismatch")
            else:
                raise ValueError("unknown event type")
            records[request_id] = record
        except (ValueError, TypeError, KeyError) as error:
            raise TraceError("invalid event at line %d: %s" % (index + 1, error)) from error
    return sorted(records.values(), key=lambda record: record["sequence"]), warnings


def body_bytes(root, record, name):
    """Validate a sidecar and return (complete_bytes, integrity_state).

    Incomplete captures return None so downstream analysis cannot treat a
    stored prefix as a complete request or response. Corruption raises TraceError.
    """
    info = record[name]
    expected = record["request_id"] + "/" + name + ".body"
    if info["path"] != expected:
        raise TraceError("unexpected body path")
    path = root / expected
    # Never follow a sidecar symlink outside the run.
    if root not in path.resolve().parents:
        raise TraceError("body path escapes run")
    if record["state"] == "in_progress":
        return None, "unfinished"
    observed, stored = info["observed_bytes"], info["stored_bytes"]
    if type(observed) is not int or type(stored) is not int or not 0 <= stored <= observed:
        raise TraceError("invalid body byte counts")
    if info["truncated"] != (stored < observed):
        raise TraceError("inconsistent truncation marker")
    data = path.read_bytes()
    if len(data) != stored:
        raise TraceError("stored body length mismatch: " + expected)
    # The stored hash validates even a capped prefix. The observed hash can
    # also be checked only when the sidecar contains every observed byte.
    if hashlib.sha256(data).hexdigest() != info["sha256_stored"]:
        raise TraceError("stored body hash mismatch: " + expected)
    if stored == observed and hashlib.sha256(data).hexdigest() != info["sha256_observed"]:
        raise TraceError("body hash mismatch: " + expected)
    if info["capture_error"]:
        return None, "capture_error"
    if info["truncated"]:
        return None, "truncated"
    if not info["complete"]:
        return None, "partial"
    return data, "verified"


def nonnegative_int(value):
    """Keep valid usage counts; reject booleans and preserve unknowns as None."""
    return value if type(value) is int and value >= 0 else None


def cache_usage(usage):
    """Normalize reported cache hits without guessing a provider from model names.

    OpenAI and DeepSeek use different fields. If both are present, require
    valid agreement rather than silently choosing one. These remain upstream
    claims, independent of local tokenizer IDs or engine validation.
    """
    details = usage.get("prompt_tokens_details")
    candidates = []
    if isinstance(details, dict) and "cached_tokens" in details:
        candidates.append(("prompt_tokens_details.cached_tokens", nonnegative_int(details["cached_tokens"])))
    if "prompt_cache_hit_tokens" in usage:
        candidates.append(("prompt_cache_hit_tokens", nonnegative_int(usage["prompt_cache_hit_tokens"])))
    if not candidates:
        return None, None, False
    source = "+".join(name for name, _ in candidates)
    values = [value for _, value in candidates]
    conflict = len(values) > 1 and any(value != values[0] for value in values[1:])
    return (None if conflict else values[0]), source, conflict


def response_summary(data, content_type, protocol="openai", content_encoding=""):
    """Extract JSON/SSE usage and semantic error/termination markers.

    data=None means no complete validated body is available. Transport
    completion is recorded separately; neither HTTP 200 nor EOF proves [DONE].
    Malformed response content is flagged without rewriting the raw evidence.
    """
    result = {"stream": content_type.split(";")[0].strip().lower() == "text/event-stream",
              "sse_done": None, "response_error": False, "parse_error": False,
              "prompt_tokens": None, "cached_tokens": None,
              "cache_metric_source": None, "cache_metric_conflict": False,
              "protocol": protocol, "message_stop": None,
              "anthropic_usage": None, "response_decode_error": None}
    if data is None:
        return result
    encoding = content_encoding.strip().lower()
    if encoding not in ("", "identity"):
        if encoding != "zstd":
            result.update(parse_error=True, response_decode_error="unsupported content encoding")
            return result
        try:
            # Python 3.14 provides the optional decoder without adding packages
            # to the dependency-free base environment. Bound decompressed data;
            # source integrity always refers to the unchanged compressed bytes.
            from compression.zstd import ZstdDecompressor
            decoder = ZstdDecompressor()
            decoded = decoder.decompress(data, max_length=(16 << 20) + 1)
            if len(decoded) > 16 << 20 or not decoder.eof or decoder.unused_data:
                raise ValueError("incomplete, concatenated or oversized zstd frame")
            data = decoded
        except ImportError:
            result.update(parse_error=True, response_decode_error="zstd requires Python 3.14 with compression.zstd")
            return result
        except Exception:
            result.update(parse_error=True, response_decode_error="invalid or oversized zstd response")
            return result
    try:
        text = data.decode("utf-8")
        if result["stream"]:
            payloads, done = [], False
            # SSE dispatches only blank-line-terminated events. Multiple data
            # lines are joined per the event framing, not parsed as separate JSON.
            blocks = text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
            if blocks[-1].strip():
                result["parse_error"] = True
            for block in blocks[:-1]:
                values = []
                for line in block.split("\n"):
                    if line == "data":
                        values.append("")
                    elif line.startswith("data:"):
                        value = line[5:]
                        values.append(value[1:] if value.startswith(" ") else value)
                if not values:
                    continue
                value = "\n".join(values)
                if value == "[DONE]":
                    done = True
                else:
                    payloads.append(json.loads(value))
            result["sse_done"] = done
            if protocol == "anthropic":
                # Messages uses message_stop, not OpenAI's [DONE]. Keeping
                # separate markers prevents EOF from appearing successful.
                result["sse_done"] = None
                result["message_stop"] = False
        else:
            payloads = [json.loads(text)]
        for payload in payloads:
            if not isinstance(payload, dict):
                result["parse_error"] = True
                continue
            result["response_error"] |= "error" in payload
            if protocol == "anthropic":
                if result["stream"] and payload.get("type") == "message_stop":
                    result["message_stop"] = True
                message = payload.get("message") if payload.get("type") == "message_start" else payload
                usage = message.get("usage") if isinstance(message, dict) else None
                if isinstance(usage, dict):
                    # Stream deltas carry cumulative updates. Preserve native
                    # fields instead of summing deltas or assuming OpenAI's
                    # prompt-token denominator applies to compatible gateways.
                    if result["anthropic_usage"] is None:
                        result["anthropic_usage"] = {}
                    result["anthropic_usage"].update(usage)
                continue
            if protocol != "openai":
                continue
            usage = payload.get("usage")
            if isinstance(usage, dict):
                # Last usage object is authoritative; null/absent stay unknown.
                result["prompt_tokens"] = nonnegative_int(usage.get("prompt_tokens"))
                result["cached_tokens"], result["cache_metric_source"], result["cache_metric_conflict"] = cache_usage(usage)
    except (ValueError, UnicodeError):
        result["parse_error"] = True
    return result


def inspect(root, response_encoding_override=None):
    """Build request/session summaries from a run without modifying its source.

    Only validated complete bodies supply usage. Ratios use paired known counts
    and report coverage, keeping absent metrics distinct from measured zeros.
    """
    root = Path(root).resolve()
    records, warnings = read_events(root)
    rows = []
    for record in records:
        request, request_integrity = body_bytes(root, record, "request")
        response, response_integrity = body_bytes(root, record, "response")
        model, request_json_valid = None, None
        if request is not None:
            try:
                payload = json.loads(request)
                request_json_valid = isinstance(payload, dict)
                if request_json_valid:
                    model = payload.get("model")
            except (ValueError, UnicodeError):
                request_json_valid = False
        row = {"request_id": record["request_id"], "sequence": record["sequence"],
               "session_id": record.get("session_id"), "turn": record.get("turn"),
               "attempt": record.get("attempt"), "http_status": record["http_status"],
               "state": record["state"], "path": record["path"], "model": model, "request_json_valid": request_json_valid,
               "request_integrity": request_integrity, "response_integrity": response_integrity,
               "request_sha256": record["request"].get("sha256_observed") or None,
               "duration_ms": record["duration_ms"] if record["state"] != "in_progress" else None,
               "first_response_write_ms": record["first_response_write_ms"]}
        protocol = {"/v1/chat/completions": "openai", "/v1/messages": "anthropic",
                    "/v1/messages/count_tokens": "anthropic_count_tokens"}.get(record["path"], "unknown")
        encoding = response_encoding_override if response_encoding_override is not None else record.get("response_content_encoding", "")
        row["response_content_encoding"] = encoding
        row["response_encoding_source"] = "explicit_override" if response_encoding_override is not None else "capture_metadata"
        row.update(response_summary(response, record["response_content_type"], protocol, encoding))
        rows.append(row)
    # Use the same request subset for numerator and denominator. Averaging
    # per-request ratios would give a short prompt the weight of a long one.
    known = [row for row in rows if row["prompt_tokens"] is not None and row["cached_tokens"] is not None]
    prompt_sum = sum(row["prompt_tokens"] for row in known)
    cached_sum = sum(row["cached_tokens"] for row in known)
    if any(row["cached_tokens"] > row["prompt_tokens"] for row in known):
        warnings.append("cached_tokens exceeds prompt_tokens in at least one response; inspect upstream metrics")
    sessions = []
    # Group only explicitly labeled sessions. Missing labels are counted
    # separately rather than inventing a session shared by unrelated callers.
    for session_id in sorted({row["session_id"] for row in rows if row["session_id"] is not None}):
        members = [row for row in rows if row["session_id"] == session_id]
        paired = [row for row in members if row["prompt_tokens"] is not None and row["cached_tokens"] is not None]
        session_prompt = sum(row["prompt_tokens"] for row in paired)
        session_cached = sum(row["cached_tokens"] for row in paired)
        sessions.append({"session_id": session_id, "requests": len(members),
                         "known_cache_metrics_requests": len(paired),
                         "known_prompt_tokens": session_prompt if paired else None,
                         "known_cached_tokens": session_cached if paired else None,
                         "weighted_cache_ratio": session_cached / session_prompt if session_prompt else None})
    return {"schema_version": 1, "source": str(root), "warnings": warnings,
            "metrics": {"requests": len(rows),
                        "transport_completed": sum(row["state"] == "completed" for row in rows),
                        "http_errors": sum(row["http_status"] >= 400 for row in rows),
                        "unfinished": sum(row["state"] == "in_progress" for row in rows),
                        "unassigned_session_requests": sum(row["session_id"] is None for row in rows),
                        "fully_verified_captures": sum(row["request_integrity"] == row["response_integrity"] == "verified" for row in rows),
                        "known_cache_metrics_requests": len(known),
                        "known_prompt_tokens": prompt_sum if known else None,
                        "known_cached_tokens": cached_sum if known else None,
                        "weighted_cache_ratio": cached_sum / prompt_sum if prompt_sum else None},
            "timing_definition": "proxy handler duration and first nonempty response Write; neither is model TTFT or engine time",
            "sessions": sessions,
            "requests": rows}


def write_sqlite(path, report):
    """Create a private derived index at a new path, preserving SQL NULLs.

    Existing paths are rejected. If indexing fails, remove only the new file
    owned by this call so a partial database cannot appear to be a valid index.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        connection = sqlite3.connect(path)
        try:
            with connection:
                connection.execute("CREATE TABLE run (source TEXT NOT NULL, schema_version INTEGER NOT NULL, summary_json TEXT NOT NULL)")
                connection.execute("INSERT INTO run VALUES (?, ?, ?)", (report["source"], 1, json.dumps(report["metrics"])))
                connection.execute("""CREATE TABLE requests (
                    request_id TEXT PRIMARY KEY, sequence INTEGER UNIQUE NOT NULL,
                    session_id TEXT, turn INTEGER, attempt INTEGER, http_status INTEGER,
                    state TEXT, prompt_tokens INTEGER, cached_tokens INTEGER, summary_json TEXT NOT NULL)""")
                connection.executemany("INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(row["request_id"], row["sequence"], row["session_id"], row["turn"], row["attempt"],
                      row["http_status"], row["state"], row["prompt_tokens"], row["cached_tokens"], json.dumps(row)) for row in report["requests"]])
        finally:
            connection.close()
    except BaseException:
        os.unlink(path)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_dir", type=Path)
    parser.add_argument("--sqlite", type=Path, help="create a new derived SQLite index")
    parser.add_argument("--response-encoding-override", choices=["identity", "zstd"], help="explicit decoding for legacy captures lacking encoding metadata; source remains unchanged")
    args = parser.parse_args()
    try:
        report = inspect(args.trace_dir, args.response_encoding_override)
        if args.sqlite:
            write_sqlite(args.sqlite, report)
        json.dump(report, sys.stdout, indent=2, ensure_ascii=False, allow_nan=False)
        sys.stdout.write("\n")
    except (OSError, TraceError, KeyError, TypeError) as error:
        parser.exit(1, "trace inspection failed: %s\n" % error)


if __name__ == "__main__":
    main()
