import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

CALIBRATION = Path(__file__).resolve().parents[1] / "experiments/cache-ground-truth"
sys.path.insert(0, str(CALIBRATION))

from build_probes import body_bytes, expand  # noqa: E402
from calibrate import InputError, check_ready, load_inputs, origin, run  # noqa: E402
from test_cache_calibration import filled_manifest  # noqa: E402

CUE = [9, 9, 9, 9]
BLOCK = 4


def fake_ids(request):
    """Stand-in tokenization: character codes per message plus a generation cue.

    Real IDs come from the pinned tokenizer; these only need the same prefix
    structure, so that a later turn replaces the cue with the assistant reply.
    """
    ids = []
    for message in request["messages"]:
        ids += [1] + [ord(c) for c in message["role"] + ":" + message["content"]] + [2]
    return ids + CUE


class Engine:
    """Test double for the engine side of the calibration contract.

    It returns the IDs it was configured with and reports cache hits from a
    block-aligned prefix store that excludes the final prompt token. It exists
    only to drive the client; its numbers say nothing about vLLM.
    """

    def __init__(self, ids_by_body):
        self.ids_by_body = ids_by_body
        self.store = set()
        self.reset_ok = True
        self.ignore_reset = False
        self.details = True
        self.corrupt = None
        self.requests = []

    def complete(self, body, headers):
        ids = list(self.ids_by_body[body])
        cached, stored = 0, 0
        for end in range(BLOCK, len(ids), BLOCK):
            if tuple(ids[:end]) in self.store:
                cached = end
        for end in range(BLOCK, len(ids) + 1, BLOCK):
            stored += tuple(ids[:end]) not in self.store
            self.store.add(tuple(ids[:end]))
        if self.corrupt is not None and len(self.requests) == self.corrupt:
            ids[-1] += 1
        self.requests.append(headers.get("X-Request-Id"))
        usage = {"prompt_tokens": len(ids), "completion_tokens": 1}
        if self.details:
            usage["prompt_tokens_details"] = {"cached_tokens": cached, "created_cache_tokens": stored * BLOCK}
        return {"id": "chatcmpl-" + headers.get("X-Request-Id"), "prompt_token_ids": ids,
                "choices": [{"index": 0, "finish_reason": "length", "token_ids": [7],
                             "message": {"role": "assistant", "content": "x"}}],
                "usage": usage, "metrics": {"time_to_first_token_ms": 1.0, "queue_time_ms": 0.1, "generation_time_ms": 0.0}}


