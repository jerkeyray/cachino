"""Explain adjacent and historical prompt matches without modeling KV residency.

This CPU-only reader joins a reconstructed dataset to integrity-verified raw
captures. Its history contains observed prompts in acceptance order, not the
engine's processed output or resident cache. See the measurement contract.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from reconstruct import lcp
from report import TraceError, inspect


def _json_file(path):
    """Read a regular derived file without following a supplied symlink."""
    if path.is_symlink() or not path.is_file():
        raise TraceError("expected regular derived file: " + str(path))
    data = path.read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def _token_ids(root, row):
    """Validate a reconstruction's ID array and its declared length/hash.

    This establishes consistency with the derived manifest, not authenticity
    of tokenization or parity with an inference engine.
    """
    ids, _file_hash = _json_file(root / (row["request_id"] + ".tokens.json"))
    if not isinstance(ids, list) or not ids or any(type(value) is not int or value < 0 for value in ids):
        raise TraceError("token IDs must be a nonempty list of nonnegative integers")
    digest = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
    if type(row.get("prompt_tokens")) is not int or row["prompt_tokens"] != len(ids):
        raise TraceError("token length mismatch: " + row["request_id"])
    if row.get("token_ids_sha256") != digest:
        raise TraceError("token hash mismatch: " + row["request_id"])
    return ids


def _exclusion(source, row):
    """Return why a row cannot enter this conservative prompt comparison set."""
    if row.get("status") == "unsupported":
        reason = row.get("reason")
        return reason if isinstance(reason, str) and reason else "unsupported reconstruction"
    if source["path"] != "/v1/chat/completions":
        return "only OpenAI chat-completion reconstructions are supported"
    if source["request_integrity"] != "verified" or source["response_integrity"] != "verified":
        return "capture is not fully verified"
    if source["state"] != "completed" or source["http_status"] != 200:
        return "capture is not a completed HTTP 200 exchange"
    if source["parse_error"] or source["response_error"]:
        return "response has a parse or semantic error"
    if source["stream"] and source["sse_done"] is not True:
        return "OpenAI stream has no verified [DONE] marker"
    return None


def explain(reconstructed, trace_dir):
    """Compare all manifested prompts in explicit session/source-model lanes.

    Require exact row coverage and source identities. Unsupported or unverified
    rows clear their session's history so missing evidence cannot be crossed
    silently. Unlabeled requests never form an inferred session. Ties select
    the most recent candidate. Neither acceptance order nor an HTTP 200 proves
    that earlier prompts were resident when a later request executed.
    """
    root = Path(reconstructed).resolve()
    dataset, dataset_hash = _json_file(root / "summary.json")
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1:
        raise TraceError("expected reconstruction schema version 1")
    rows = dataset.get("requests")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows) or not isinstance(dataset.get("pin"), dict):
        raise TraceError("reconstruction rows and rendering pin are required")
    source_report = inspect(trace_dir)
    sources = source_report["requests"]
    # Exact coverage prevents a caller from dropping an unsupported row and
    # accidentally joining history across its evidence boundary.
    if [row.get("request_id") for row in rows] != [row["request_id"] for row in sources]:
        raise TraceError("reconstruction request coverage/order differs from raw capture")
    histories, previous, result_rows = {}, {}, []
    for row, source in zip(rows, sources):
        for key in ("sequence", "session_id", "turn"):
            if type(row.get(key)) is not type(source.get(key)) or row.get(key) != source.get(key):
                raise TraceError("reconstruction/source identity mismatch: " + key)
        if row.get("status") not in {"rendered", "unsupported"}:
            raise TraceError("unknown reconstruction row status")
        session = source["session_id"]
        if session is not None and (not isinstance(session, str) or not session):
            raise TraceError("session must be a nonempty string or null")
        ids = None
        if row["status"] == "rendered":
            if row.get("request_sha256") != source["request_sha256"]:
                raise TraceError("reconstruction/source request hash mismatch")
            if not isinstance(row.get("source_model"), str) or row["source_model"] != source["model"]:
                raise TraceError("reconstruction/source model mismatch")
            ids = _token_ids(root, row)
        result = {
            "request_id": source["request_id"], "sequence": source["sequence"],
            "session_id": session, "turn": source["turn"], "attempt": source["attempt"],
            "status": "excluded", "reason": _exclusion(source, row),
            "source_model": source["model"], "model_remapped": row.get("model_remapped"),
            "local_prompt_tokens": len(ids) if ids is not None else None,
            "local_token_ids_sha256": row.get("token_ids_sha256"),
            "adjacent_request_id": None, "adjacent_lcp_tokens": None,
            "prior_prompt_candidates": 0, "historical_request_id": None,
            "historical_prompt_lcp_tokens": None,
            "actual_engine_cached_tokens": None, "processed_prompt_tokens": None,
            "upstream_usage": {key: source[key] for key in (
                "protocol", "prompt_tokens", "cached_tokens", "cache_metric_source", "cache_metric_conflict")},
        }
        if result["reason"] is not None:
            if session is not None:
                histories = {lane: entries for lane, entries in histories.items() if lane[0] != session}
                previous.pop(session, None)
        elif session is None:
            result.update(status="unassigned", reason="no explicit session; no history comparison")
        else:
            lane = (session, row["source_model"])
            history = histories.setdefault(lane, [])
            prior = previous.get(session)
            result.update(status="compared", prior_prompt_candidates=len(history))
            if prior is not None and prior[0] == lane:
                result["adjacent_request_id"] = prior[1]
                result["adjacent_lcp_tokens"] = lcp(prior[2], ids)
            # Reverse order makes equal-length matches select the latest one.
            for request_id, candidate in reversed(history):
                shared = lcp(candidate, ids)
                if result["historical_prompt_lcp_tokens"] is None or shared > result["historical_prompt_lcp_tokens"]:
                    result["historical_request_id"] = request_id
                    result["historical_prompt_lcp_tokens"] = shared
            history.append((source["request_id"], ids))
            previous[session] = (lane, source["request_id"], ids)
        result_rows.append(result)
    return {
        "schema_version": 1, "measurement_domain": "local_observed_prompt_structure_only",
        "engine_validated": False, "source_reconstruction": str(root),
        "source_reconstruction_sha256": dataset_hash, "source_trace": str(Path(trace_dir).resolve()),
        "analysis_sources_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                    for name in ("reuse.py", "reconstruct.py", "report.py")},
        "pin": dataset["pin"], "template_sha256": dataset.get("template_sha256"),
        "libraries": dataset.get("libraries"), "warnings": source_report["warnings"],
        "comparison_order": "capture acceptance; not engine execution or completion order",
        "history_scope": "explicit session and source model, cleared at unsupported/unverified rows",
        "history_contents": "observed prompt IDs only; excludes generated tokens and other sessions",
        "cache_geometry": None,
        "limitations": [
            "A historical prompt match is not a cache hit, residency map or complete reuse ceiling.",
            "Upstream usage is a separate domain and is never subtracted from local Qwen token counts.",
            "Token files are hash-checked against reconstruction; tokenization is not rerun here.",
            "No block alignment or causal cache-miss classification is inferred.",
        ],
        "requests": result_rows,
    }


def markdown(report):
    """Render a text-free request index and explicit measurement limitations."""
    lines = ["# Observed prompt history", "",
             "CPU structural comparison only. No engine/cache reuse was validated.", "",
             "| Sequence | Request | Status | Adjacent LCP | Historical prompt LCP | Selected prior request |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in report["requests"]:
        values = [row[key] for key in ("sequence", "request_id", "status", "adjacent_lcp_tokens",
                                      "historical_prompt_lcp_tokens", "historical_request_id")]
        lines.append("| " + " | ".join("unknown / not applicable" if value is None else str(value) for value in values) + " |")
    lines += ["", "## Boundaries", ""] + ["- " + item for item in report["limitations"]]
    lines += ["", "History: " + report["history_scope"] + ".", report["history_contents"] + ".",
              report["comparison_order"] + ".", "",
              "Excluded/unassigned reasons are retained in summary.json. No request text or token arrays are copied into this report.", ""]
    return "\n".join(lines)


def write_report(out, report):
    """Write a new private report directory; refuse to overwrite any existing one."""
    out = Path(out)
    out.mkdir(mode=0o700, parents=True)
    for name, text in (("report.md", markdown(report)),
                       ("summary.json", json.dumps(report, indent=2, ensure_ascii=False) + "\n")):
        with os.fdopen(os.open(out / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as file:
            file.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reconstructed", type=Path, help="existing reconstruction directory with summary.json")
    parser.add_argument("--trace-dir", type=Path, required=True, help="original raw captures to verify the reconstruction against")
    parser.add_argument("--out", type=Path, required=True, help="new derived report directory")
    args = parser.parse_args()
    try:
        result = explain(args.reconstructed, args.trace_dir)
        write_report(args.out, result)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, "Prompt history failed: %s\n" % error)
    print("Compared %d captured requests. Structural only; no engine/cache validation. Report: %s" %
          (len(result["requests"]), args.out / "report.md"))


if __name__ == "__main__":
    main()
