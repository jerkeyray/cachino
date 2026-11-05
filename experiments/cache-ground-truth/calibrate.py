"""Run the M4 calibration sequence against one vLLM server and record evidence.

The client reads probes.json, a probe directory written by build_probes.py and a
filled run manifest. It sends each built request body byte for byte, follows the
spec's sequence of resets and probes, stores every raw response, and compares
returned prompt token IDs and cached-token counts with the local expectations.

Default execution is a dry run: it validates inputs and prints the plan without
opening a connection. --run sends requests and needs a manifest with no open
gates. Endpoints must be explicit http://127.0.0.1:PORT origins; a remote GPU
host is reached through an SSH port forward, so this client never sends probe
traffic or headers to a non-loopback address.

What a completed run establishes is limited to these controls on this server:
returned-ID parity, reported cache counts under the recorded configuration, and
engine-reported timing fields with their source-defined boundaries. Client
round-trip time is recorded separately and is not engine time.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "analysis"))
sys.path.insert(0, str(HERE))

from check_manifest import check as check_manifest  # noqa: E402
from reconstruct import lcp  # noqa: E402

RESPONSE_CAP = 8 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    """Treat a redirect as a failed step instead of sending a second request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class InputError(ValueError):
    """Probe, spec or manifest inputs are inconsistent; nothing was sent."""


class StopRun(Exception):
    """A stop rule fired. The step record carries the reason."""


def origin(value):
    """Accept only an explicit http://127.0.0.1:PORT origin without credentials."""
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise InputError("endpoint must be an http://127.0.0.1:PORT origin: " + value)
    if parsed.port is None:
        raise InputError("endpoint needs an explicit port: " + value)
    return value.rstrip("/")


def load_inputs(spec_path, probes_dir):
    """Return (spec, spec_sha256, bodies, tokens) after checking every hash.

    The probe directory must have been built from exactly this spec, and each
    body and token file must match the hashes in its summary. A mismatch means
    the bytes to send or the expectations changed after the build.
    """
    spec_bytes = Path(spec_path).read_bytes()
    spec = json.loads(spec_bytes)
    spec_sha = hashlib.sha256(spec_bytes).hexdigest()
    probes_dir = Path(probes_dir)
    summary = json.loads((probes_dir / "summary.json").read_text(encoding="utf-8"))
    if summary.get("spec_sha256") != spec_sha:
        raise InputError("probe directory was built from a different spec")
    bodies, tokens = {}, {}
    for name in spec["probes"]:
        row = summary["probes"].get(name)
        if row is None:
            raise InputError("probe directory lacks " + name)
        data = (probes_dir / (name + ".request.json")).read_bytes()
        if hashlib.sha256(data).hexdigest() != row["request_sha256"]:
            raise InputError("request body hash changed for " + name)
        ids = json.loads((probes_dir / (name + ".tokens.json")).read_text())
        if hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest() != row["token_ids_sha256"]:
            raise InputError("local token hash changed for " + name)
        bodies[name], tokens[name] = data, ids
    return spec, spec_sha, bodies, tokens


def check_ready(manifest, spec, spec_sha):
    """Raise InputError unless the manifest has no open gates for this spec."""
    missing, conflicts = check_manifest(manifest)
    if missing or conflicts:
        raise InputError("manifest not ready: missing " + ", ".join(missing) + "; conflicts " + "; ".join(conflicts))
    if manifest["run"]["probe_spec_sha256"] != spec_sha:
        raise InputError("manifest probe_spec_sha256 does not match probes.json")
    if manifest["model"]["served_model_name"] != spec["common_request"]["model"]:
        raise InputError("served model name differs from the probe request model")


class Client:
    """One bounded HTTP exchange per call: no retries, redirects or env proxies."""

    def __init__(self, timeout):
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def exchange(self, method, url, data=None, headers=None):
        """Return (status, headers, body, elapsed_ms); raise StopRun on transport failure.

        HTTP error statuses are returned, not raised, so their bodies are kept.
        Bodies above the cap stop the run rather than being stored truncated.
        """
        request = Request(url, data=data, method=method, headers=headers or {})
        started = time.perf_counter_ns()
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            response = error
        except (URLError, OSError) as error:
            raise StopRun("transport failure: " + type(error).__name__ + ": " + str(error)) from error
        with response:
            body = response.read(RESPONSE_CAP + 1)
            elapsed = (time.perf_counter_ns() - started) / 1e6
            if len(body) > RESPONSE_CAP:
                raise StopRun("response exceeded cap")
            return response.status, dict(response.headers.items()), body, elapsed


