"""Synthetic receipts only: no SSH, HTTP, Slurm, or model calls."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import audit_thinking_pilot as audit
import run_thinking_pipeline as pipeline
from test_answer_batch128 import Sampler, reply
from test_selfscore_batch128_v2 import evaluation_text


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='synthetic_thinking_audit_')
        cls.snapshot = Path(cls.temp.name).resolve()
        raw = (audit.SOURCE / 'input_tasks.json').read_bytes()
        data = json.loads(raw)
        mapped = (audit.SOURCE / 'task_mapping.jsonl').read_bytes()
        rows = [json.loads(line) for line in mapped.splitlines()]
        bundle, bundle_sha = pipeline.load_bundle(audit.SOURCE)
        original_prompts = {audit.digest(audit.canonical(t['messages'])) for t in data['tasks'][:10]}

        def fake_transport(payload):
            generating = audit.digest(audit.canonical(payload['messages'])) in original_prompts
            status, headers, body = reply('完整测试回答' if generating else evaluation_text())
            value = json.loads(body)
            message = value['choices'][0]['message']
            message['reasoning'] = message.pop('reasoning_content')
            return status, headers, json.dumps(value)

        endpoint = dict(status='ready', job_id='402816', model='deepseek_v4_flash', hostname='dgx-040', max_num_seqs=128)
        runner = pipeline.Runner(raw, cls.snapshot / 'results', fake_transport, Sampler(), endpoint,
                                 bundle, bundle_sha, rows, max_new_tasks=10, sleep=lambda _: None,
                                 mapping_sha256=audit.MAPPING_SHA)
        with contextlib.redirect_stdout(io.StringIO()):
            cls.summary = runner.run()
        (cls.snapshot / 'dispatches').mkdir()
        pipeline.base.atomic(cls.snapshot / 'dispatches/attempt-001.intent.json',
                             dict(dispatch_id='attempt-001', max_new_tasks=10, resume=False), exclusive=True)
        pipeline.base.atomic(cls.snapshot / 'dispatches/attempt-001.exit.json',
                             dict(dispatch_id='attempt-001', exit_code=0), exclusive=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_positive_pilot_and_reasoning_alias(self):
        result = audit.Audit(self.snapshot).run()
        self.assertTrue(result['full_continuation_safe'])
        self.assertEqual(result['valid_scores'], 10)
        self.assertEqual(result['audited_questions'], 10)
        self.assertEqual(result['remaining_unstarted_questions'], 11085)
        self.assertEqual(result['cases_by_split'], {'primary': 1, 'secondary': 1})
        self.assertEqual(len(result['requests']), 20)
        self.assertEqual(result['usage_total']['total_tokens'], 500)

    def test_wrong_thinking_detected(self):
        path = next((self.snapshot / 'results').glob('task_*/attempt_001.request.json'))
        original = path.read_bytes()
        value = json.loads(original)
        value['payload']['chat_template_kwargs']['thinking'] = False
        try:
            pipeline.base.atomic(path, value)
            inspector = audit.Audit(self.snapshot)
            with self.assertRaisesRegex(ValueError, 'Summary status counts'):
                inspector.run()
            self.assertEqual(len(inspector.errors), 1)
            self.assertEqual(inspector.errors[0]['error'], 'Payload mode/model mismatch')
        finally:
            pipeline.base.atomic(path, original.decode(), raw=True)

    def test_reasoning_mixed_into_score_detected(self):
        path = next((self.snapshot / 'results').glob('task_*/attempt_002.request.json'))
        original = path.read_bytes()
        value = json.loads(original)
        value['payload']['messages'][1]['content'] += '\nnot answer'
        try:
            pipeline.base.atomic(path, value)
            inspector = audit.Audit(self.snapshot)
            with self.assertRaisesRegex(ValueError, 'Summary status counts'):
                inspector.run()
            self.assertEqual(len(inspector.errors), 1)
            self.assertIn('Scoring messages differ', inspector.errors[0]['error'])
        finally:
            pipeline.base.atomic(path, original.decode(), raw=True)

    def test_nonzero_exit_is_not_safe(self):
        path = self.snapshot / 'dispatches/attempt-001.exit.json'
        original = path.read_bytes()
        try:
            pipeline.base.atomic(path, dict(dispatch_id='attempt-001', exit_code=1))
            result = audit.Audit(self.snapshot).run()
            self.assertFalse(result['full_continuation_safe'])
            self.assertEqual(result['dispatch_exit_code'], 1)
        finally:
            pipeline.base.atomic(path, original.decode(), raw=True)


if __name__ == '__main__':
    unittest.main()
