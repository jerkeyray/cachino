import copy
import json
from pathlib import Path
import sys
import unittest

CALIBRATION = Path(__file__).resolve().parents[1] / "experiments/cache-ground-truth"
sys.path.insert(0, str(CALIBRATION))

from build_probes import body_bytes, expand  # noqa: E402
from check_manifest import REQUIRED, check  # noqa: E402


def filled_manifest():
    """Fill every template null with a placeholder that satisfies the gates."""
    manifest = json.loads((CALIBRATION / "run-manifest.template.json").read_text())
    for section, fields in manifest.items():
        if isinstance(fields, dict):
            for key, value in fields.items():
                if value is None:
                    fields[key] = REQUIRED.get((section, key), "recorded")
    manifest["rendering"]["default_chat_template_kwargs"] = {}
    manifest["telemetry"]["kv_events_config"] = "disabled"
    return manifest


class ManifestTests(unittest.TestCase):
    def test_template_reports_missing_gates_without_conflicts(self):
        missing, conflicts = check(json.loads((CALIBRATION / "run-manifest.template.json").read_text()))
        self.assertIn("telemetry.enable_prompt_tokens_details", missing)
        self.assertIn("cache.block_size_tokens", missing)
        self.assertEqual(conflicts, [])

    def test_filled_manifest_is_ready(self):
        self.assertEqual(check(filled_manifest()), ([], []))

    def test_contradicting_values_are_conflicts_not_defaults(self):
        for section, key, value in (("topology", "tensor_parallel_size", 2),
                                    ("telemetry", "enable_prompt_tokens_details", False),
                                    ("telemetry", "vllm_server_dev_mode", False),
                                    ("cache", "cpu_offload_or_external_connector", True),
                                    ("telemetry", "kv_events_config", "off")):
            manifest = filled_manifest()
            manifest[section][key] = value
            missing, conflicts = check(manifest)
            self.assertEqual(missing, [])
            self.assertEqual(len(conflicts), 1, (section, key))

    def test_template_defaults_beyond_thinking_need_validation(self):
        manifest = filled_manifest()
        manifest["rendering"]["default_chat_template_kwargs"] = {"enable_thinking": True}
        self.assertEqual(check(manifest), ([], []))
        manifest["rendering"]["default_chat_template_kwargs"] = {"enable_thinking": False, "custom": 1}
        self.assertEqual(len(check(manifest)[1]), 1)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((CALIBRATION / "probes.json").read_text())

    def test_mutations_change_only_their_intended_text(self):
        probes = expand(self.spec)
        base, early, late = probes["base"]["messages"], probes["early-mutation"]["messages"], probes["late-mutation"]["messages"]
        changed = [i for i, (a, b) in enumerate(zip(base[0]["content"].splitlines(), early[0]["content"].splitlines())) if a != b]
        self.assertEqual(changed, [self.spec["text"]["early_mutation"]["rule_index"] - 1])
        self.assertEqual(base[1], early[1])
        self.assertEqual(base[0], late[0])
        self.assertNotEqual(base[1], late[1])
        self.assertEqual(probes["turn-2"]["messages"][:2], base)

    def test_every_probe_carries_calibration_controls(self):
        for name, request in expand(self.spec).items():
            self.assertIs(request["return_token_ids"], True, name)
            self.assertEqual(request["chat_template_kwargs"], {"enable_thinking": False}, name)
            self.assertNotIn("{", "".join(m["content"] for m in request["messages"] if m["content"].startswith("{")))

    def test_body_bytes_are_deterministic(self):
        first, second = expand(self.spec), expand(copy.deepcopy(self.spec))
        self.assertEqual(body_bytes(first["base"]), body_bytes(second["base"]))

    def test_unknown_placeholder_and_ambiguous_mutation_fail(self):
        spec = copy.deepcopy(self.spec)
        spec["probes"]["bad"] = {"messages": [{"role": "user", "content": "{missing}"}]}
        with self.assertRaisesRegex(ValueError, "placeholder"):
            expand(spec)
        spec = copy.deepcopy(self.spec)
        spec["text"]["early_mutation"]["find"] = "e"
        with self.assertRaisesRegex(ValueError, "exactly once"):
            expand(spec)

    def test_sequence_references_defined_probes_and_resets(self):
        names = set(self.spec["probes"])
        for step in self.spec["sequence"]:
            if step["action"] == "send":
                self.assertIn(step["probe"], names)
                self.assertIn(step["route"], {"direct", "proxy"})
            else:
                self.assertIn(step["action"], {"get_models", "reset_prefix_cache"})
        cold = [i for i, s in enumerate(self.spec["sequence"]) if s["step"] in {"cold-base", "direct-cold", "proxy-cold", "late-warmup", "history-turn-1"}]
        for index in cold:
            self.assertEqual(self.spec["sequence"][index - 1]["action"], "reset_prefix_cache")


if __name__ == "__main__":
    unittest.main()