def serve(engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply({"data": [{"id": "Qwen/Qwen3-0.6B"}]})

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path == "/reset_prefix_cache":
                if engine.reset_ok and not engine.ignore_reset:
                    engine.store.clear()
                self.reply({"success": engine.reset_ok})
            else:
                self.reply(engine.complete(body, self.headers))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server


class CalibrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        spec_bytes = (CALIBRATION / "probes.json").read_bytes()
        spec = json.loads(spec_bytes)
        probes, rows, ids_by_body = self.root / "probes", {}, {}
        probes.mkdir()
        for name, request in expand(spec).items():
            data, ids = body_bytes(request), fake_ids(request)
            (probes / (name + ".request.json")).write_bytes(data)
            (probes / (name + ".tokens.json")).write_text(json.dumps(ids))
            rows[name] = {"request_sha256": hashlib.sha256(data).hexdigest(),
                          "token_ids_sha256": hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()}
            ids_by_body[data] = ids
        self.pin = {"model_id": spec["common_request"]["model"], "revision": "c" * 40}
        (probes / "summary.json").write_text(json.dumps({"spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
                                                         "pin": self.pin, "probes": rows}))
        self.inputs = load_inputs(CALIBRATION / "probes.json", probes)
        self.engine = Engine(ids_by_body)
        # Two listeners share one engine, standing in for direct and proxied routes.
        self.servers = [serve(self.engine), serve(self.engine)]
        self.direct, self.suez = (f"http://127.0.0.1:{s.server_address[1]}" for s in self.servers)
        self.manifest = filled_manifest()
        self.manifest["run"]["run_id"] = "t1"
        self.manifest["cache"]["block_size_tokens"] = BLOCK

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def execute(self, out="out"):
        result = run(*self.inputs, self.manifest, self.direct, self.suez, self.root / out, timeout=5)
        steps = [json.loads(line) for line in (self.root / out / "steps.jsonl").read_text().splitlines()]
        return result, steps

    def test_full_sequence_passes_against_consistent_engine(self):
        result, steps = self.execute()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(len(steps), len(self.inputs[0]["sequence"]))
        warm = next(s for s in steps if s["step"] == "warm-base")
        self.assertGreater(warm["cached_tokens"], 0)
        self.assertTrue(all(s.get("response_id_matches", True) for s in steps))
        proxy = next(s for s in steps if s["step"] == "proxy-cold")
        self.assertEqual(proxy["route"], "suez")
        self.assertTrue((self.root / "out/raw/00-preflight.response.body").exists())
        late = next(s for s in steps if s["step"] == "late-after-warm")
        self.assertTrue(all(c["passed"] for c in late["checks"]))
        self.assertIn("block_prediction:base", [c["check"] for c in late["checks"]])
        self.assertIsNotNone(late["created_cache_tokens"])

    def test_wrong_block_size_is_a_recorded_prediction_failure(self):
        # The double caches in blocks of 4; a manifest claiming 8 must make the
        # block predictions miss without stopping the run.
        self.manifest["cache"]["block_size_tokens"] = 2 * BLOCK
        result, steps = self.execute()
        self.assertEqual(result["status"], "completed_with_failures")
        self.assertTrue(all("block_prediction" in f for f in result["failed_checks"]), result["failed_checks"])

    def test_ready_requires_matching_model_and_tokenizer_revision(self):
        spec, spec_sha = self.inputs[0], self.inputs[1]
        self.manifest["run"]["probe_spec_sha256"] = spec_sha
        for key, value in (("model_id", self.pin["model_id"]), ("served_model_name", self.pin["model_id"]),
                           ("local_tokenizer_revision", self.pin["revision"])):
            self.manifest["model"][key] = value
        check_ready(self.manifest, spec, spec_sha, self.root / "probes")
        self.manifest["model"]["local_tokenizer_revision"] = "b" * 40
        with self.assertRaisesRegex(InputError, "tokenizer_revision"):
            check_ready(self.manifest, spec, spec_sha, self.root / "probes")

    def test_parity_mismatch_stops_and_keeps_partial_record(self):
        self.engine.corrupt = 0
        result, steps = self.execute()
        self.assertEqual(result["status"], "stopped")
        self.assertIn("parity mismatch", result["stop"]["reason"])
        self.assertEqual(steps[-1]["step"], "parity-short")
        self.assertEqual(steps[-1]["status"], 200)
        self.assertEqual(len(self.engine.requests), 1)

    def test_missing_cache_details_stop_instead_of_reading_zero(self):
        self.engine.details = False
        result, steps = self.execute()
        self.assertEqual(result["status"], "stopped")
        self.assertIn("prompt_tokens_details absent", result["stop"]["reason"])
        self.assertIsNone(steps[-1]["cached_tokens"])

    def test_failed_reset_stops_before_any_probe(self):
        self.engine.reset_ok = False
        result, steps = self.execute()
        self.assertEqual(result["stop"]["step"], "reset-1")
        self.assertIs(steps[-1]["reset_success"], False)
        self.assertEqual(self.engine.requests, [])

    def test_bound_failures_are_recorded_and_sequence_continues(self):
        # A reset that reports success but keeps blocks makes later cold steps
        # see hits. That is a recorded check failure, not a stop rule.
        self.engine.ignore_reset = True
        result, steps = self.execute()
        self.assertEqual(result["status"], "completed_with_failures")
        self.assertIn("late-warmup: cached_zero", result["failed_checks"])
        self.assertEqual(len(steps), len(self.inputs[0]["sequence"]))

    def test_existing_inputs_are_validated(self):
        with self.assertRaises(InputError):
            origin("http://localhost:8000")
        with self.assertRaises(InputError):
            origin("http://127.0.0.1")
        with self.assertRaises(InputError):
            origin("https://127.0.0.1:8000")
        (self.root / "probes/base.request.json").write_bytes(b"{}")
        with self.assertRaisesRegex(InputError, "hash changed"):
            load_inputs(CALIBRATION / "probes.json", self.root / "probes")


if __name__ == "__main__":
    unittest.main()
