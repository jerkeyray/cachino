"""Capture a smolagents ToolCallingAgent session against the CPU vLLM through Cachino.

A second harness for the same frozen task. smolagents sends structured tool
definitions with every request and asks for a tool call (tool_choice
"required"); vLLM parses Qwen3's <tool_call> output into structured calls. In
history, smolagents re-serializes each call as assistant text ("Calling
tools: ...") and each result as a user message ("Observation: ..."), unlike
mini-swe-agent, which replays the model's text reply.

The agent's tools are read-only and backed by readonly_env.py over the task's
baseline tree: list a directory, read a line range, search. Nothing is
executed. The worker runs in the smolagents environment with no inherited
credentials and a network guard that allows only this run's proxy.
"""
import argparse
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

from wire_check import clean_environment, network_guard, stop, write_json  # noqa: E402


def build_tools(files):
    """Return smolagents tools that answer from the frozen snapshot."""
    import shlex
    from readonly_env import ReadOnlyFiles, respond
    from smolagents import tool

    fs = ReadOnlyFiles(files)

    def answer(command):
        result = respond(fs, command)
        return "returncode %d\n%s" % (result["returncode"], result["output"])

    @tool
    def list_files(path: str) -> str:
        """List the entries of a directory in the repository.

        Args:
            path: Directory path relative to the repository root, such as "." or "more_itertools".
        """
        return answer("ls " + shlex.quote(path))

    @tool
    def read_file(path: str, start_line: int, end_line: int) -> str:
        """Read numbered lines from a file in the repository.

        Args:
            path: File path relative to the repository root.
            start_line: First line to show, starting at 1.
            end_line: Last line to show.
        """
        return answer("nl -ba %s | sed -n '%d,%dp'" % (shlex.quote(path), int(start_line), int(end_line)))

    @tool
    def search(pattern: str, path: str) -> str:
        """Search files for a regular expression and show matching lines with line numbers.

        Args:
            pattern: Regular expression to search for.
            path: File or directory to search, relative to the repository root.
        """
        return answer("grep -rn %s %s" % (shlex.quote(pattern), shlex.quote(path)))

    return [list_files, read_file, search]


def worker(out):
    """Run the agent in the smolagents environment; keep evidence on failure."""
    spec = json.loads((out / "effective-spec.json").read_text())
    denied = []
    sys.addaudithook(network_guard(int(spec["proxy"]["listen"].split(":")[1]), denied))
    result = {"status": "failed", "commands_executed": False}
    agent = None
    try:
        from smolagents import OpenAIServerModel, ToolCallingAgent
        files = json.loads((out / "task-snapshot.json").read_text())["files"]
        config = spec["model_config"]
        model = OpenAIServerModel(
            model_id=config["model_name"], api_base=config["api_base"], api_key="local-placeholder",
            flatten_messages_as_text=True,
            # One attempt per request, like the mini-swe-agent adapter; the label
            # keeps this session distinct when a proxy is shared.
            client_kwargs={"max_retries": 0, "timeout": config["timeout"],
                           "default_headers": {"X-Cachino-Session": spec["proxy"]["session"]}},
            temperature=config["temperature"], max_tokens=config["max_tokens"])
        agent = ToolCallingAgent(tools=build_tools(files), model=model, max_steps=spec["agent_bounds"]["step_limit"],
                                 verbosity_level=0)
        result["started_at"] = time.time()
        answer = agent.run((HERE / "prompt.txt").read_text())
        result.update(status="completed", final_answer=str(answer)[:2000])
    except Exception as error:
        result.update(status="ended_by_error", error_type=type(error).__name__, error=str(error)[:2000])
    finally:
        steps = getattr(getattr(agent, "memory", None), "steps", []) if agent else []
        calls = [{"name": c.name, "arguments": c.arguments} for s in steps for c in (getattr(s, "tool_calls", None) or [])]
        result.update(steps=len(steps), tool_calls=calls, blocked_network_attempts=denied, ended_at=time.time())
        write_json(out / "worker-result.json", result)


def run(out, engine, spec_path, python):
    """Start Cachino in front of the engine and run one smolagents session."""
    from live_cpu import load_snapshot
    spec = json.loads(spec_path.read_text())
    with build_opener(ProxyHandler({})).open(engine + "/v1/models", timeout=5) as reply:
        served = [m["id"] for m in json.loads(reply.read())["data"]]
    if spec["model_config"]["model_name"] not in served:
        raise ValueError("engine does not serve the configured model")
    out.mkdir(mode=0o700, parents=True)
    manifest = json.loads((HERE / "manifest.json").read_text())
    revision, files = load_snapshot(manifest, ROOT / spec["environment"]["task_checkout"])
    write_json(out / "task-snapshot.json", {"revision": revision, "files": files})
    home = out / "home"
    home.mkdir(mode=0o700)
    write_json(out / "effective-spec.json", spec)
    binary = out / "cachino"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/cachino"], cwd=ROOT,
                   env=dict(os.environ, GOTOOLCHAIN="local"), check=True, timeout=120)
    host, port = spec["proxy"]["listen"].split(":")
    command = [str(binary), "-listen", spec["proxy"]["listen"], "-upstream", engine,
               "-trace-dir", str(out / "capture"), "-session", spec["proxy"]["session"]]
    proxy = None
    try:
        with (out / "proxy.log").open("x") as proxy_log, (out / "worker.log").open("x") as worker_log:
            proxy = subprocess.Popen(command, stdout=proxy_log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 10
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
            env = clean_environment(home, python)
            env["PYTHONPATH"] = str(HERE)
            subprocess.run([str(python), "-B", str(Path(__file__).resolve()), "--worker", "--out", str(out)],
                           cwd=home, env=env, stdout=worker_log, stderr=subprocess.STDOUT, check=True,
                           timeout=spec["agent_bounds"]["wall_time_limit_seconds"] + 120)
    finally:
        stop(proxy)
    sys.path.insert(0, str(ROOT / "analysis"))
    from report import inspect
    write_json(out / "capture-report.json", inspect(out / "capture"))
    return json.loads((out / "worker-result.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--spec", type=Path, default=HERE / "smol-cpu.json")
    parser.add_argument("--engine", default="http://127.0.0.1:18000")
    parser.add_argument("--python", type=Path, default=ROOT / ".project/envs/smolagents/bin/python")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.out.resolve())
        return
    if not args.run:
        print(json.dumps({"dry_run": True, "spec": json.loads(args.spec.read_text())}, indent=2))
        return
    print(json.dumps(run(args.out.resolve(), args.engine, args.spec, args.python), indent=2))


if __name__ == "__main__":
    main()
