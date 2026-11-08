"""Run agent commands for real, inside a sandboxed throwaway copy of the task.

Each command runs with /bin/bash under macOS sandbox-exec, in a fresh copy of
the task's baseline tree. The sandbox profile denies all network access,
denies writes everywhere except the workspace and its own temp directory, and
denies reads under /Users except the workspace and the Python installation the
agent may use. Commands get a minimal environment with no credentials, a
timeout and an output cap.

The frozen task tells the agent its repository is at /task, which cannot be
created on macOS. Commands have /task rewritten to the workspace path before
they run, and outputs have the workspace path rewritten back, so the agent
sees the paths the task describes.
"""
import os
from pathlib import Path
import shutil
import subprocess

SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
OUTPUT_CAP = 100_000


def profile(workspace, scratch, python_root):
    """Return the sandbox-exec profile for one workspace."""
    return """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{w}") (subpath "{s}") (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/fd/"))
(deny file-read* (subpath "/Users"))
(allow file-read* (subpath "{w}") (subpath "{s}") (subpath "{p}"))
""".format(w=workspace, s=scratch, p=python_root)


class SandboxEnvironment:
    """mini-swe-agent environment protocol, backed by sandboxed execution."""

    def __init__(self, root, files, python, template_vars, timeout=60):
        root = Path(root).resolve()
        self.workspace = root / "task"
        self.scratch = root / "tmp"
        if self.workspace.exists():
            raise FileExistsError(self.workspace)
        for rel, text in files.items():
            path = self.workspace / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.scratch.mkdir(parents=True)
        # Expose the interpreter as `python` and `python3` without the rest of PATH.
        self.bin = root / "bin"
        self.bin.mkdir()
        python = Path(python).resolve()
        for name in ("python", "python3"):
            (self.bin / name).symlink_to(python)
        self.python_root = python.parents[1]
        self.timeout = timeout
        self.template_vars = dict(template_vars)
        self.actions = []

    def _profile(self):
        return profile(self.workspace, self.scratch, self.python_root)

    def run(self, command):
        """Run one command; return {"output", "returncode", "exception_info"}."""
        command = command.replace("/task", str(self.workspace))
        env = {"PATH": "%s:/usr/bin:/bin" % self.bin, "HOME": str(self.scratch), "TMPDIR": str(self.scratch),
               "LANG": "en_US.UTF-8", "PAGER": "cat", "MANPAGER": "cat", "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            done = subprocess.run(["sandbox-exec", "-p", self._profile(), "/bin/bash", "-c", command],
                                  cwd=self.workspace, env=env, capture_output=True, timeout=self.timeout)
            output = (done.stdout + done.stderr).decode("utf-8", errors="replace")
            result = {"returncode": done.returncode, "exception_info": ""}
        except subprocess.TimeoutExpired as error:
            output = ((error.stdout or b"") + (error.stderr or b"")).decode("utf-8", errors="replace")
            result = {"returncode": -1, "exception_info": "command timed out after %d s" % self.timeout}
        if len(output) > OUTPUT_CAP:
            output = output[:OUTPUT_CAP] + "\n[output truncated]\n"
        result["output"] = output.replace(str(self.workspace), "/task")
        return result

    def execute(self, action, cwd=""):
        from minisweagent.exceptions import Submitted
        command = action.get("command", "") if isinstance(action, dict) else str(action)
        self.actions.append(action)
        result = self.run(command)
        lines = result["output"].lstrip().splitlines()
        if lines and lines[0].strip() == SUBMIT and result["returncode"] == 0:
            raise Submitted({"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": ""}})
        return result

    def get_template_vars(self, **kwargs):
        return dict(self.template_vars, **kwargs)

    def serialize(self):
        return {"environment": {"actions_recorded": len(self.actions), "commands_executed": True,
                                "sandbox": "sandbox-exec: no network, writes only in workspace"}}

    def cleanup(self):
        shutil.rmtree(self.scratch, ignore_errors=True)
