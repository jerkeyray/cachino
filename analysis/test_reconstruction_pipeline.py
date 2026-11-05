"""Exercise trace orchestration with a declared byte-token test double.

These tests verify provenance, ordering and rejection behavior. Their token IDs
are fixture bytes, not Qwen output or evidence of engine/template equivalence.
"""
import hashlib
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reconstruct import main, reconstruct
from tokenizer_assets import load_pin


class ByteTokenizerFixture:
    """An explicit orchestration fixture with deterministic text and byte IDs."""
    chat_template = "test-fixture-only"

    def apply_chat_template(self, messages, tokenize, **options):
        text = json.dumps(messages, ensure_ascii=False)
        return list(text.encode()) if tokenize else text

    def encode(self, text, add_special_tokens):
        if add_special_tokens:
            raise AssertionError("extra special tokens would duplicate template tokens")
        return list(text.encode())

    def decode(self, ids, **options):
        return bytes(ids).decode()


class ReconstructionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'capture'
        self.source.mkdir()
        self.out = self.root / 'derived'
        self.pin = load_pin()
        self.events = self.source / 'events.jsonl'
        fixture_runtime = {'tokenizers': 'fixture', 'transformers': 'fixture', 'jinja2': 'fixture', 'huggingface-hub': 'fixture'}
        self.loader = patch('reconstruct._load_tokenizer', return_value=(ByteTokenizerFixture(), {'fixture': True}, fixture_runtime))
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def add(self, number, body, session='s', complete=True, path='/v1/chat/completions'):
        """Create genuine trace-format sidecars with known hashes and lifecycle."""
        request_id = '%032x' % number
        directory = self.source / request_id
        directory.mkdir()
        data = body.encode()
        (directory / 'request.body').write_bytes(data)
        descriptor = {'path': request_id + '/request.body', 'stored_bytes': len(data),
                      'observed_bytes': len(data), 'sha256_stored': hashlib.sha256(data).hexdigest(),
                      'sha256_observed': hashlib.sha256(data).hexdigest(),
                      'complete': complete, 'truncated': False, 'capture_error': False}
        record = {'schema_version': 1, 'request_id': request_id, 'sequence': number,
                  'state': 'in_progress', 'path': path, 'request': descriptor}
        if session is not None:
            record['session_id'] = session
        with self.events.open('a') as file:
            file.write(json.dumps({'event': 'started', 'record': record}) + '\n')
            record['state'] = 'completed'
            file.write(json.dumps({'event': 'finished', 'record': record}) + '\n')
        return request_id

    def request(self, content='hello', **fields):
        return json.dumps(dict(model=self.pin['model_id'], messages=[{'role': 'user', 'content': content}], **fields))

    def test_messages_route_rejected_even_with_chat_shaped_body(self):
        self.add(1, self.request(), path='/v1/messages')
        self.add(2, self.request())
        result = self.run_pipeline(remap=True)
        self.assertEqual(result['requests'][0]['status'], 'unsupported')
        self.assertIn('only OpenAI', result['requests'][0]['reason'])
        self.assertIsNone(result['requests'][1]['previous_request_id'])

    def run_pipeline(self, remap=False):
        return reconstruct(self.source, self.root / 'unused-fixture-assets', self.out, self.pin, remap)

    def test_interleaved_sessions_and_raw_source_preservation(self):
        first = self.add(1, self.request('héllo 世界'), session='a')
        self.add(2, self.request('unrelated'), session='b')
        self.add(3, self.request('héllo 世界', stream=True), session='a')
        before = {str(path.relative_to(self.source)): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        result = self.run_pipeline()
        rows = result['requests']
        self.assertIsNone(rows[1]['adjacent_lcp_tokens'])
        self.assertEqual(rows[2]['previous_request_id'], first)
        self.assertEqual(rows[2]['adjacent_lcp_tokens'], rows[2]['prompt_tokens'])
        self.assertEqual(rows[0]['token_ids_sha256'], rows[2]['token_ids_sha256'])
        after = {str(path.relative_to(self.source)): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(result['engine_validated'])
        self.assertTrue((self.out / (first + '.tokens.json')).is_file())
        self.assertEqual(json.loads((self.out / 'summary.json').read_text())['libraries']['transformers'], 'fixture')

    def test_rejected_or_partial_request_breaks_comparison_chain(self):
        self.add(1, self.request())
        self.add(2, 'not JSON')
        self.add(3, self.request())
        self.add(4, self.request(), complete=False)
        self.add(5, self.request())
        rows = self.run_pipeline()['requests']
        self.assertEqual([row['status'] for row in rows], ['rendered', 'unsupported', 'rendered', 'unsupported', 'rendered'])
        self.assertIsNone(rows[2]['previous_request_id'])
        self.assertIsNone(rows[4]['adjacent_lcp_tokens'])
        self.assertIn('partial', rows[3]['reason'])

    def test_unlabeled_requests_never_share_a_session(self):
        self.add(1, self.request(), session=None)
        self.add(2, self.request(), session=None)
        rows = self.run_pipeline()['requests']
        self.assertTrue(all(row['adjacent_lcp_tokens'] is None for row in rows))

    def test_remapping_is_recorded_and_outputs_cannot_be_overwritten(self):
        body = json.loads(self.request())
        body['model'] = 'fake-model'
        self.add(1, json.dumps(body))
        result = self.run_pipeline(remap=True)
        row = result['requests'][0]
        self.assertTrue(result['allow_model_remap'])
        self.assertTrue(row['model_remapped'])
        self.assertEqual(row['source_model'], 'fake-model')
        with self.assertRaises(FileExistsError):
            self.run_pipeline(remap=True)

    def test_disagreeing_tokenization_paths_abort_without_success_summary(self):
        self.add(1, self.request())
        # A renderer-integrity failure must propagate; silently calling the
        # request unsupported would hide a broken reconstruction implementation.
        with patch.object(ByteTokenizerFixture, 'encode', return_value=[999]):
            with self.assertRaisesRegex(ValueError, 'token IDs differ'):
                self.run_pipeline()
        self.assertFalse((self.out / 'summary.json').exists())

    def test_setup_check_loads_tokenizer_without_creating_a_dataset(self):
        with patch('sys.argv', ['reconstruct.py', '--assets', str(self.root / 'unused-fixture-assets'), '--check-setup']), contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        self.assertIn('Tokenizer setup verified', output.getvalue())
        self.assertFalse(self.out.exists())
        self.assertFalse(self.events.exists())
