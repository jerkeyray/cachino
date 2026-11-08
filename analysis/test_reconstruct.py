import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reconstruct import UnsupportedRequest, lcp, prompt_inputs, template_controls
from tokenizer_assets import FILES, fetch, load_pin, verify_assets


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.pin = load_pin()
        self.request = {"model": self.pin["model_id"], "messages": [{"role": "user", "content": "héllo 世界"}]}

    def test_exact_token_prefix_not_equal_token_count(self):
        self.assertEqual(lcp([12, 34, 56], [12, 34, 78]), 2)
        self.assertEqual(lcp([12, 34], [12, 34]), 2)
        self.assertEqual(lcp([12, 34], [12, 34, 56]), 2)
        self.assertEqual(lcp([12], [34]), 0)
        self.assertEqual(lcp([], [34]), 0)

    def test_generation_fields_do_not_rewrite_prompt_inputs(self):
        self.request.update(temperature=0, stream=True, max_tokens=1)
        original = copy.deepcopy(self.request)
        messages, tools = prompt_inputs(self.request, self.pin["model_id"])
        self.assertEqual(messages, original["messages"])
        self.assertIsNone(tools)
        self.assertEqual(self.request, original)

    def test_unknown_rendering_controls_are_rejected(self):
        for key in ("response_format", "tool_choice", "cache_salt", "chat_template", "echo", "continue_final_message", "unknown_field"):
            request = dict(self.request, **{key: {}})
            with self.assertRaises(UnsupportedRequest):
                prompt_inputs(request, self.pin["model_id"])

    def test_tool_choice_accepted_only_when_render_neutral(self):
        tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
        for choice in ("auto", "required"):
            prompt_inputs(dict(self.request, tools=tools, tool_choice=choice), self.pin["model_id"])
        for request in (dict(self.request, tools=tools, tool_choice="none"),
                        dict(self.request, tool_choice="required")):
            with self.assertRaisesRegex(UnsupportedRequest, "tool_choice"):
                prompt_inputs(request, self.pin["model_id"])

    def test_return_token_ids_is_observational(self):
        request = dict(self.request, return_token_ids=True)
        messages, _ = prompt_inputs(request, self.pin["model_id"])
        self.assertEqual(messages, self.request["messages"])
        for value in ("true", 1, None):
            with self.assertRaisesRegex(UnsupportedRequest, "boolean"):
                prompt_inputs(dict(self.request, return_token_ids=value), self.pin["model_id"])

    def test_template_controls_must_match_pin(self):
        # A request that states the pinned settings is explicit; one that omits
        # them depends on server launch defaults, which the flags must expose.
        self.assertEqual(template_controls(self.request, self.pin),
                         {"enable_thinking_explicit": False, "add_generation_prompt_explicit": False})
        request = dict(self.request, chat_template_kwargs={"enable_thinking": self.pin["enable_thinking"]},
                       add_generation_prompt=self.pin["add_generation_prompt"])
        prompt_inputs(request, self.pin["model_id"])
        self.assertEqual(template_controls(request, self.pin),
                         {"enable_thinking_explicit": True, "add_generation_prompt_explicit": True})
        for bad in ({"chat_template_kwargs": {"enable_thinking": not self.pin["enable_thinking"]}},
                    {"chat_template_kwargs": {"enable_thinking": 0}},
                    {"chat_template_kwargs": {"enable_thinking": False, "add_generation_prompt": True}},
                    {"chat_template_kwargs": []},
                    {"add_generation_prompt": not self.pin["add_generation_prompt"]},
                    {"add_generation_prompt": 1}):
            with self.assertRaises(UnsupportedRequest):
                template_controls(dict(self.request, **bad), self.pin)

    def test_unsupported_messages_are_rejected(self):
        for message in ({"role": "user", "content": [{"type": "image_url"}]},
                        {"role": "assistant", "content": None},
                        {"role": ["user"], "content": "bad role"},
                        {"role": "developer", "content": "unsupported role"},
                        {"role": "user", "content": "hello", "name": "unknown normalization"}):
            with self.assertRaises(UnsupportedRequest):
                prompt_inputs(dict(self.request, messages=[message]), self.pin["model_id"])

    def test_model_remapping_requires_explicit_option(self):
        request = dict(self.request, model="fake-model")
        with self.assertRaisesRegex(UnsupportedRequest, "remapping"):
            prompt_inputs(request, self.pin["model_id"])
        prompt_inputs(request, self.pin["model_id"], remap=True)
        with self.assertRaises(UnsupportedRequest):
            prompt_inputs(dict(request, model=None), self.pin["model_id"], remap=True)

    def test_tool_history_is_preserved_without_argument_reserialization(self):
        request = dict(self.request)
        arguments = '{ "path" : "README.md" }'
        request["tools"] = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
        request["messages"] = self.request["messages"] + [
            {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "id": "call-1", "function": {"name": "read", "arguments": arguments}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "file contents"}]
        original = copy.deepcopy(request)
        messages, tools = prompt_inputs(request, self.pin["model_id"])
        self.assertEqual(messages[1]["tool_calls"][0]["function"]["arguments"], arguments)
        self.assertEqual(tools, original["tools"])
        self.assertEqual(request, original)
        request["messages"][1]["tool_calls"][0]["function"]["arguments"] = 'not JSON'
        with self.assertRaises(UnsupportedRequest):
            prompt_inputs(request, self.pin["model_id"])


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "assets"
        self.pin = load_pin()

    def test_download_allowlist_and_integrity(self):
        # Tiny unit-test assets check storage/provenance only. They are not a
        # substitute tokenizer and are never used to claim real token results.
        calls = []
        def download(url, timeout):
            calls.append(url)
            return io.BytesIO(b'fixture asset')
        with patch('tokenizer_assets.urlopen', side_effect=download):
            fetch(self.out, self.pin)
        manifest = verify_assets(self.out, self.pin)
        self.assertEqual(set(manifest['files']), set(FILES))
        self.assertEqual(len(calls), 5)
        self.assertTrue(all('/' + self.pin['revision'] + '/' in url and 'safetensors' not in url for url in calls))
        (self.out / 'tokenizer.json').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            verify_assets(self.out, self.pin)

    def test_failure_cleanup_and_no_overwrite(self):
        with patch('tokenizer_assets.urlopen', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                fetch(self.out, self.pin)
        self.assertFalse(self.out.exists())
        self.out.mkdir()
        marker = self.out / 'existing'
        marker.write_text('preserve')
        with self.assertRaises(FileExistsError):
            fetch(self.out, self.pin)
        self.assertEqual(marker.read_text(), 'preserve')

    def test_pin_mismatch_is_rejected(self):
        with patch('tokenizer_assets.urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'fixture')):
            fetch(self.out, self.pin)
        wrong_pin = dict(self.pin, revision='f' * 40)
        with self.assertRaisesRegex(ValueError, 'pin'):
            verify_assets(self.out, wrong_pin)

    def test_untracked_template_and_token_overrides_are_rejected(self):
        with patch('tokenizer_assets.urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'fixture')):
            fetch(self.out, self.pin)
        for name in ('chat_template.jinja', 'added_tokens.json', 'special_tokens_map.json'):
            with self.subTest(name=name):
                path = self.out / name
                path.write_text('unrecorded override')
                with self.assertRaisesRegex(ValueError, 'untracked'):
                    verify_assets(self.out, self.pin)
                path.unlink()

    def test_symlinked_snapshot_file_is_rejected(self):
        with patch('tokenizer_assets.urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'fixture')):
            fetch(self.out, self.pin)
        path = self.out / 'config.json'
        path.unlink()
        path.symlink_to(self.out / 'tokenizer.json')
        with self.assertRaisesRegex(ValueError, 'nonregular'):
            verify_assets(self.out, self.pin)
