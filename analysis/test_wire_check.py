"""Verify local wire-check boundaries without importing or installing the SDK."""

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

RUNNER = Path(__file__).resolve().parents[1] / "experiments/agent-task/wire_check.py"
SPEC = importlib.util.spec_from_file_location("wire_check_fixture", RUNNER)
wire = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wire)


class WireCheckTests(unittest.TestCase):
    def exchange(self):
        first = [{"role": "system", "content": wire.SYSTEM}, {"role": "user", "content": wire.TASK}]
        second = copy.deepcopy(first) + [
            {"role": "assistant", "content": wire.ACTION},
            {"role": "user", "content": "<returncode>0</returncode>\n<output>\n%s</output>" % wire.OBSERVATION},
        ]
        requests = [{"model": "wire-fixture", "messages": messages} for messages in (first, second)]
        result = {"status": "completed", "exit_status": "LimitsExceeded", "agent_calls": 2,
                  "actions": [{"command": "wire_fixture_action"}] * 2}
        return requests, result

    def test_stable_history_preserves_optional_sdk_fields_for_separate_review(self):
        requests, result = self.exchange()
        requests[1]["messages"][2]["tool_calls"] = None
        observed = wire.check_messages(requests, result)
        self.assertTrue(observed["history_appended"])
        self.assertIn("tool_calls", observed["message_field_names"][1][2])
        self.assertIsNone(requests[1]["messages"][2]["tool_calls"])

    def test_mutated_history_metadata_missing_observation_and_wrong_stop_fail(self):
        cases = ("initial", "metadata", "observation", "stop", "actions", "calls", "model")
        for case in cases:
            with self.subTest(case=case):
                requests, result = self.exchange()
                if case == "initial":
                    requests[1]["messages"][0]["content"] += " changed"
                elif case == "metadata":
                    requests[1]["messages"][2]["extra"] = {"timestamp": 123}
                elif case == "observation":
                    requests[1]["messages"].pop()
                elif case == "stop":
                    result["exit_status"] = "FormatError"
                elif case == "actions":
                    result["actions"] = []
                elif case == "calls":
                    result["agent_calls"] = 3
                else:
                    requests[1]["model"] = "other-model"
                with self.assertRaises(ValueError):
                    wire.check_messages(requests, result)

    def test_environment_records_even_shell_looking_text_without_execution(self):
        env = wire.RecordingEnvironment()
        action = {"command": "touch /tmp/should-never-run; exit 1"}
        with patch.object(wire.subprocess, "Popen", side_effect=AssertionError("shell executed")):
            output = env.execute(action)
        self.assertEqual(env.actions, [action])
        self.assertEqual(output["output"], wire.OBSERVATION)
        self.assertFalse(env.serialize()["wire_environment"]["commands_executed"])

    def test_clean_environment_excludes_host_keys_proxy_and_global_config(self):
        with patch.dict(wire.os.environ, {"DEEPSEEK_API_KEY": "test-secret", "HTTPS_PROXY": "http://remote"}):
            env = wire.clean_environment(Path("/private/wire"), Path("/venv/bin/python"))
        self.assertNotIn("DEEPSEEK_API_KEY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")
        self.assertEqual(env["MSWEA_GLOBAL_CONFIG_DIR"], "/private/wire/mini-config")
        self.assertEqual(env["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"], "1")
        self.assertNotIn("LITELLM_LOCAL_MODEL_COST_MAP", env)

    def test_network_guard_allows_only_owned_proxy_including_dns(self):
        denied = []
        guard = wire.network_guard(18082, denied)
        guard("socket.connect", (None, ("127.0.0.1", 18082)))
        guard("socket.getaddrinfo", ("127.0.0.1", 18082, 0, 0, 0))
        for event, args in [
            ("socket.connect", (None, ("127.0.0.1", 443))),
            ("socket.connect", (None, ("93.184.216.34", 443))),
            ("socket.connect", (None, "/tmp/other.sock")),
            ("socket.getaddrinfo", ("example.com", 443, 0, 0, 0)),
        ]:
            with self.assertRaises(PermissionError):
                guard(event, args)
        self.assertEqual(len(denied), 4)

    def test_checkout_requires_exact_pin_clean_tree_and_blob_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            source = checkout / "source.py"
            source.write_bytes(b"pinned source\n")
            data = source.read_bytes()
            blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            manifest = {"harness": {"revision": "frozen", "source_files": [{"path": "source.py", "git_blob_sha": blob}]}}
            with patch.object(wire.subprocess, "check_output", side_effect=["frozen\n", ""]):
                self.assertEqual(wire.verify_checkout(checkout, manifest)["source.py"], hashlib.sha256(data).hexdigest())
            for outputs in (["wrong\n"], ["frozen\n", "?? extra.py\n"]):
                with patch.object(wire.subprocess, "check_output", side_effect=outputs):
                    with self.assertRaises(ValueError):
                        wire.verify_checkout(checkout, manifest)
            source.write_bytes(b"edited source\n")
            with patch.object(wire.subprocess, "check_output", side_effect=["frozen\n", ""]):
                with self.assertRaisesRegex(ValueError, "blob mismatch"):
                    wire.verify_checkout(checkout, manifest)

    def test_dry_run_never_imports_harness_or_launches_children(self):
        with patch.object(wire.sys, "argv", [str(RUNNER)]), patch("sys.stdout", new_callable=io.StringIO) as output:
            with patch.object(wire.subprocess, "Popen", side_effect=AssertionError("child started")):
                wire.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(result["sdk_validated"])
        self.assertFalse(result["external_setup_performed"])

    def test_evidence_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            wire.write_json(path, {"first": True})
            with self.assertRaises(FileExistsError):
                wire.write_json(path, {"first": False})
            self.assertTrue(json.loads(path.read_text())["first"])

    def test_http_fixture_returns_two_actions_no_usage_and_rejects_third(self):
        # Drive the actual HTTP handler with an in-memory socket. This tests
        # framing and admission without binding a port or importing an SDK.
        class MemorySocket:
            def __init__(self, body):
                self.incoming = io.BytesIO(
                    b"POST /v1/chat/completions HTTP/1.0\r\n"
                    b"Authorization: Bearer wire-fixture-placeholder\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                self.outgoing = bytearray()

            def makefile(self, *_):
                return self.incoming

            def sendall(self, data):
                self.outgoing.extend(data)

            def settimeout(self, _):
                pass

        exchanges = []
        handler = wire.fixture_handler(exchanges)
        body = b'{"model":"wire-fixture","messages":[{"role":"user","content":"test"}]}'
        for _ in range(2):
            connection = MemorySocket(body)
            handler(connection, ("127.0.0.1", 1234), object())
            headers, response = bytes(connection.outgoing).split(b"\r\n\r\n", 1)
            self.assertTrue(headers.startswith(b"HTTP/1.0 200"))
            reply = json.loads(response)
            self.assertEqual(reply["choices"][0]["message"]["content"], wire.ACTION)
            self.assertNotIn("usage", reply)
            self.assertEqual(exchanges[-1]["request"], body)
            self.assertTrue(exchanges[-1]["placeholder_auth"])
        connection = MemorySocket(body)
        handler(connection, ("127.0.0.1", 1234), object())
        self.assertTrue(connection.outgoing.startswith(b"HTTP/1.0 400"))
        self.assertEqual(len(exchanges), 2)

    def test_dependency_inventory_requires_exact_lock_and_unique_names(self):
        inventory = [{"name": "python_dotenv", "version": "1.2.4"}, {"name": "openai", "version": "2.54.0"}]
        lock = "# resolved packages\npython-dotenv==1.2.4 \\\n    --hash=sha256:fixture\nopenai==2.54.0\n"
        self.assertEqual(wire.check_dependencies(inventory, lock), 2)
        for changed_inventory, changed_lock in (
            (inventory[:1], lock),
            (inventory + [{"name": "extra", "version": "1"}], lock),
            (inventory, lock.replace("2.54.0", "2.53.0")),
            (inventory + inventory[:1], lock),
            (inventory, lock + "python_dotenv==1.2.4\n"),
            ([], "# no packages\n"),
        ):
            with self.assertRaises(ValueError):
                wire.check_dependencies(changed_inventory, changed_lock)


if __name__ == "__main__":
    unittest.main()
