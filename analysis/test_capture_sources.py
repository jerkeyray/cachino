"""Check capture provenance snapshots without a key, build or API request."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


class CaptureSourceTests(unittest.TestCase):
    def test_allowlisted_snapshots_match_and_exclude_credentials(self):
        runner = Path(__file__).resolve().parents[1] / "experiments/claude-capture/run.py"
        spec = importlib.util.spec_from_file_location("capture_runner_fixture", runner)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hashes = module.snapshot_sources(root)
            self.assertIn("experiments/claude-capture/run.py", hashes)
            self.assertIn("analysis/report.py", hashes)
            self.assertIn("cmd/suez/main.go", hashes)
            self.assertFalse(any(".env" in name or name.startswith("traces/") for name in hashes))
            for name, digest in hashes.items():
                self.assertEqual(hashlib.sha256((root / "source" / name).read_bytes()).hexdigest(), digest)
                self.assertEqual((root / "source" / name).read_bytes(), (module.ROOT / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