def observe(body):
    """Extract source-defined fields from one chat response without inventing values.

    Absent fields become None. Metric names keep their API meaning: the API's
    time_to_first_token_ms is scheduled-to-first-token at the inspected pin, so
    it is normalized as engine_schedule_to_first_token_ms.
    """
    data = json.loads(body)
    usage = data.get("usage") or {}
    details = usage.get("prompt_tokens_details")
    metrics = data.get("metrics")
    choice = (data.get("choices") or [{}])[0]
    return {
        "response_id": data.get("id"),
        "prompt_token_ids": data.get("prompt_token_ids"),
        "generated_token_ids": choice.get("token_ids"),
        "finish_reason": choice.get("finish_reason"),
        "usage_prompt_tokens": usage.get("prompt_tokens"),
        "usage_completion_tokens": usage.get("completion_tokens"),
        "cached_tokens": details.get("cached_tokens") if isinstance(details, dict) else None,
        "prompt_tokens_details_present": isinstance(details, dict),
        "metrics_present": isinstance(metrics, dict),
        "engine_schedule_to_first_token_ms": metrics.get("time_to_first_token_ms") if isinstance(metrics, dict) else None,
        "engine_queue_ms": metrics.get("queue_time_ms") if isinstance(metrics, dict) else None,
        "engine_first_to_last_token_ms": metrics.get("generation_time_ms") if isinstance(metrics, dict) else None,
    }


def evaluate(name, probe, seen, tokens, returned):
    """Return (passed, detail) for one named check from the spec.

    Parity failure and unavailable cache details raise StopRun, because every
    later comparison depends on them. Other failures are recorded and the
    sequence continues.
    """
    local = tokens[probe]
    cached = seen["cached_tokens"]
    if name == "parity":
        ids = seen["prompt_token_ids"]
        if ids is None:
            raise StopRun("prompt_token_ids absent")
        if ids != local:
            first = lcp(ids, local)
            raise StopRun(f"prompt ID parity mismatch at position {first} (engine {len(ids)}, local {len(local)})")
        if seen["usage_prompt_tokens"] is not None and seen["usage_prompt_tokens"] != len(local):
            raise StopRun("usage.prompt_tokens differs from returned ID count")
        return True, f"{len(ids)} IDs equal"
    if name.startswith("same_prompt_ids_as:"):
        other = returned.get(name.split(":", 1)[1])
        if other is None:
            return False, "reference step has no returned IDs"
        return seen["prompt_token_ids"] == other, "compared with " + name.split(":", 1)[1]
    if cached is None:
        raise StopRun("cached_tokens unavailable; recorded as unknown, not zero")
    if name == "cached_zero":
        return cached == 0, f"cached {cached}"
    if name == "cached_positive":
        return cached > 0, f"cached {cached}"
    if name == "cached_within_eligible":
        return cached <= len(local) - 1, f"cached {cached}, eligible {len(local) - 1}"
    if name.startswith("cached_within_lcp:"):
        other = name.split(":", 1)[1]
        bound = lcp(tokens[other], local)
        return cached <= bound, f"cached {cached}, local LCP with {other} {bound}"
    raise InputError("unknown check " + name)


def plan(spec):
    """Return the ordered step list shown by a dry run."""
    return [{"index": i, "step": s["step"], "action": s["action"], "probe": s.get("probe"),
             "route": s.get("route"), "checks": s.get("checks", [])} for i, s in enumerate(spec["sequence"])]


def run(spec, spec_sha, bodies, tokens, manifest, direct, suez, out, timeout=120.0):
    """Execute the sequence once and return the result summary.

    Each step record is appended to steps.jsonl and synced before the next
    request is sent, so an interrupted run still leaves an exact prefix of what
    happened. Raw response bytes are written before they are parsed.
    """
    run_id = manifest["run"]["run_id"]
    out = Path(out)
    out.mkdir(mode=0o700, parents=True)
    (out / "raw").mkdir(mode=0o700)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    client = Client(timeout)
    returned, failures, stop = {}, [], None
    with open(out / "steps.jsonl", "a", encoding="utf-8") as log:
        for index, step in enumerate(spec["sequence"]):
            record = {"index": index, "step": step["step"], "action": step["action"]}
            try:
                _step(client, step, index, run_id, direct, suez, bodies, tokens, returned, out, record)
                failures.extend(f"{step['step']}: {c['check']}" for c in record.get("checks", []) if not c["passed"])
            except StopRun as error:
                record["stopped"] = str(error)
                stop = {"step": step["step"], "reason": str(error)}
            log.write(json.dumps(record) + "\n")
            log.flush()
            os.fsync(log.fileno())
            if stop:
                break
    status = "stopped" if stop else ("completed_with_failures" if failures else "completed")
    result = {"schema_version": 1, "run_id": run_id, "status": status, "stop": stop, "failed_checks": failures,
              "spec_sha256": spec_sha, "direct": direct, "suez": suez,
              "note": "Suez capture integrity for the proxy step is checked separately with analysis/report.py."}
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def _json(body, what):
    """Parse a response body, turning malformed JSON into a stop rather than a crash."""
    try:
        return json.loads(body)
    except ValueError as error:
        raise StopRun(what + " response is not JSON") from error


