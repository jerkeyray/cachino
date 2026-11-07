"""Offline dispatch checks: no sockets, provider credentials or inference."""

import io
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from hosted_smoke import NoRedirect, endpoint, send


class Response(io.BytesIO):
    def __init__(self, body, content_type):
        super().__init__(body)
        self.status = 200
        self.headers = {"Content-Type": content_type}


class HostedSmokeTests(unittest.TestCase):
    def test_only_local_origin_accepted(self):
        self.assertEqual(endpoint("http://127.0.0.1:8080/"), "http://127.0.0.1:8080/v1/chat/completions")
        for value in ("https://api.deepseek.com", "http://127.0.0.1", "http://127.0.0.1:0/path",
                      "http://secret@127.0.0.1:8080", "http://127.0.0.1:8080?key=secret",
                      "http://127.0.0.1:99999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                endpoint(value)

    def test_two_requests_preserve_limits_labels_and_usage(self):
        usage = b'{"usage":{"prompt_tokens":10,"prompt_cache_hit_tokens":0}}'
        opener = Mock()
        opener.open.side_effect = [Response(usage, "application/json"),
                                   Response(b'data: ' + usage + b'\n\ndata: [DONE]\n\n', "text/event-stream")]
        rows = send(endpoint("http://127.0.0.1:8080"), "fixture-key", opener)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["cached_tokens"], 0)
        for turn, call in enumerate(opener.open.call_args_list):
            req = call.args[0]
            payload = json.loads(req.data)
            self.assertEqual(payload["max_tokens"], 16)
            self.assertEqual(payload["thinking"], {"type": "disabled"})
            self.assertEqual(payload["stream"], bool(turn))
            self.assertEqual(req.get_header("X-cachino-turn"), str(turn))
            self.assertEqual(call.kwargs["timeout"], 30)

    def test_http_failure_stops_without_retry(self):
        opener = Mock()
        opener.open.side_effect = HTTPError("http://fixture", 429, "limited", {}, None)
        with self.assertRaises(HTTPError):
            send("http://fixture", "fixture-key", opener)
        self.assertEqual(opener.open.call_count, 1)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 307, "", {}, "http://other"))

    def test_large_or_error_response_stops_before_second_request(self):
        for body in (b'x' * ((1 << 20) + 1), b'{"error":{"message":"fixture"}}'):
            opener = Mock()
            opener.open.return_value = Response(body, "application/json")
            with self.assertRaises(ValueError):
                send("http://fixture", "fixture-key", opener)
            self.assertEqual(opener.open.call_count, 1)

    def test_stream_without_done_is_rejected(self):
        opener = Mock()
        opener.open.side_effect = [Response(b'{}', "application/json"),
                                   Response(b'data: {}\n\n', "text/event-stream")]
        with self.assertRaises(ValueError):
            send("http://fixture", "fixture-key", opener)


if __name__ == "__main__":
    unittest.main()
