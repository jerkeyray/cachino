"""Run one opt-in, bounded Claude Code capture against a frozen read-only task.

The default prints bounds only. --send loads the local key, builds Suez, starts
its limited proxy, runs Claude with only Read, and closes owned child processes.
Raw CLI logs and traces remain private; neither credentials nor raw logs print.
"""

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
TASK = Path(__file__).resolve().parent / "task"


def load_key():
    """Read one literal dotenv assignment without executing shell contents."""
    key = None
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if separator and name.strip() == "DEEPSEEK_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            key = value
    if not key or any(c.isspace() for c in key):
        raise ValueError("missing or invalid API key assignment")
    return key


def stop(process):
    """Stop a child before closing its log files; bound graceful shutdown."""
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def snapshot_sources(out):
    """Copy an explicit source allowlist, never dotenv, traces or credentials.

    Store the implementation used for building/reporting and the frozen task,
    so a later edit does not make an old manifest's hashes unrecoverable.
    """
    files = [ROOT / "go.mod", Path(__file__), ROOT / "analysis/report.py"]
    files += [path for folder in (ROOT / "cmd", ROOT / "internal") for path in folder.rglob("*.go")]
    files += [path for path in TASK.iterdir() if path.is_file() and path.suffix in (".py", ".txt")]
    hashes = {}
    for path in files:
        relative = path.relative_to(ROOT)
        target = out / "source" / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = path.read_bytes()
        target.write_bytes(data)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="run up to four billable upstream requests")
    parser.add_argument("--out", type=Path, default=ROOT / "traces" / ("claude-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())))
    args = parser.parse_args()
    bounds = {"maximum_upstream_requests": 4, "maximum_request_bytes": 131072,
              "maximum_requested_output_tokens_per_call": 512, "model": "deepseek-flash",
              "client_timeout_seconds": 120, "tools": ["Read"], "mode": "live" if args.send else "dry_run"}
    if not args.send:
        print(json.dumps(bounds, indent=2))
        return
    key = load_key()
    out = args.out.resolve()
    out.mkdir(mode=0o700, parents=True)
    # All outputs below inherit private permissions, including subprocess logs.
    os.umask(0o077)
    source_hashes = snapshot_sources(out)
    binary = out / "suez"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/suez"], cwd=ROOT, check=True)
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest for name, digest in source_hashes.items()):
        raise RuntimeError("source changed during build; run not dispatched")
    command = [str(binary), "-listen", "127.0.0.1:18081", "-upstream", "https://api.deepseek.com",
               "-messages-prefix", "/anthropic", "-trace-dir", str(out / "capture"),
               "-session", "claude-readonly", "-session-request-limit", "4",
               "-session-output-tokens", "512", "-session-input-bytes", "131072"]
    claude = ["claude", "--print", "--safe-mode", "--setting-sources", "", "--model", "deepseek-flash",
              "--tools", "Read", "--allowedTools", "Read(./fixture.py)", "--permission-mode", "dontAsk",
              "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
              "--no-session-persistence", "--output-format", "json"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_")) and k != "DEEPSEEK_API_KEY"}
    env.update({"ANTHROPIC_BASE_URL": "http://127.0.0.1:18081", "ANTHROPIC_AUTH_TOKEN": key,
                "ANTHROPIC_MODEL": "deepseek-flash", "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-flash",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-flash", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash",
                "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-flash", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "512",
                "CLAUDE_CODE_MAX_RETRIES": "0", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "MAX_THINKING_TOKENS": "0"})
    manifest = {**bounds, "proxy_command": command, "claude_command": claude,
                "cli_version": subprocess.check_output(["claude", "--version"], text=True).strip(),
                "files_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in TASK.iterdir() if p.is_file()},
                "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "credential_source": ".env DEEPSEEK_API_KEY (value omitted)", "engine_validated": False}
    manifest["source_snapshot_sha256"] = source_hashes
    manifest["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    report_python = shutil.which("python3.14") or sys.executable
    manifest["report_python"] = report_python
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    proxy = agent = None
    with (out / "proxy.log").open("w") as proxy_log, (out / "claude.json").open("w") as result, (out / "claude.stderr").open("w") as errors:
        try:
            proxy = subprocess.Popen(command, cwd=ROOT, stdout=proxy_log, stderr=proxy_log)
            for _ in range(50):
                if proxy.poll() is not None:
                    raise RuntimeError("proxy startup failed")
                try:
                    with socket.create_connection(("127.0.0.1", 18081), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("proxy readiness timeout")
            # Never attach credentials to a pre-existing listener if bind failed.
            time.sleep(0.1)
            if proxy.poll() is not None:
                raise RuntimeError("proxy exited before dispatch")
            agent = subprocess.Popen(claude, cwd=TASK, env=env, stdin=subprocess.PIPE, stdout=result, stderr=errors, text=True)
            try:
                agent.communicate((TASK / "prompt.txt").read_text(), timeout=120)
            except subprocess.TimeoutExpired:
                stop(agent)
                raise RuntimeError("Claude timeout; no automatic rerun")
            manifest["cli_exit_code"] = agent.returncode
        finally:
            stop(agent)
            stop(proxy)
            (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with (out / "report.json").open("w") as report:
        subprocess.run([report_python, str(ROOT / "analysis/report.py"), str(out / "capture"),
                        "--sqlite", str(out / "index.sqlite")], stdout=report, check=True)
    print(json.dumps({"output": str(out), "cli_exit_code": agent.returncode, "bounds": bounds}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        sys.exit("Capture stopped; inspect private logs. No automatic rerun. Credentials not printed.")
