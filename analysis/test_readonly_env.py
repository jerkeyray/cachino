from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/agent-task"))

from readonly_env import SUBMIT, UNAVAILABLE, ReadOnlyFiles, respond  # noqa: E402

FILES = {"pkg/more.py": "def a():\n    return 1\n\ndef split_after():\n    pass\n",
         "tests/test_more.py": "import pkg\nclass SplitAfterTests:\n    pass\n",
         "README.rst": "hello\n"}


class ReadOnlyEnvTests(unittest.TestCase):
    def setUp(self):
        self.fs = ReadOnlyFiles(FILES)

    def run_(self, command):
        return respond(self.fs, command)

    def test_listing_and_reading(self):
        self.assertEqual(self.run_("ls")["output"], "README.rst\npkg\ntests\n")
        self.assertEqual(self.run_("cd /task && ls -la pkg")["output"], "more.py\n")
        self.assertEqual(self.run_("cat pkg/more.py")["output"], FILES["pkg/more.py"])
        self.assertEqual(self.run_("nl -ba pkg/more.py | sed -n '4,5p'")["output"],
                         "     4\tdef split_after():\n     5\t    pass\n")
        self.assertEqual(self.run_("head -n 1 /task/pkg/more.py")["output"], "def a():\n")

    def test_search(self):
        self.assertEqual(self.run_("grep -n split_after pkg/more.py")["output"], "4:def split_after():\n")
        self.assertEqual(self.run_("grep -rn SplitAfter tests")["output"], "tests/test_more.py:2:class SplitAfterTests:\n")
        self.assertEqual(self.run_("find . -name '*.py'")["output"], "./pkg/more.py\n./tests/test_more.py\n")
        self.assertEqual(self.run_("grep -n nothing pkg/more.py")["returncode"], 1)

    def test_edits_and_shell_syntax_are_not_run(self):
        for command in ("sed -i 's/a/b/' pkg/more.py", "python -c 1", "echo x > pkg/more.py",
                        "cat pkg/more.py; ls", "rm -rf /"):
            result = self.run_(command)
            self.assertEqual((result["output"], result["returncode"]), (UNAVAILABLE + "\n", 127), command)
        self.assertEqual(self.fs.files["pkg/more.py"], FILES["pkg/more.py"])

    def test_paths_cannot_leave_task(self):
        self.assertEqual(self.run_("cat ../etc/passwd")["returncode"], 1)
        self.assertEqual(self.run_("cat /etc/passwd")["returncode"], 1)

    def test_submission_marker(self):
        self.assertEqual(self.run_("echo " + SUBMIT)["output"], SUBMIT + "\n")


if __name__ == "__main__":
    unittest.main()
