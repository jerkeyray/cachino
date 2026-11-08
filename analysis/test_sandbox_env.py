from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/agent-task"))

from sandbox_env import SandboxEnvironment  # noqa: E402

PYTHON = sys.executable


@unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox-exec required")
class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = SandboxEnvironment(Path(self.tmp.name) / "run", {"pkg/mod.py": "x = 1\n"}, PYTHON, {})

    def tearDown(self):
        self.tmp.cleanup()

    def test_commands_run_and_edit_inside_the_workspace(self):
        result = self.env.run("cd /task && sed -i '' 's/x = 1/x = 2/' pkg/mod.py && cat pkg/mod.py && pwd")
        self.assertEqual(result["returncode"], 0, result)
        self.assertIn("x = 2", result["output"])
        self.assertIn("/task", result["output"])
        self.assertEqual((self.env.workspace / "pkg/mod.py").read_text(), "x = 2\n")
        self.assertEqual(self.env.run("python3 -c 'print(6*7)'")["output"].strip(), "42")

    def test_writes_outside_the_workspace_are_denied(self):
        outside = Path(self.tmp.name) / "outside.txt"
        result = self.env.run("echo hi > %s" % outside)
        self.assertNotEqual(result["returncode"], 0)
        self.assertFalse(outside.exists())

    @unittest.skipUnless(str(Path.home()).startswith("/Users/"), "home directory is not under /Users")
    def test_reads_under_users_are_denied(self):
        # The home directory, not this checkout, which may live outside /Users.
        result = self.env.run("ls %s" % Path.home())
        self.assertNotEqual(result["returncode"], 0)

    def test_network_is_denied(self):
        code = "import socket; socket.create_connection(('1.1.1.1', 80), timeout=3)"
        result = self.env.run("python3 -c \"%s\"" % code)
        self.assertNotEqual(result["returncode"], 0)
        self.assertRegex(result["output"], "Operation not permitted|PermissionError")

    def test_timeout_is_enforced(self):
        self.env.timeout = 1
        result = self.env.run("sleep 5")
        self.assertEqual(result["returncode"], -1)
        self.assertIn("timed out", result["exception_info"])


if __name__ == "__main__":
    unittest.main()
