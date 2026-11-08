"""The released results/ tables validate, and tampering with them is caught."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate_results  # noqa: E402


class ValidateResultsTests(unittest.TestCase):
    def test_released_tables_pass(self):
        self.assertEqual(validate_results.main(), 0)

    def run_on_copy(self, edit):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "results"
            shutil.copytree(validate_results.RESULTS, copy)
            edit(copy)
            with mock.patch.object(validate_results, "RESULTS", copy):
                checks = validate_results.Checks()
                validate_results.integrity(checks)
                validate_results.claims(checks)
                return checks.failures

    def test_a_changed_number_fails_its_checksum_and_its_claim(self):
        def edit(root):
            path = root / "cache-sweep/requests.csv"
            lines = path.read_text().splitlines()
            fields = lines[1].split(",")
            fields[5] = str(int(fields[5]) + 32)  # one more cached block on the first request
            lines[1] = ",".join(fields)
            path.write_text("\n".join(lines) + "\n")
        failures = self.run_on_copy(edit)
        self.assertTrue(any(f.startswith("sha256 cache-sweep/requests.csv") for f in failures), failures)
        self.assertTrue(any("exact" in f for f in failures), failures)

    def test_a_hit_that_is_not_whole_blocks_is_rejected(self):
        def edit(root):
            path = root / "cache-sweep/requests.csv"
            lines = path.read_text().splitlines()
            fields = lines[2].split(",")
            fields[6] = str(int(fields[6]) + 1)
            lines[2] = ",".join(fields)
            path.write_text("\n".join(lines) + "\n")
        with self.assertRaisesRegex(AssertionError, "whole-block"):
            self.run_on_copy(edit)


if __name__ == "__main__":
    unittest.main()
