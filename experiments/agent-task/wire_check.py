"""Check the pinned harness/direct HTTP adapter without model inference.

The default prints a plan and imports no harness. --run uses an already prepared
checkout and interpreter; it never clones, installs packages or loads Suez's env.
Only the agent child imports the harness. Its environment and network guard keep
this compatibility check separate from credentials and agent shell execution.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ACTION = "```mswea_bash_command\nwire_fixture_action\n```"
SYSTEM = "Return exactly one mswea_bash_command action. This is a wire fixture."
TASK = "Record two fixture actions. No command will be executed."
OBSERVATION = "wire fixture: action recorded but not executed"


def write_json(path, value):
    """Create one private result without replacing earlier evidence."""
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def clean_environment(home, python):
    """Construct a child environment without inherited credentials or proxy URLs.

    The harness imports dotenv itself. Redirect its config and disable dotenv,
    rather than assuming that avoiding our own load_dotenv call is sufficient.
    """
    return {
        "HOME": str(home), "PATH": str(python.parent) + ":/usr/bin:/bin",
        "TMPDIR": str(home), "LANG": "en_US.UTF-8",
        "MSWEA_GLOBAL_CONFIG_DIR": str(home / "mini-config"),
        "MSWEA_SILENT_STARTUP": "1", "PYTHON_DOTENV_DISABLED": "1",
        "MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT": "1",
        "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false",
    }


def verify_checkout(checkout, manifest):
    """Require the inspected revision and byte-identical source files before import.

    Git commands are read-only. Also reject tracked edits and untracked files,
    which could otherwise shadow imports in the source checkout.
    """
    checkout = checkout.resolve()

    def git(*arguments):
        return subprocess.check_output(
            ["git", "-C", str(checkout), *arguments], text=True, timeout=10,
        ).strip()

    if git("rev-parse", "HEAD") != manifest["harness"]["revision"]:
        raise ValueError("harness checkout revision differs from the frozen pin")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("harness checkout must be clean")
    hashes = {}
    for item in manifest["harness"]["source_files"]:
        path = checkout / item["path"]
        if path.is_symlink() or checkout not in path.resolve().parents:
            raise ValueError("harness source escapes checkout")
        data = path.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if blob != item["git_blob_sha"]:
            raise ValueError("harness blob mismatch: " + item["path"])
        hashes[item["path"]] = hashlib.sha256(data).hexdigest()
    return hashes


def network_guard(port, denied):
    """Return a child audit hook allowing connections only to this run's proxy.

    This is an accidental-network guard, not an OS security sandbox. The fixture
    environment below never executes the model's action text.
    """
    def guard(event, arguments):
        if event == "socket.connect":
            address = arguments[1]
            if not (isinstance(address, tuple) and address[:2] == ("127.0.0.1", port)):
                denied.append(event)
                raise PermissionError("wire child can connect only to its local proxy")
        elif event == "socket.getaddrinfo":
            if arguments[0] != "127.0.0.1" or arguments[1] not in (port, str(port)):
                denied.append(event)
                raise PermissionError("external DNS disabled for wire child")
    return guard


def check_dependencies(inventory, lock_text):
    """Require the resolved names/versions before any harness import.

    The installer separately verifies distribution hashes with --require-hashes.
    This check catches a different or subsequently changed environment; it does
    not claim to hash every installed file.
    """
    def canonical(name):
        return re.sub(r"[-_.]+", "-", name).lower()

    rows = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", lock_text, re.MULTILINE)
    expected = {canonical(name): version for name, version in rows}
    actual = {canonical(item["name"]): item["version"] for item in inventory}
    if not expected or len(expected) != len(rows) or len(actual) != len(inventory) or actual != expected:
        raise ValueError("installed package names/versions differ from wire dependency lock")
    return len(expected)


class RecordingEnvironment:
    """Implement the harness environment protocol without executing commands."""

    def __init__(self):
        self.actions = []

    def execute(self, action, cwd=""):
        self.actions.append(action)
        return {"output": OBSERVATION, "returncode": 0, "exception_info": ""}

    def get_template_vars(self):
        return {}

    def serialize(self):
        return {"wire_environment": {"actions_recorded": len(self.actions), "commands_executed": False}}


def worker(checkout, out):
    """Run the actual two-step agent in the isolated interpreter child.

    Package inventory precedes third-party imports. Preserve failure evidence as
    well as successful output; dependency setup remains a separate user action.
    """
    spec = json.loads((out / "effective-spec.json").read_text())
    denied = []
    sys.addaudithook(network_guard(int(spec["proxy"]["listen"].split(":")[1]), denied))
    inventory = sorted(
        ({"name": dist.metadata["Name"], "version": dist.version}
         for dist in importlib.metadata.distributions()), key=lambda item: item["name"].lower(),
    )
    write_json(out / "installed-packages.json", inventory)
    result = {"status": "failed", "commands_executed": False, "inference_performed": False}
    try:
        result["locked_packages_verified"] = check_dependencies(
            inventory, (out / "source/experiments/agent-task/wire-requirements.lock").read_text(),
        )
        sys.path.insert(0, str(checkout / "src"))
        sys.path.insert(0, str(out / "source/experiments/agent-task"))
        from minisweagent.agents.default import DefaultAgent
        from direct_model import DirectTextModel

        for name, module in list(sys.modules.items()):
            if name.startswith("minisweagent") and getattr(module, "__file__", None):
                if checkout not in Path(module.__file__).resolve().parents:
                    raise ValueError("harness imported outside pinned checkout")
        env = RecordingEnvironment()
        model = DirectTextModel(**spec["model_config"])
        agent = DefaultAgent(
            model, env, system_template=SYSTEM, instance_template="{{task}}",
            **spec["agent_bounds"], output_path=out / "trajectory.json",
        )
        exit_info = agent.run(TASK)
        sdk_imports = sorted(name for name in sys.modules if name.split(".")[0] in ("litellm", "openai"))
        if sdk_imports:
            raise ValueError("direct transport unexpectedly imported a provider SDK")
        result.update(status="completed", exit_status=exit_info.get("exit_status"),
                      agent_calls=agent.n_calls, actions=env.actions, provider_sdk_imports=sdk_imports)
    except Exception as error:
        result["error_type"] = type(error).__name__
        raise
    finally:
        result["blocked_network_attempts"] = denied
        write_json(out / "worker-result.json", result)


def fixture_handler(exchanges):
    """Serve two synthetic OpenAI replies; retain bodies and safe header checks.

    No token usage is invented. An SDK may synthesize usage internally, so the
    raw HTTP reply is the source of truth for this fixture's unknown counts.
    """
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.connection.settimeout(5)
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/chat/completions" or not 0 < length <= 131072 or len(exchanges) >= 2:
                self.send_error(400)
                return
            body = self.rfile.read(length)
            response = json.dumps({
                "id": "chatcmpl-wire-%d" % (len(exchanges) + 1), "object": "chat.completion",
                "created": 0, "model": "wire-fixture",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": ACTION},
                             "finish_reason": "stop"}],
            }).encode()
            exchanges.append({"request": body, "response": response,
                              "placeholder_auth": self.headers.get("Authorization") == "Bearer wire-fixture-placeholder",
                              "suez_labels_absent": not any(key.lower().startswith("x-suez-") for key in self.headers)})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
    return Handler


def check_messages(requests, result):
    """Check observed append behavior without relaxing strict rendering support.

    Unknown wire fields remain visible. Report renderer rejection separately
    from transport success; do not silently remove nulls or normalize messages.
    """
    if result.get("status") != "completed" or result.get("exit_status") != "LimitsExceeded":
        raise ValueError("agent did not stop at its configured step limit")
    if result.get("agent_calls") != 2 or result.get("actions") != [{"command": "wire_fixture_action"}] * 2:
        raise ValueError("expected exactly two parsed fixture actions")
    if len(requests) != 2 or any(item.get("model") != "wire-fixture" for item in requests):
        raise ValueError("expected two requests to the fixture model")
    first, second = (item["messages"] for item in requests)
    initial = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": TASK}]
    if first != initial or len(second) != 4 or second[:2] != first:
        raise ValueError("initial messages changed or history did not append")
    if second[2].get("role") != "assistant" or second[2].get("content") != ACTION:
        raise ValueError("assistant response not preserved")
    expected_observation = "<returncode>0</returncode>\n<output>\n%s</output>" % OBSERVATION
    if second[3] != {"role": "user", "content": expected_observation}:
        raise ValueError("unexpected recorded observation")
    if any("extra" in msg for request in requests for msg in request["messages"]):
        raise ValueError("internal harness metadata reached the wire")
    return {"initial_messages_stable": True, "history_appended": True,
            "request_field_names": [sorted(request) for request in requests],
            "message_field_names": [[sorted(msg) for msg in request["messages"]] for request in requests]}


def stop(process):
    """Drain owned proxy capture before inspecting it; bound cleanup time."""
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run(checkout, python, out):
    """Build Suez, run owned local fixtures and verify captured agent exchanges.

    Existing paths are never reused. A failure leaves private logs and source
    snapshots for inspection and stops owned processes in all exit paths.
    """
    manifest = json.loads((HERE / "manifest.json").read_text())
    harness_hashes = verify_checkout(checkout, manifest)
    spec = json.loads((HERE / "wire-check.json").read_text())
    out.mkdir(mode=0o700, parents=True)
    home = out / "home"
    home.mkdir(mode=0o700)
    (home / "mini-config").mkdir(mode=0o700)
    source_hashes = {}
    sources = [HERE / name for name in ("wire_check.py", "direct_model.py", "wire-check.json", "manifest.json",
                                      "wire-requirements.in", "wire-requirements.lock")]
    sources += [ROOT / "go.mod", ROOT / "analysis/report.py", ROOT / "analysis/reconstruct.py", ROOT / "analysis/tokenizer_assets.py"]
    sources += [path for folder in (ROOT / "cmd", ROOT / "internal") for path in folder.rglob("*.go")]
    for path in sources:
        data = path.read_bytes()
        relative = path.relative_to(ROOT)
        target = out / "source" / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(data)
        source_hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    for name in harness_hashes:
        target = out / "harness-source" / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes((checkout / name).read_bytes())
    binary = out / "cachino"
    build_env = dict(os.environ, GOTOOLCHAIN="local", GOCACHE="/tmp/cachino-go-cache")
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/cachino"], cwd=ROOT,
                   env=build_env, check=True, timeout=60)
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest for name, digest in source_hashes.items()):
        raise ValueError("Suez source changed during build")
    exchanges = []
    server = HTTPServer(("127.0.0.1", 0), fixture_handler(exchanges))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    proxy = None
    try:
        # Reserve/check the configured proxy port before starting children. The
        # later capture and fixture comparisons also detect a mismatched owner.
        host, port = spec["proxy"]["listen"].split(":")
        with socket.socket() as reservation:
            # Match the HTTP listener's reuse behavior: a completed prior run
            # can leave TIME_WAIT sockets without leaving a live listener.
            reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            reservation.bind((host, int(port)))
        spec["proxy"]["upstream"] = "http://127.0.0.1:%d" % server.server_port
        write_json(out / "effective-spec.json", spec)
        write_json(out / "provenance.json", {
            "harness_revision": manifest["harness"]["revision"], "harness_source_sha256": harness_hashes,
            "suez_source_sha256": source_hashes, "python": str(python),
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "inference_performed": False, "engine_validated": False,
        })
        command = [str(binary), "-listen", spec["proxy"]["listen"], "-upstream", spec["proxy"]["upstream"],
                   "-trace-dir", str(out / "capture"), "-session", "mini-wire", "-session-protocol", "openai",
                   "-session-request-limit", "2", "-session-input-bytes", "131072",
                   "-session-output-tokens", "64", "-session-model", "wire-fixture"]
        thread.start()
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
                           stderr=subprocess.STDOUT, check=True, timeout=40)
            probe = Request("http://" + spec["proxy"]["listen"] + "/v1/chat/completions",
                            data=json.dumps({"model": "wire-fixture", "max_tokens": 1,
                                             "messages": [{"role": "user", "content": "quota probe"}]}).encode(),
                            headers={"Content-Type": "application/json", "X-Suez-Session": "mini-wire-quota-probe"})
            try:
                with build_opener(ProxyHandler({})).open(probe, timeout=5) as response:
                    quota_status = response.status
            except HTTPError as error:
                quota_status = error.code
                error.close()
            if quota_status != 403:
                raise ValueError("third request was not rejected by admission quota")
            stop(proxy)
            if proxy.returncode != 0:
                raise RuntimeError("proxy did not shut down cleanly")
        sys.path.insert(0, str(ROOT / "analysis"))
        from report import body_bytes, inspect, read_events
        from reconstruct import UnsupportedRequest, prompt_inputs

        records, warnings = read_events(out / "capture")
        if warnings or [row["http_status"] for row in records] != [200, 200, 403] or len(exchanges) != 2:
            raise ValueError("unexpected capture lifecycle, statuses or upstream count")
        if [row.get("session_id") for row in records] != ["mini-wire", "mini-wire", "mini-wire-quota-probe"]:
            raise ValueError("quota probe must remain separate from agent history")
        requests, render_support = [], []
        for index, record in enumerate(records):
            captured = []
            for name in ("request", "response"):
                data, integrity = body_bytes(out / "capture", record, name)
                if integrity != "verified":
                    raise ValueError("incomplete wire capture")
                captured.append(data)
                if index < 2 and data != exchanges[index][name]:
                    raise ValueError("forwarded bytes differ from fixture exchange")
            if index < 2:
                request = json.loads(captured[0])
                requests.append(request)
                try:
                    prompt_inputs(request, "wire-fixture")
                    render_support.append({"supported": True})
                except UnsupportedRequest as error:
                    render_support.append({"supported": False, "reason": str(error)})
        if not all(item["placeholder_auth"] and item["suez_labels_absent"] for item in exchanges):
            raise ValueError("unexpected auth or local-label forwarding")
        worker_result = json.loads((out / "worker-result.json").read_text())
        result = check_messages(requests, worker_result)
        if verify_checkout(checkout, manifest) != harness_hashes:
            raise ValueError("harness changed during wire check")
        if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest for name, digest in source_hashes.items()):
            raise ValueError("Suez/adapter source changed during wire check")
        result.update(status="wire_validated", transport_validated=True, sdk_validated=False, engine_validated=False,
                      inference_performed=False, commands_executed=False, upstream_requests=2,
                      quota_probe_status=quota_status, strict_renderer_support=render_support,
                      model_token_usage=None, cache_usage=None)
        result["locked_packages_verified"] = worker_result["locked_packages_verified"]
        result["blocked_network_attempts"] = worker_result["blocked_network_attempts"]
        result["provider_sdk_imports"] = worker_result["provider_sdk_imports"]
        write_json(out / "capture-report.json", inspect(out / "capture"))
        write_json(out / "result.json", result)
        return result
    finally:
        stop(proxy)
        if thread.is_alive():
            server.shutdown()
            thread.join(timeout=2)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="use already approved/prepared harness dependencies")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--harness", type=Path, default=ROOT / ".project/checkouts/mini-swe-agent")
    parser.add_argument("--python", type=Path, default=ROOT / ".project/envs/mini-wire/bin/python")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not args.run and not args.worker:
        print(json.dumps({"status": "dry_run", "transport_validated": False, "sdk_validated": False, "inference_performed": False,
                          "commands_executed": False, "external_setup_performed": False,
                          "next": "use --run with an approved pinned checkout and prepared isolated dependencies"}, indent=2))
        return
    if args.out is None:
        parser.error("--out must name a new private result directory")
    os.umask(0o077)
    if args.worker:
        worker(args.harness.resolve(), args.out.resolve())
    else:
        # Preserve the venv executable path: resolving its symlink would select
        # the base interpreter and lose the isolated dependency environment.
        result = run(args.harness.resolve(), args.python.absolute(), args.out.absolute())
        print(json.dumps({key: result[key] for key in ("status", "transport_validated", "engine_validated")}, indent=2))


if __name__ == "__main__":
    main()
