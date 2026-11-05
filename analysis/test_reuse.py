"""Check history boundaries and provenance with declared integer-token fixtures.

These IDs are not Qwen output. Genuine trace-format hashes exercise the join
without loading a tokenizer or pretending to execute an inference engine.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from report import TraceError
from reuse import explain, write_report


class PromptHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trace = self.root / "capture"
        self.derived = self.root / "reconstructed"
        self.trace.mkdir()
        self.derived.mkdir()
        self.rows = []

    def add(self, ids, session="s", model="fixture-model", supported=True,
            response=None, stream=False, complete=True):
        """Persist real lifecycle/sidecar records and an explicit token fixture."""
        sequence = len(self.rows) + 1
        request_id = "%032x" % sequence
        folder = self.trace / request_id
        folder.mkdir()
        request = json.dumps({"model": model, "messages": [{"role": "user", "content": "fixture %d" % sequence}]}).encode()
        if response is None:
            response = b'{"choices":[]}'
        descriptors = {}
        for name, data in (("request", request), ("response", response)):
            (folder / (name + ".body")).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            descriptors[name] = {"path": request_id + "/" + name + ".body",
                                 "observed_bytes": len(data), "stored_bytes": len(data),
                                 "sha256_observed": digest, "sha256_stored": digest,
                                 "complete": complete if name == "response" else True,
                                 "truncated": False, "capture_error": False}
        record = {"schema_version": 1, "request_id": request_id, "sequence": sequence,
                  "session_id": session, "turn": sequence - 1, "attempt": 0,
                  "started_at": "2026-11-03T00:00:00Z", "method": "POST",
                  "path": "/v1/chat/completions", "state": "in_progress",
                  "http_status": 0, "response_content_type": "", "duration_ms": 0,
                  "first_response_write_ms": None, **descriptors}
        with (self.trace / "events.jsonl").open("a") as file:
            file.write(json.dumps({"event": "started", "record": record}) + "\n")
            record.update(state="completed", http_status=200, duration_ms=10,
                          response_content_type="text/event-stream" if stream else "application/json")
            file.write(json.dumps({"event": "finished", "record": record}) + "\n")
        row = {"request_id": request_id, "sequence": sequence, "session_id": session,
               "turn": sequence - 1, "status": "rendered" if supported else "unsupported"}
        if supported:
            token_hash = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
            row.update(prompt_tokens=len(ids), token_ids_sha256=token_hash,
                       request_sha256=descriptors["request"]["sha256_observed"],
                       source_model=model, model_remapped=False)
            (self.derived / (request_id + ".tokens.json")).write_text(json.dumps(ids))
        else:
            row["reason"] = "unsupported test fixture"
        self.rows.append(row)
        return request_id

    def run_report(self):
        manifest = {"schema_version": 1, "pin": {"model_id": "fixture-model", "revision": "test-only"},
                    "template_sha256": "test-only", "libraries": {"tokenizer": "declared-integer-fixture"},
                    "engine_validated": False, "requests": self.rows}
        (self.derived / "summary.json").write_text(json.dumps(manifest))
        return explain(self.derived, self.trace)

    def test_nonadjacent_prompt_can_match_more_than_the_previous_prompt(self):
        first = self.add([1, 2, 3, 4])
        self.add([1, 9, 9])
        self.add([1, 2, 3, 4])
        report = self.run_report()
        last = report["requests"][-1]
        self.assertEqual(last["adjacent_lcp_tokens"], 1)
        self.assertEqual(last["historical_prompt_lcp_tokens"], 4)
        self.assertEqual(last["historical_request_id"], first)
        self.assertEqual(last["prior_prompt_candidates"], 2)
        self.assertFalse(report["engine_validated"])
        self.assertIsNone(last["actual_engine_cached_tokens"])

    def test_ties_choose_latest_and_zero_match_is_distinct_from_no_candidate(self):
        self.add([1, 2])
        second = self.add([1, 3])
        self.add([1, 4])
        fourth = self.add([8, 9])
        rows = self.run_report()["requests"]
        self.assertIsNone(rows[0]["historical_prompt_lcp_tokens"])
        self.assertEqual(rows[2]["historical_request_id"], second)
        self.assertEqual(rows[3]["historical_prompt_lcp_tokens"], 0)
        self.assertNotEqual(rows[3]["historical_request_id"], fourth)

    def test_sessions_and_source_models_are_not_inferred_to_share_history(self):
        first = self.add([1, 2], session="a")
        self.add([1, 2], session="b")
        self.add([1, 2], session="a", model="other-model")
        self.add([1, 2], session="a")
        self.add([1, 2], session=None)
        self.add([1, 2], session=None)
        rows = self.run_report()["requests"]
        self.assertIsNone(rows[1]["historical_prompt_lcp_tokens"])
        self.assertIsNone(rows[2]["adjacent_lcp_tokens"])
        self.assertIsNone(rows[3]["adjacent_lcp_tokens"])
        self.assertEqual(rows[3]["historical_request_id"], first)
        self.assertTrue(all(row["status"] == "unassigned" for row in rows[4:]))

    def test_unsupported_or_partial_capture_clears_history(self):
        self.add([1, 2])
        self.add([], supported=False)
        self.add([1, 2])
        self.add([1, 2], complete=False)
        self.add([1, 2])
        rows = self.run_report()["requests"]
        self.assertEqual(rows[1]["status"], "excluded")
        self.assertEqual(rows[3]["reason"], "capture is not fully verified")
        self.assertIsNone(rows[2]["historical_prompt_lcp_tokens"])
        self.assertIsNone(rows[4]["historical_prompt_lcp_tokens"])

    def test_stream_without_done_is_a_boundary(self):
        self.add([1, 2])
        self.add([1, 2], stream=True, response=b'data: {"choices":[]}\n\n')
        self.add([1, 2])
        rows = self.run_report()["requests"]
        self.assertIn("[DONE]", rows[1]["reason"])
        self.assertIsNone(rows[2]["historical_prompt_lcp_tokens"])

    def test_upstream_counts_never_become_local_engine_hits(self):
        self.add([1, 2], response=b'{"usage":{"prompt_tokens":100,"prompt_cache_hit_tokens":80}}')
        row = self.run_report()["requests"][0]
        self.assertEqual(row["local_prompt_tokens"], 2)
        self.assertEqual(row["upstream_usage"]["cached_tokens"], 80)
        self.assertIsNone(row["actual_engine_cached_tokens"])
        self.assertIsNone(row["processed_prompt_tokens"])

    def test_returned_output_ids_are_not_assumed_to_be_materialized_history(self):
        self.add([1, 2], response=b'{"choices":[{"token_ids":[3,4]}]}')
        self.add([1, 2, 3, 4])
        row = self.run_report()["requests"][-1]
        self.assertEqual(row["historical_prompt_lcp_tokens"], 2)
        self.assertIsNone(row["actual_engine_cached_tokens"])

    def test_missing_rejection_reason_still_breaks_history(self):
        self.add([1, 2])
        self.add([], supported=False)
        self.rows[-1]["reason"] = None
        self.add([1, 2])
        rows = self.run_report()["requests"]
        self.assertEqual(rows[1]["reason"], "unsupported reconstruction")
        self.assertIsNone(rows[2]["historical_prompt_lcp_tokens"])

    def test_boolean_sequence_is_not_equal_to_integer_identity(self):
        self.add([1, 2])
        self.rows[0]["sequence"] = True
        with self.assertRaisesRegex(TraceError, "identity mismatch"):
            self.run_report()

    def test_token_tampering_and_bool_ids_fail_validation(self):
        request_id = self.add([1, 2])
        path = self.derived / (request_id + ".tokens.json")
        path.write_text("[1, 3]")
        with self.assertRaisesRegex(TraceError, "token hash mismatch"):
            self.run_report()
        path.write_text("[true, 2]")
        with self.assertRaisesRegex(TraceError, "nonnegative integers"):
            self.run_report()

    def test_source_tampering_and_label_drift_fail_validation(self):
        request_id = self.add([1, 2])
        self.rows[0]["session_id"] = "changed"
        with self.assertRaisesRegex(TraceError, "identity mismatch"):
            self.run_report()
        self.rows[0]["session_id"] = "s"
        (self.trace / request_id / "request.body").write_bytes(b"changed")
        with self.assertRaisesRegex(TraceError, "mismatch"):
            self.run_report()

    def test_cannot_omit_or_reorder_manifest_rows(self):
        self.add([1, 2])
        self.add([1, 3])
        self.rows.reverse()
        with self.assertRaisesRegex(TraceError, "coverage/order"):
            self.run_report()
        self.rows.pop()
        with self.assertRaisesRegex(TraceError, "coverage/order"):
            self.run_report()

    def test_symlinked_token_file_is_rejected(self):
        request_id = self.add([1, 2])
        path = self.derived / (request_id + ".tokens.json")
        target = self.root / "elsewhere.json"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(TraceError, "regular derived file"):
            self.run_report()

    def test_private_reports_refuse_overwrite_and_preserve_sources(self):
        self.add([1, 2])
        self.add([1, 2])
        report = self.run_report()
        before = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        out = self.root / "history"
        write_report(out, report)
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        self.assertEqual((out / "summary.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse(json.loads((out / "summary.json").read_text())["engine_validated"])
        self.assertIn("Historical prompt LCP", (out / "report.md").read_text())
        for path, data in before.items():
            self.assertEqual(Path(path).read_bytes(), data)
        with self.assertRaises(FileExistsError):
            write_report(out, report)


if __name__ == "__main__":
    unittest.main()
