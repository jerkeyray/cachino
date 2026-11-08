"""The committed examples still print their expected output."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OneBlockExampleTests(unittest.TestCase):
    def test_output_matches_expected(self):
        example = ROOT / "examples/one-block"
        out = subprocess.run([sys.executable, "-I", str(example / "run.py")], capture_output=True, text=True,
                             check=True).stdout
        self.assertEqual(out, (example / "expected.txt").read_text())


if __name__ == "__main__":
    unittest.main()
