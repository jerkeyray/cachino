"""Run several live agent sessions at once against one CPU vLLM server.

Each session is one live_cpu.run with its own Cachino proxy port, capture
directory and session label, all forwarding to the same engine and so sharing
one prefix cache. Sessions start together and run in threads; each owns its
proxy and agent child exactly as a single session does. The base spec supplies
everything except the proxy port and label, which are derived per session.

Concurrency here means overlapping requests from independent sessions, the
case where one session's traffic can evict another's cached prefix.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import threading

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from live_cpu import run as run_session  # noqa: E402


def session_specs(base, count, first_port):
    """Return one spec per session: distinct proxy port, api_base and label."""
    specs = []
    for index in range(count):
        spec = copy.deepcopy(base)
        port = first_port + index
        spec["proxy"]["listen"] = "127.0.0.1:%d" % port
        spec["proxy"]["session"] = "%s-%d" % (base["proxy"]["session"], index)
        spec["model_config"]["api_base"] = "http://127.0.0.1:%d/v1" % port
        specs.append(spec)
    return specs


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, required=True, help="base live session spec")
    parser.add_argument("--sessions", type=int, default=3)
    parser.add_argument("--first-port", type=int, default=18090)
    parser.add_argument("--engine", default="http://127.0.0.1:18000")
    parser.add_argument("--harness", type=Path, default=ROOT / ".project/checkouts/mini-swe-agent")
    parser.add_argument("--python", type=Path, default=ROOT / ".project/envs/mini-wire/bin/python")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(mode=0o700, parents=True)
    base = json.loads(args.spec.read_text())
    results, threads = {}, []
    for index, spec in enumerate(session_specs(base, args.sessions, args.first_port)):
        path = out / ("spec-%d.json" % index)
        path.write_text(json.dumps(spec, indent=2) + "\n")

        def target(index=index, path=path):
            try:
                results[index] = run_session(args.harness.resolve(), args.python, out / ("session-%d" % index),
                                             args.engine, path)
            except Exception as error:  # keep the other sessions' evidence
                results[index] = {"status": "driver_error", "error": "%s: %s" % (type(error).__name__, error)}

        threads.append(threading.Thread(target=target))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    (out / "result.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({i: {k: r.get(k) for k in ("status", "exit_status", "error", "agent_calls")}
                      for i, r in sorted(results.items())}, indent=2))


if __name__ == "__main__":
    main()
