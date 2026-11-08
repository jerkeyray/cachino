"""Run several live agent sessions at once against one CPU vLLM server.

All sessions forward to the same engine and so share one prefix cache. Two
modes:

- separate proxies (default): each session is one live_cpu.run with its own
  Cachino proxy port, capture directory and session label;
- shared proxy (--shared-proxy): one Cachino instance, built and owned here,
  serves every session, which sends its own X-Cachino-Session label. Its
  capture holds all sessions, and --max-active N turns on the proxy's
  active-session limit, so the limit's effect can be measured live.
  --token-budget T turns on its size limit instead or as well: younger
  sessions wait while active sessions' estimated footprints exceed T tokens.

Sessions start together and run in threads; each records its wall-clock start
and end in its worker result.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from live_cpu import run as run_session  # noqa: E402


def session_specs(base, count, first_port, shared_port=None):
    """Return one spec per session: its own label, and its own or the shared port."""
    specs = []
    for index in range(count):
        spec = copy.deepcopy(base)
        port = shared_port or first_port + index
        label = "%s-%d" % (base["proxy"]["session"], index)
        spec["proxy"]["listen"] = "127.0.0.1:%d" % port
        spec["proxy"]["session"] = label
        spec["model_config"]["api_base"] = "http://127.0.0.1:%d/v1" % port
        if shared_port:
            spec["model_config"]["session"] = label
        specs.append(spec)
    return specs


def start_shared_proxy(out, engine, port, max_active, idle, budget=0, per_byte=0.25):
    """Build Cachino and start it in front of the engine; return the process."""
    binary = out / "cachino"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/cachino"], cwd=ROOT,
                   env=dict(os.environ, GOTOOLCHAIN="local"), check=True, timeout=120)
    command = [str(binary), "-listen", "127.0.0.1:%d" % port, "-upstream", engine,
               "-trace-dir", str(out / "capture"), "-session", "unlabeled"]
    if max_active:
        command += ["-max-active-sessions", str(max_active)]
    if budget:
        command += ["-session-token-budget", str(budget), "-tokens-per-byte", str(per_byte)]
    if max_active or budget:
        command += ["-session-idle", idle]
    log = (out / "proxy.log").open("x")
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 10
    while True:
        if process.poll() is not None:
            raise RuntimeError("shared proxy exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return process
        except OSError:
            if time.monotonic() > deadline:
                process.terminate()
                raise TimeoutError("shared proxy did not become ready")
            time.sleep(0.05)


def waits_from_log(path):
    """Parse the active-session limiter's wait lines from the proxy log."""
    pattern = re.compile(r"activelimit wait session=(\S+) request=(\S*) waited_ms=([\d.]+)")
    return [{"session": m.group(1), "request_id": m.group(2), "waited_ms": float(m.group(3))}
            for m in pattern.finditer(Path(path).read_text())]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, required=True, help="base live session spec")
    parser.add_argument("--sessions", type=int, default=3)
    parser.add_argument("--first-port", type=int, default=18090)
    parser.add_argument("--shared-proxy", action="store_true")
    parser.add_argument("--max-active", type=int, default=0, help="with --shared-proxy: active-session limit")
    parser.add_argument("--token-budget", type=int, default=0, help="with --shared-proxy: KV budget in tokens")
    parser.add_argument("--tokens-per-byte", type=float, default=0.25)
    parser.add_argument("--session-idle", default="5s")
    parser.add_argument("--engine", default="http://127.0.0.1:18000")
    parser.add_argument("--harness", type=Path, default=ROOT / ".project/checkouts/mini-swe-agent")
    parser.add_argument("--python", type=Path, default=ROOT / ".project/envs/mini-wire/bin/python")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if (args.max_active or args.token_budget) and not args.shared_proxy:
        parser.error("--max-active and --token-budget need --shared-proxy")
    out = args.out.resolve()
    out.mkdir(mode=0o700, parents=True)
    base = json.loads(args.spec.read_text())
    proxy = start_shared_proxy(out, args.engine, args.first_port, args.max_active, args.session_idle,
                               args.token_budget, args.tokens_per_byte) \
        if args.shared_proxy else None
    results, threads = {}, []
    started = time.time()
    try:
        specs = session_specs(base, args.sessions, args.first_port, args.first_port if args.shared_proxy else None)
        for index, spec in enumerate(specs):
            path = out / ("spec-%d.json" % index)
            path.write_text(json.dumps(spec, indent=2) + "\n")

            def target(index=index, path=path):
                try:
                    results[index] = run_session(args.harness.resolve(), args.python, out / ("session-%d" % index),
                                                 args.engine, path, external_proxy=args.shared_proxy)
                except Exception as error:  # keep the other sessions' evidence
                    results[index] = {"status": "driver_error", "error": "%s: %s" % (type(error).__name__, error)}

            threads.append(threading.Thread(target=target))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        if proxy is not None:
            proxy.terminate()
            proxy.wait(timeout=30)
    summary = {"shared_proxy": args.shared_proxy, "max_active": args.max_active, "token_budget": args.token_budget,
               "tokens_per_byte": args.tokens_per_byte, "session_idle": args.session_idle,
               "wall_clock_s": round(time.time() - started, 1), "sessions": results}
    if args.shared_proxy:
        summary["waits"] = waits_from_log(out / "proxy.log")
    (out / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({i: {k: r.get(k) for k in ("status", "exit_status", "error", "agent_calls")}
                      for i, r in sorted(results.items())}, indent=2))
    print("wall clock %.1f s, waits %d" % (summary["wall_clock_s"], len(summary.get("waits", []))))


if __name__ == "__main__":
    main()
