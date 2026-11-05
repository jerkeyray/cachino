"""Check direct transport boundaries without importing the external harness."""

import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

PATH = Path(__file__).resolve().parents[1] / "experiments/agent-task/direct_model.py"
SPEC = importlib.util.spec_from_file_location("direct_model_fixture", PATH)
direct = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(direct)


class DirectModelTests(unittest.TestCase):
    def model(self, **kwargs):
        return direct.DirectTextModel(**({"model_name": "wire-fixture", "api_base": "http://127.0.0.1:18082/v1",
                                          "api_key": "fixture-only-key"} | kwargs))

    def reply(self):
        return {"model": "wire-fixture", "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "```mswea_bash_command\naction\n```", "refusal": None}}]}

    def test_explicit_payload_unicode_history_and_no_retry_on_http_error(self):
        model = self.model()
        messages = [{"role": "system", "content": "héllo 世界"},
                    {"role": "assistant", "content": "prior", "extra": {"response": {"private": True}}},
                    {"role": "user", "content": "observation", "extra": {"timestamp": 123}}]
        before = copy.deepcopy(messages)
        stream = io.BytesIO(b"error")
        error = HTTPError(model.config["api_base"], 429, "limited", {}, stream)
        model._opener = Mock()
        model._opener.open.side_effect = error
        with self.assertRaises(HTTPError):
            model.query(messages)
        model._opener.open.assert_called_once()
        request = model._opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload, {"model": "wire-fixture", "messages": direct.text_messages(messages),
                                   "max_tokens": 64, "n": 1, "stream": False, "temperature": 0})
        self.assertIn("世界".encode(), request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture-only-key")
        self.assertEqual(request.full_url, "http://127.0.0.1:18082/v1/chat/completions")
        self.assertEqual(messages, before)
        self.assertTrue(stream.closed)

    def test_unknown_message_fields_and_nontext_fail_before_dispatch(self):
        model = self.model()
        model._opener = Mock()
        for messages in ([], [{"role": "user", "content": None}],
                         [{"role": "user", "content": "hello", "provider_specific_fields": {"refusal": None}}],
                         [{"role": "tool", "content": "output"}],
                         [{"role": "assistant", "content": "prefill"}]):
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                model.query(messages)
        model._opener.open.assert_not_called()

    def test_text_response_preserves_evidence_and_rejects_unsupported_shapes(self):
        response = self.reply()
        before = copy.deepcopy(response)
        self.assertEqual(direct.assistant_text(response, "wire-fixture"), response["choices"][0]["message"]["content"])
        self.assertEqual(response, before)
        for change in ("model", "choice_count", "truncated", "null", "refusal", "tool_calls", "reasoning", "role"):
            bad = self.reply()
            choice = bad["choices"][0]
            if change == "model":
                bad["model"] = "other"
            elif change == "choice_count":
                bad["choices"] *= 2
            elif change == "truncated":
                choice["finish_reason"] = "length"
            elif change == "null":
                choice["message"]["content"] = None
            elif change == "refusal":
                choice["message"]["refusal"] = "refused"
            elif change == "tool_calls":
                choice["message"]["tool_calls"] = [{"function": {"name": "unexpected"}}]
            elif change == "reasoning":
                choice["message"]["reasoning_content"] = "unmodeled reasoning"
            else:
                choice["message"]["role"] = "user"
            with self.subTest(change=change), self.assertRaises(ValueError):
                direct.assistant_text(bad, "wire-fixture")

    def test_response_byte_cap_and_invalid_json_fail_without_retry(self):
        for data in (b"x" * (direct.RESPONSE_LIMIT + 1), b"not json"):
            model = self.model()
            model._opener = Mock()
            model._opener.open.return_value = io.BytesIO(data)
            with self.assertRaises(ValueError):
                model.query([{"role": "user", "content": "hello"}])
            model._opener.open.assert_called_once()

    def test_endpoint_and_numeric_controls_fail_explicitly(self):
        for base in ("https://example.com/v1", "http://localhost:18082/v1", "http://127.0.0.1/v1",
                     "http://key@127.0.0.1:18082/v1", "http://127.0.0.1:18082/v1?x=1",
                     "http://127.0.0.1:18082/v1#fragment"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                self.model(api_base=base)
        for kwargs in ({"max_tokens": True}, {"max_tokens": 0}, {"temperature": float("nan")},
                       {"timeout": 0}, {"timeout": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.model(**kwargs)

    def test_key_excluded_from_templates_and_trajectory_and_redirects_disabled(self):
        model = self.model()
        self.assertNotIn("fixture-only-key", json.dumps(model.get_template_vars()))
        self.assertNotIn("fixture-only-key", json.dumps(model.serialize()))
        self.assertTrue(any(isinstance(handler, direct.NoRedirect) for handler in model._opener.handlers))
        self.assertIsNone(direct.NoRedirect().redirect_request(None, None, 302, "found", {}, "http://elsewhere"))


if __name__ == "__main__":
    unittest.main()