def _step(client, step, index, run_id, direct, suez, bodies, tokens, returned, out, record):
    """Perform one action, filling record in place; raise StopRun on a stop rule.

    Filling the caller's record keeps fields observed before a stop, such as the
    status and returned counts, in the persisted step log.
    """
    prefix = out / "raw" / f"{index:02d}-{step['step']}"
    action = step["action"]
    if action == "get_models":
        status, headers, body, elapsed = client.exchange("GET", direct + "/v1/models")
        prefix.with_suffix(".response.body").write_bytes(body)
        record.update(status=status, client_round_trip_ms=elapsed)
        if status != 200:
            raise StopRun(f"models status {status}")
        record["served_models"] = [m.get("id") for m in _json(body, "models").get("data", [])]
        return
    if action == "reset_prefix_cache":
        status, headers, body, elapsed = client.exchange("POST", direct + "/reset_prefix_cache", data=b"")
        prefix.with_suffix(".response.body").write_bytes(body)
        success = _json(body, "reset").get("success") if status == 200 else None
        record.update(status=status, reset_success=success, client_round_trip_ms=elapsed)
        if status != 200 or success is not True:
            raise StopRun(f"reset failed: status {status}, success {success!r}")
        return
    if action != "send":
        raise InputError("unknown action " + action)
    probe = step["probe"]
    request_id = f"suez-m4-{run_id}-{index:02d}"
    headers = {"Content-Type": "application/json", "X-Request-Id": request_id}
    base = direct
    if step["route"] == "suez":
        base = suez
        headers.update({"X-Suez-Session": "m4-" + run_id, "X-Suez-Turn": str(index)})
    status, response_headers, body, elapsed = client.exchange("POST", base + "/v1/chat/completions", data=bodies[probe], headers=headers)
    prefix.with_suffix(".response.body").write_bytes(body)
    prefix.with_suffix(".response.headers.json").write_text(json.dumps(response_headers, indent=2) + "\n")
    record.update(probe=probe, route=step["route"], status=status, x_request_id=request_id,
                  suez_request_id=response_headers.get("X-Suez-Request-ID"), client_round_trip_ms=elapsed)
    if status != 200:
        raise StopRun(f"chat status {status}")
    _json(body, "chat")
    seen = observe(body)
    record["response_id_matches"] = seen["response_id"] == "chatcmpl-" + request_id
    record.update({k: v for k, v in seen.items() if k not in ("prompt_token_ids", "generated_token_ids")})
    record["generated_token_ids"] = seen["generated_token_ids"]
    returned[step["step"]] = seen["prompt_token_ids"]
    if not seen["prompt_tokens_details_present"]:
        raise StopRun("prompt_tokens_details absent; enable_prompt_tokens_details not effective")
    if not seen["metrics_present"]:
        raise StopRun("per-request metrics absent; enable_per_request_metrics not effective")
    record["checks"] = []
    for name in step.get("checks", []):
        try:
            passed, detail = evaluate(name, probe, seen, tokens, returned)
        except StopRun as error:
            record["checks"].append({"check": name, "passed": False, "detail": str(error)})
            raise StopRun(str(error)) from error
        record["checks"].append({"check": name, "passed": passed, "detail": detail})


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", default=HERE / "probes.json", type=Path)
    parser.add_argument("--probes", required=True, type=Path, help="directory written by build_probes.py")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--direct", required=True, help="vLLM origin, http://127.0.0.1:PORT")
    parser.add_argument("--suez", required=True, help="Suez proxy origin forwarding to the same server")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--timeout", default=120.0, type=float)
    parser.add_argument("--run", action="store_true", help="send requests; default is a dry run")
    args = parser.parse_args()
    direct, suez = origin(args.direct), origin(args.suez)
    if direct == suez:
        raise InputError("direct and Suez endpoints must differ")
    spec, spec_sha, bodies, tokens = load_inputs(args.spec, args.probes)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not args.run:
        missing, conflicts = check_manifest(manifest)
        print(json.dumps({"dry_run": True, "spec_sha256": spec_sha, "manifest_missing": missing,
                          "manifest_conflicts": conflicts, "steps": plan(spec)}, indent=2))
        return 0
    check_ready(manifest, spec, spec_sha)
    if args.out.exists():
        raise InputError("output directory already exists")
    result = run(spec, spec_sha, bodies, tokens, manifest, direct, suez, args.out, args.timeout)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
