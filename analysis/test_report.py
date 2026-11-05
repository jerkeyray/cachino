import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from report import TraceError, inspect, response_summary, write_sqlite


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_run(self, response=b'{"choices":[]}', content_type="application/json", finished=True, limit=None, sequence=1, path="/v1/chat/completions"):
        request_id = "%032x" % sequence
        directory = self.root / request_id
        directory.mkdir()
        record = {"schema_version": 1, "request_id": request_id, "sequence": sequence,
                  "session_id": "test", "turn": 0, "attempt": 0,
                  "started_at": "2026-10-27T00:00:00Z", "method": "POST", "path": path,
                  "state": "in_progress", "http_status": 0, "response_content_type": "",
                  "duration_ms": 0, "first_response_write_ms": None,
                  "request": {"path": request_id + "/request.body"},
                  "response": {"path": request_id + "/response.body"}}
        start = json.dumps({"event": "started", "record": record})
        for name, data in (("request", b'{"model":"fake-model","messages":[{}]}'), ("response", response)):
            stored = data if limit is None else data[:limit]
            (directory / (name + ".body")).write_bytes(stored)
            record[name].update({"observed_bytes": len(data), "stored_bytes": len(stored),
                                 "sha256_observed": hashlib.sha256(data).hexdigest(),
                                 "sha256_stored": hashlib.sha256(stored).hexdigest(),
                                 "complete": True, "truncated": len(stored) < len(data), "capture_error": False})
        record.update(state="completed", http_status=200, response_content_type=content_type,
                      duration_ms=10, first_response_write_ms=1)
        text = start + "\n"
        if finished:
            text += json.dumps({"event": "finished", "record": record}) + "\n"
        with (self.root / "events.jsonl").open("a") as file:
            file.write(text)
        return record

    def test_unknown_metrics_stay_null_and_sqlite_is_derived(self):
        self.make_run()
        report = inspect(self.root)
        self.assertEqual(report["metrics"]["fully_verified_captures"], 1)
        self.assertEqual(report["metrics"]["known_cache_metrics_requests"], 0)
        self.assertIsNone(report["metrics"]["known_cached_tokens"])
        self.assertIsNone(report["requests"][0]["cached_tokens"])
        database = self.root / "index.sqlite"
        write_sqlite(database, report)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("SELECT state, cached_tokens FROM requests").fetchone(), ("completed", None))
        with self.assertRaises(FileExistsError):
            write_sqlite(database, report)

    def test_known_zero_is_distinct_from_missing(self):
        self.make_run(b'{"usage":{"prompt_tokens":12,"prompt_tokens_details":{"cached_tokens":0}}}')
        report = inspect(self.root)
        self.assertEqual(report["metrics"]["known_cache_metrics_requests"], 1)
        self.assertEqual(report["metrics"]["known_cached_tokens"], 0)
        self.assertEqual(report["metrics"]["weighted_cache_ratio"], 0)

    def test_deepseek_json_and_stream_cache_usage(self):
        payload = b'{"usage":{"prompt_tokens":20,"prompt_cache_hit_tokens":16,"prompt_cache_miss_tokens":4}}'
        for content_type, body in (("application/json", payload),
                                   ("text/event-stream", b'data: ' + payload + b'\n\ndata: [DONE]\n\n')):
            with self.subTest(content_type=content_type):
                row = response_summary(body, content_type)
                self.assertEqual(row["prompt_tokens"], 20)
                self.assertEqual(row["cached_tokens"], 16)
                self.assertEqual(row["cache_metric_source"], "prompt_cache_hit_tokens")
                self.assertFalse(row["cache_metric_conflict"])
        self.make_run(payload)
        report = inspect(self.root)
        self.assertEqual(report["metrics"]["weighted_cache_ratio"], 0.8)

    def test_deepseek_unknown_zero_and_conflicting_aliases(self):
        for value in (None, True, -1, "8", 1.5, 0):
            with self.subTest(value=value):
                body = json.dumps({"usage": {"prompt_tokens": 20, "prompt_cache_hit_tokens": value}}).encode()
                row = response_summary(body, "application/json")
                self.assertEqual(row["cached_tokens"], 0 if type(value) is int and value == 0 else None)
        for alias, expected, conflict in ((16, 16, False), (8, None, True), (None, None, True)):
            body = json.dumps({"usage": {"prompt_tokens": 20, "prompt_cache_hit_tokens": alias,
                                          "prompt_tokens_details": {"cached_tokens": 16}}}).encode()
            row = response_summary(body, "application/json")
            self.assertEqual(row["cached_tokens"], expected)
            self.assertEqual(row["cache_metric_conflict"], conflict)

    def test_last_usage_replaces_deepseek_cache_counts(self):
        body = (b'data: {"usage":{"prompt_tokens":20,"prompt_cache_hit_tokens":16}}\n\n'
                b'data: {"usage":{"prompt_tokens":21}}\n\ndata: [DONE]\n\n')
        row = response_summary(body, "text/event-stream")
        self.assertEqual(row["prompt_tokens"], 21)
        self.assertIsNone(row["cached_tokens"])
        self.assertIsNone(row["cache_metric_source"])

    def test_interrupted_capture_and_torn_last_event_remain_visible(self):
        self.make_run(finished=False)
        with (self.root / "events.jsonl").open("ab") as file:
            file.write(b'{"event":"finished"')
        report = inspect(self.root)
        self.assertEqual(report["metrics"]["unfinished"], 1)
        self.assertEqual(report["requests"][0]["response_integrity"], "unfinished")
        self.assertEqual(len(report["warnings"]), 1)

    def test_tampering_fails_inspection(self):
        record = self.make_run()
        (self.root / record["response"]["path"]).write_bytes(b'{"choices":[1]}')
        with self.assertRaisesRegex(TraceError, "mismatch"):
            inspect(self.root)

    def test_truncated_capture_is_not_used_for_metrics(self):
        self.make_run(b'{"usage":{"prompt_tokens":12,"prompt_tokens_details":{"cached_tokens":8}}}', limit=8)
        report = inspect(self.root)
        self.assertEqual(report["requests"][0]["response_integrity"], "truncated")
        self.assertIsNone(report["requests"][0]["cached_tokens"])
        (self.root / report["requests"][0]["request_id"] / "response.body").write_bytes(b"tampered")
        with self.assertRaisesRegex(TraceError, "hash mismatch"):
            inspect(self.root)

    def test_cache_ratio_is_token_weighted_with_session_coverage(self):
        self.make_run(b'{"usage":{"prompt_tokens":100,"prompt_tokens_details":{"cached_tokens":0}}}')
        self.make_run(b'{"usage":{"prompt_tokens":10,"prompt_tokens_details":{"cached_tokens":10}}}', sequence=2)
        self.make_run(sequence=3)
        report = inspect(self.root)
        self.assertEqual(report["metrics"]["known_cache_metrics_requests"], 2)
        self.assertAlmostEqual(report["metrics"]["weighted_cache_ratio"], 10 / 110)
        self.assertEqual(report["sessions"][0]["requests"], 3)
        self.assertEqual(report["sessions"][0]["known_cache_metrics_requests"], 2)
        self.assertEqual(report["sessions"][0]["weighted_cache_ratio"], report["metrics"]["weighted_cache_ratio"])

    def test_event_identity_mismatch_rejected(self):
        self.make_run()
        lines = (self.root / "events.jsonl").read_text().splitlines()
        event = json.loads(lines[1])
        event["record"]["turn"] = 1
        (self.root / "events.jsonl").write_text(lines[0] + "\n" + json.dumps(event) + "\n")
        with self.assertRaisesRegex(TraceError, "identity mismatch"):
            inspect(self.root)

    def test_sse_usage_done_and_in_band_errors(self):
        body = (b': heartbeat\r\n\r\ndata: {"usage":{"prompt_tokens":20,\r\n'
                b'data: "prompt_tokens_details":{"cached_tokens":16}}}\r\n\r\n'
                b'data: {"error":{"message":"failed"}}\r\n\r\ndata: [DONE]\r\n\r\n')
        self.make_run(body, "text/event-stream; charset=utf-8")
        row = inspect(self.root)["requests"][0]
        self.assertTrue(row["sse_done"])
        self.assertTrue(row["response_error"])
        self.assertEqual(row["cached_tokens"], 16)
        self.assertFalse(row["parse_error"])

    def test_malformed_or_unterminated_sse_does_not_look_complete(self):
        result = response_summary(b'data: {broken}\n\n', "text/event-stream")
        self.assertTrue(result["parse_error"])
        result = response_summary(b'data: [DONE]', "text/event-stream")
        self.assertTrue(result["parse_error"])
        self.assertFalse(result["sse_done"])
        result = response_summary(b'data: {}\n\n', "text/event-stream")
        self.assertFalse(result["sse_done"])

    def test_messages_stream_native_usage_and_termination(self):
        body = (b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":12,"output_tokens":0,"cache_read_input_tokens":8}}}\n\n'
                b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":2}}\n\n'
                b'event: message_stop\ndata: {"type":"message_stop"}\n\n')
        self.make_run(body, "text/event-stream", path="/v1/messages")
        row = inspect(self.root)["requests"][0]
        self.assertEqual(row["protocol"], "anthropic")
        self.assertTrue(row["message_stop"])
        self.assertIsNone(row["sse_done"])
        self.assertEqual(row["anthropic_usage"], {"input_tokens": 12, "output_tokens": 2, "cache_read_input_tokens": 8})
        self.assertIsNone(row["prompt_tokens"])
        self.assertIsNone(row["cached_tokens"])
        self.assertEqual(inspect(self.root)["metrics"]["known_cache_metrics_requests"], 0)

    def test_messages_json_and_error_without_stop(self):
        row = response_summary(b'{"type":"message","usage":{"input_tokens":9,"output_tokens":1}}', "application/json", "anthropic")
        self.assertEqual(row["anthropic_usage"]["input_tokens"], 9)
        self.assertIsNone(row["message_stop"])
        row = response_summary(b'event: error\ndata: {"type":"error","error":{"message":"fixture"}}\n\n', "text/event-stream", "anthropic")
        self.assertTrue(row["response_error"])
        self.assertFalse(row["message_stop"])
        row = response_summary(b'data: [DONE]\n\n', "text/event-stream", "anthropic")
        self.assertFalse(row["message_stop"])

    def test_token_count_route_never_enters_cache_ratios(self):
        self.make_run(b'{"input_tokens":9}', path="/v1/messages/count_tokens")
        row = inspect(self.root)["requests"][0]
        self.assertEqual(row["protocol"], "anthropic_count_tokens")
        self.assertIsNone(row["prompt_tokens"])
        self.assertIsNone(row["cached_tokens"])

    def test_encoded_response_requires_explicit_supported_decoder(self):
        row = response_summary(b'opaque', "text/event-stream", "anthropic", "br")
        self.assertTrue(row["parse_error"])
        self.assertIsNotNone(row["response_decode_error"])
        self.assertIsNone(row["anthropic_usage"])

    def test_zstd_decode_is_bounded_and_does_not_change_source(self):
        try:
            from compression.zstd import compress
        except ImportError:
            self.skipTest("optional zstd verification requires Python 3.14")
        body = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        encoded = compress(body)
        row = response_summary(encoded, "text/event-stream", "anthropic", "zstd")
        self.assertTrue(row["message_stop"])
        self.assertFalse(row["parse_error"])
        self.assertEqual(encoded, compress(body))
        for data in (encoded[:-1], encoded + encoded, compress(b'x' * ((16 << 20) + 1))):
            row = response_summary(data, "text/event-stream", "anthropic", "zstd")
            self.assertTrue(row["parse_error"])
            self.assertIsNotNone(row["response_decode_error"])


if __name__ == "__main__":
    unittest.main()
