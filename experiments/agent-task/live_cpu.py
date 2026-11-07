"""Capture a real mini-swe-agent session against the local CPU vLLM through Suez.

The pinned DefaultAgent loop, its text-based templates and the direct HTTP
adapter build every request; Qwen3-0.6B on the vLLM CPU backend answers. The
environment records each action and returns one fixed observation, so no
command is executed and observations do not depend on the action. Requests
therefore carry real harness serialization of real model output, which is what
prefix reuse depends on. Task success is not measured.

The vLLM server must already be listening on --engine. This script builds and
owns only the Suez proxy and the agent child. The child gets no inherited
credentials and an audit hook that allows connections only to the proxy.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from wire_check import check_dependencies, clean_environment, network_guard, stop, verify_checkout, write_json  # noqa: E402

TEMPLATE_FILE = "src/minisweagent/config/mini_textbased.yaml"
OBSERVATION = "This capture records actions without executing them. No output is available."


def block_scalar(text, key):
    """Return a YAML literal block (`key: |`) under `agent:` without a YAML library.

    The pinned file's templates are plain literal blocks indented by four spaces.
    Anything else raises rather than guessing at YAML semantics.
    """
    lines = text.splitlines()
    start = lines.index("  %s: |" % key) + 1
    body = []
    for line in lines[start:]:
        if line.startswith("    ") or line == "":
            body.append(line[4:])
        else:
            break
    while body and body[-1] == "":
        body.pop()
    if not body:
        raise ValueError("empty template " + key)
    return "\n".join(body) + "\n"


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


class FixedObservationEnvironment:
    """Record actions and return one fixed observation; never execute anything.

    Template variables come from the spec rather than the host's uname, so the
    prompt is deterministic and describes the stated task container.
    """

    def __init__(self, template_vars):
        self.actions = []
        self.template_vars = dict(template_vars)

    def execute(self, action, cwd=""):
        self.actions.append(action)
        return {"output": OBSERVATION, "returncode": 0, "exception_info": ""}

    def get_template_vars(self, **kwargs):
        return dict(self.template_vars, **kwargs)

    def serialize(self):
        return {"environment": {"actions_recorded": len(self.actions), "commands_executed": False}}


def worker(checkout, out):
    """Run the agent in the isolated interpreter; keep evidence on failure too."""
    spec = json.loads((out / "effective-spec.json").read_text())
    denied = []
    sys.addaudithook(network_guard(int(spec["proxy"]["listen"].split(":")[1]), denied))
    import importlib.metadata
    inventory = sorted(({"name": d.metadata["Name"], "version": d.version} for d in importlib.metadata.distributions()),
                       key=lambda item: item["name"].lower())
    write_json(out / "installed-packages.json", inventory)
    result = {"status": "failed", "commands_executed": False}
    env = FixedObservationEnvironment(spec["environment"]["template_vars"])
    agent = None
    try:
        result["locked_packages_verified"] = check_dependencies(inventory, (HERE / "wire-requirements.lock").read_text())
        sys.path.insert(0, str(checkout / "src"))
        from minisweagent.agents.default import DefaultAgent
        from direct_model import DirectTextModel

        text = (checkout / TEMPLATE_FILE).read_text()
        model = DirectTextModel(**spec["model_config"])
        agent = DefaultAgent(model, env, system_template=block_scalar(text, "system_template"),
                             instance_template=block_scalar(text, "instance_template"),
                             **spec["agent_bounds"], output_path=out / "trajectory.json")
        exit_info = agent.run((HERE / "prompt.txt").read_text())
        result.update(status="completed", exit_status=exit_info.get("exit_status"))
    except Exception as error:
        # A truncated or malformed reply ends the session; the requests already
        # captured are still valid evidence of the turns that happened.
        result.update(status="ended_by_error", error_type=type(error).__name__, error=str(error)[:2000])
    finally:
        result.update(agent_calls=getattr(agent, "n_calls", None), actions=env.actions, blocked_network_attempts=denied)
        write_json(out / "worker-result.json", result)


def run(checkout, python, out, engine, spec_path):
    """Verify pins, start Suez in front of the running engine and run one session."""
    manifest = json.loads((HERE / "manifest.json").read_text())
    harness_hashes = verify_checkout(checkout, manifest)
    spec = json.loads(spec_path.read_text())
    template_blob = git_blob((checkout / TEMPLATE_FILE).read_bytes())
    if template_blob != spec["template_git_blob_sha"]:
        raise ValueError("text-based template file differs from the recorded blob")
    with build_opener(ProxyHandler({})).open(engine + "/v1/models", timeout=5) as reply:
        served = [m["id"] for m in json.loads(reply.read())["data"]]
    if spec["model_config"]["model_name"] not in served:
        raise ValueError("engine does not serve the configured model")
    out.mkdir(mode=0o700, parents=True)
    home = out / "home"
    (home / "mini-config").mkdir(mode=0o700, parents=True)
    spec["proxy"]["upstream"] = engine
    write_json(out / "effective-spec.json", spec)
    binary = out / "cachino"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/cachino"], cwd=ROOT,
                   env=dict(os.environ, GOTOOLCHAIN="local"), check=True, timeout=120)
    write_json(out / "provenance.json", {
        "harness_revision": manifest["harness"]["revision"], "harness_source_sha256": harness_hashes,
        "template_git_blob_sha": template_blob, "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "suez_commit": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "served_models": served, "commands_executed": False})
    proxy_spec = spec["proxy"]
    host, port = proxy_spec["listen"].split(":")
    command = [str(binary), "-listen", proxy_spec["listen"], "-upstream", engine, "-trace-dir", str(out / "capture"),
               "-session", proxy_spec["session"], "-session-protocol", "openai",
               "-session-request-limit", str(proxy_spec["session_request_limit"]),
               "-session-input-bytes", str(proxy_spec["session_input_bytes"]),
               "-session-output-tokens", str(proxy_spec["session_output_tokens"]),
               "-session-model", spec["model_config"]["model_name"]]
    proxy = None
    try:
        with (out / "proxy.log").open("x") as proxy_log, (out / "worker.log").open("x") as worker_log:
            proxy = subprocess.Popen(command, cwd=out, stdout=proxy_log, stderr=subprocess.STDOUT,
                                     env=clean_environment(home, python))
            deadline = time.monotonic() + 5
            while True:
                if proxy.poll() is not None:
                    raise RuntimeError("proxy exited before readiness")
                try:
                    with socket.create_connection((host, int(port)), timeout=0.1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("proxy did not become ready")
                    time.sleep(0.05)
            subprocess.run([str(python), "-I", "-B", str(Path(__file__).resolve()), "--worker",
                            "--harness", str(checkout), "--out", str(out)],
                           cwd=home, env=clean_environment(home, python), stdout=worker_log,
                           stderr=subprocess.STDOUT, check=True, timeout=spec["agent_bounds"]["wall_time_limit_seconds"] + 120)
    finally:
        stop(proxy)
    if verify_checkout(checkout, manifest) != harness_hashes:
        raise ValueError("harness changed during the session")
    sys.path.insert(0, str(ROOT / "analysis"))
    from report import inspect
    write_json(out / "capture-report.json", inspect(out / "capture"))
    return json.loads((out / "worker-result.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--harness", type=Path, default=ROOT / ".project/checkouts/mini-swe-agent")
    parser.add_argument("--python", type=Path, default=ROOT / ".project/envs/mini-wire/bin/python")
    parser.add_argument("--spec", type=Path, default=HERE / "live-cpu.json")
    parser.add_argument("--engine", default="http://127.0.0.1:18000")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.harness.resolve(), args.out.resolve())
        return
    if not args.run:
        print(json.dumps({"dry_run": True, "spec": json.loads(args.spec.read_text())}, indent=2))
        return
    print(json.dumps(run(args.harness.resolve(), args.python, args.out.resolve(), args.engine, args.spec), indent=2))


if __name__ == "__main__":
    main()
