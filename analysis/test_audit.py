"""Verify request evidence comparisons without provider calls or token claims."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from audit import audit, differences, markdown, request_shape
from report import TraceError


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def add(self, number, request, session="s", path="/v1/messages"):
        """Persist valid body/lifecycle evidence for an ordered fixture request."""
        request_id = "%032x" % number
        folder = self.root / request_id
        folder.mkdir()
        record = {"schema_version": 1, "request_id": request_id, "sequence": number,
                  "session_id": session, "method": "POST", "path": path,
                  "state": "in_progress", "http_status": 0, "response_content_type": "application/json",
                  "duration_ms": 1, "first_response_write_ms": None}
        for name, data in (("request", json.dumps(request).encode()), ("response", b'{}')):
            (folder / (name + ".body")).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            record[name] = {"path": request_id + "/" + name + ".body", "observed_bytes": len(data),
                            "stored_bytes": len(data), "sha256_observed": digest, "sha256_stored": digest,
                            "complete": True, "truncated": False, "capture_error": False}
        with (self.root / "events.jsonl").open("a") as file:
            file.write(json.dumps({"event": "started", "record": record}) + "\n")
            record.update(state="completed", http_status=200)
            file.write(json.dumps({"event": "finished", "record": record}) + "\n")
        return request_id

    def test_order_change_is_separate_from_value_change(self):
        self.add(1, {"messages": [], "tools": [{"name": "Read", "description": "fixture"}]})
        self.add(2, {"messages": [], "tools": [{"description": "fixture", "name": "Read"}]})
        row = audit(self.root)["requests"][1]
        tools = next(c for c in row["comparison"]["components"] if c["field"] == "tools")
        self.assertTrue(tools["parsed_values_equal"])
        self.assertFalse(tools["ordered_values_equal"])
        self.assertEqual(row["comparison"]["changes"][0]["path"], "/tools/0")

    def test_history_links_and_thinking_text_is_not_copied(self):
        request = {"messages": [
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "private-reasoning-fixture"},
                                               {"type": "tool_use", "id": "call1", "name": "Read", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call1", "content": "private-file-fixture"}]}]}
        shape = request_shape(request)
        self.assertTrue(shape["history_contains_thinking"])
        self.assertFalse(shape["thinking_parameter_present"])
        self.assertEqual(shape["tool_links"][0]["matching_history_call"]["name"], "Read")
        self.assertNotIn("private-reasoning-fixture", json.dumps(shape))
        self.assertNotIn("private-file-fixture", json.dumps(shape))

    def test_unknown_session_unsupported_row_and_protocol_break_comparisons(self):
        self.add(1, {"messages": []})
        self.add(2, {"input": "unsupported"})
        self.add(3, {"messages": []})
        self.add(4, {"messages": []}, path="/v1/chat/completions")
        self.add(5, {"messages": []}, session=None)
        self.add(6, {"messages": []}, session=None)
        rows = audit(self.root)["requests"]
        self.assertEqual(rows[1]["status"], "unsupported")
        self.assertTrue(all("comparison" not in row for row in rows))

    def test_diff_pointers_limit_and_type_changes(self):
        result = differences({"a/b": 1, "flag": True}, {"a/b": 2, "flag": 1}, limit=1)
        self.assertEqual(result["changes"][0]["path"], "/a~1b")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(differences(True, 1)["changes"]), 1)

    def test_audit_preserves_source_and_fails_on_tampering(self):
        request_id = self.add(1, {"messages": []})
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = audit(self.root)
        self.assertIn("not model prompt order", markdown(result))
        after = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        (self.root / request_id / "request.body").write_bytes(b"changed")
        with self.assertRaises(TraceError):
            audit(self.root)


if __name__ == "__main__":
    unittest.main()
