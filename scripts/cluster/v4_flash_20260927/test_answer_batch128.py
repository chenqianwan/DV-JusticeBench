import copy
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

import run_answer_batch128 as batch
from test_answer_smoke import fixture as smoke_fixture


def fixture():
    return dict(batch.CONFIG, tasks=smoke_fixture()['tasks'])


def reply(content='Complete answer', finish='stop', refusal=None, model='deepseek_v4_flash'):
    return 200, {'x-request-id': 'http-id'}, json.dumps(dict(id='response-id', model=model,
        choices=[dict(message=dict(content=content, refusal=refusal, reasoning_content='not answer'), finish_reason=finish)],
        usage=dict(prompt_tokens=10, completion_tokens=15, total_tokens=25)))


class FakeHTTP:
    def __init__(self, sequence=(), always=None):
        self.calls, self.sequence, self.always, self.lock = [], list(sequence), always, threading.Lock()

    def __call__(self, payload):
        with self.lock:
            self.calls.append(copy.deepcopy(payload))
            value = self.sequence.pop(0) if self.sequence else self.always or reply()
        if isinstance(value, Exception):
            raise value
        return value


class Sampler:
    def __init__(self, healthy=True, kv=.1, preempt=0):
        self.healthy, self.kv, self.preempt, self.calls = healthy, kv, preempt, 0

    def __call__(self):
        self.calls += 1
        return dict(healthy=self.healthy, gpu_returncode=0, gpu_anomalies=[],
                    gpus=[dict(index=str(i), name='H800', utilization_pct='50', memory_used_mib='10000',
                               memory_total_mib='80000', power_w='300', temperature_c='60') for i in range(8)],
                    metrics=dict(running=0, waiting=0, kv_cache_usage=self.kv, preemptions_total=self.preempt))


class Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / 'results'
        self.data = fixture()
        self.endpoint = dict(status='ready', job_id='402816', model='deepseek_v4_flash', hostname='dgx-040', max_num_seqs=128)

    def runner(self, http, **kwargs):
        sampler = kwargs.pop('sampler', Sampler())
        return batch.Runner(batch.base.canonical(self.data), self.output, http, sampler, self.endpoint,
                            sleep=lambda seconds: None, **kwargs)

    def test_segment_then_resume_only_remaining_and_preserve_success(self):
        first = FakeHTTP()
        summary = self.runner(first, max_new_tasks=3).run()
        self.assertEqual(len(first.calls), 3)
        self.assertEqual(summary['status'], 'segment_completed')
        self.assertEqual(summary['pending'], 7)
        self.assertEqual(summary['actual_attempts'], 3)
        terminals = {p: p.read_bytes() for p in self.output.glob('task_*/terminal.json')}
        with self.assertRaisesRegex(ValueError, 'explicit --resume'):
            self.runner(FakeHTTP()).run()
        second = FakeHTTP()
        summary = self.runner(second, resume=True).run()
        self.assertEqual(len(second.calls), 7)
        self.assertEqual(summary['success'], 10)
        self.assertEqual(summary['actual_attempts'], 10)
        self.assertEqual(summary['usage_totals']['total_tokens'], 250)
        self.assertEqual(summary['reused_existing_answers'], 10)
        self.assertEqual(summary['combined_expected_answers'], 20)
        for path, raw in terminals.items():
            self.assertEqual(path.read_bytes(), raw)
        third, sampler = FakeHTTP(), Sampler()
        self.runner(third, resume=True, sampler=sampler).run()
        self.assertEqual(third.calls, [])
        self.assertEqual(sampler.calls, 0)

    def test_truncation_once_original_payload_and_usage(self):
        http = FakeHTTP([reply('Partial', 'length')])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual([p['max_tokens'] for p in http.calls], [3000, 6000])
        self.assertEqual(summary['length_retries'], 1)
        self.assertEqual(summary['actual_attempts'], 2)
        self.assertEqual(summary['usage_totals']['total_tokens'], 50)
        terminal = json.loads(next(self.output.glob('task_*/terminal.json')).read_text())
        self.assertEqual(terminal['usage']['total_tokens'], 50)
        self.assertEqual(terminal['final_response_usage']['total_tokens'], 25)
        self.assertEqual(terminal['response_model'], 'deepseek_v4_flash')
        for payload in http.calls:
            self.assertEqual(payload['messages'], self.data['tasks'][0]['messages'])
            self.assertEqual(payload['chat_template_kwargs'], {'thinking': False})
            self.assertEqual(payload['temperature'], .3)
            self.assertEqual(terminal['request_sha256_by_token_limit'][str(payload['max_tokens'])],
                             batch.base.digest(batch.base.canonical(payload)))

    def test_refusal_is_terminal_without_retry(self):
        http = FakeHTTP([reply('', refusal='refused')])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(summary['counts']['refusal'], 1)
        self.assertEqual(summary['success'], 0)

    def test_known_503_has_bounded_retries(self):
        http = FakeHTTP(always=(503, {}, json.dumps({'error': {'message': 'unavailable'}})))
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 9)
        self.assertEqual(summary['actual_attempts'], 9)
        self.assertEqual(summary['counts']['interface_error'], 1)
        self.assertLessEqual(summary['actual_attempts'], 18)

    def test_transport_timeout_needs_reconcile_not_retried(self):
        http = FakeHTTP([TimeoutError('secret diagnostic must not leak')])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(summary['needs_reconcile'], 1)
        self.assertEqual(summary['status'], 'paused')
        self.assertEqual(summary['actual_attempts'], 1)
        all_text = ''.join(p.read_text() for p in self.output.rglob('*.json'))
        self.assertNotIn('secret diagnostic', all_text)

    def test_unknown_existing_intent_not_resent(self):
        runner = self.runner(FakeHTTP())
        self.output.mkdir()
        batch.base.atomic(self.output / 'run_identity.json', runner.identity, exclusive=True)
        task = self.data['tasks'][0]
        directory = runner.task_dir(task)
        directory.mkdir()
        payload = dict(model='deepseek_v4_flash', messages=task['messages'], temperature=.3,
                       max_tokens=3000, chat_template_kwargs={'thinking': False})
        batch.base.atomic(directory / 'attempt_001.request.json', {'payload': payload}, exclusive=True)
        http = FakeHTTP()
        summary = self.runner(http, resume=True, max_new_tasks=1).run()
        self.assertEqual(http.calls, [])
        self.assertEqual(summary['needs_reconcile'], 1)
        self.assertEqual(summary['unmatched_intents'], 1)

    def test_health_or_high_kv_prevents_dispatch(self):
        http = FakeHTTP()
        summary = self.runner(http, sampler=Sampler(kv=.85)).run()
        self.assertEqual(http.calls, [])
        self.assertIn('kv_cache_at_or_above_85_percent', summary['stop_reasons'])

    def test_missing_gpu_observation_stops_dispatch(self):
        observed = Sampler()()
        observed['gpus'].pop()
        http = FakeHTTP()
        summary = self.runner(http, sampler=lambda: observed).run()
        self.assertEqual(http.calls, [])
        self.assertIn('gpu_observation_failure', summary['stop_reasons'])

    def test_monitor_exception_is_fail_closed_without_secret_output(self):
        runner = self.runner(FakeHTTP())
        runner.output.mkdir()
        runner.started, runner.started_at = time.monotonic(), 'test'
        runner.monitor_done = SimpleNamespace(wait=lambda seconds: False)
        def broken():
            raise RuntimeError('confidential text')
        runner.sample = broken
        runner.monitor()
        self.assertTrue(runner.stop.is_set())
        self.assertIn('monitor_exception_RuntimeError', runner.stop_reasons)
        self.assertNotIn('confidential', (runner.output / 'run_state.json').read_text())

    def test_real_input_and_prompt_tamper_validation(self):
        path = Path(__file__).resolve().parents[3] / 'data/pkulaw_v4_answer_batch128_20260928/input_tasks.json'
        batch.validate(json.loads(path.read_bytes()))
        self.data['tasks'][0]['messages'][1]['content'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'Prompt hash'):
            batch.validate(self.data)


if __name__ == '__main__':
    unittest.main()
