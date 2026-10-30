import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

import benchmark_concurrency as benchmark


def fixture():
    data = dict(benchmark.CONFIG, run_id='benchmark-test', tasks=[])
    for i in range(256):
        messages = [{'role': 'system', 'content': 'Original prompt'},
                    {'role': 'user', 'content': 'Original facts and question ' + str(i)}]
        data['tasks'].append(dict(task_id=str(i), case_id='case' + str(i), question_id='q1',
                                 messages=messages, prompt_hash=benchmark.base.digest(benchmark.base.canonical(messages))))
    return data


class FakeHTTP:
    def __init__(self, failure=False, model='deepseek_v4_flash'):
        self.calls, self.failure, self.lock = [], failure, threading.Lock()
        self.model = model

    def __call__(self, payload):
        with self.lock:
            self.calls.append(copy.deepcopy(payload))
        time.sleep(.002)
        if self.failure:
            return 503, {}, json.dumps({'error': {'message': 'Service unavailable'}})
        return 200, {}, json.dumps({'id': 'response-id', 'model': self.model,
             'choices': [{'message': {'content': 'Partial benchmark answer'}, 'finish_reason': 'length'}],
             'usage': {'prompt_tokens': 10, 'completion_tokens': 256, 'total_tokens': 266}})


class FakeSampler:
    def __init__(self, healthy=True, gpu_anomalies=None):
        self.calls, self.healthy, self.gpu_anomalies = 0, healthy, gpu_anomalies or []

    def __call__(self):
        self.calls += 1
        return {'healthy': self.healthy, 'gpu_anomalies': self.gpu_anomalies,
                'metrics': {'kv_cache_usage': .4, 'preemptions_total': self.calls, 'running': 0, 'waiting': 0}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / 'results'
        self.data = fixture()
        self.endpoint = dict(status='ready', job_id='402816', model='deepseek_v4_flash', hostname='dgx-040', max_num_seqs=128)

    def runner(self, http, sampler=None):
        return benchmark.Runner(benchmark.base.canonical(self.data), self.out, 16, http,
                                sampler or FakeSampler(), self.endpoint)

    def test_success_and_final_resume_no_new_requests(self):
        http, sampler = FakeHTTP(), FakeSampler()
        summary = self.runner(http, sampler).run()
        self.assertEqual(len(http.calls), 32)
        self.assertEqual(summary['successful'], 32)
        self.assertEqual(summary['counts'], {'ok_length': 32})
        self.assertEqual(summary['usage_totals']['completion_tokens'], 8192)
        self.assertEqual(summary['valid_completion_tokens'], 8192)
        self.assertFalse(summary['formal_experiment'])
        self.assertGreater(summary['completion_tokens_per_s'], 0)
        self.assertLessEqual(summary['max_inflight'], 16)
        self.assertEqual(summary['kv_cache_peak'], .4)
        self.assertEqual(summary['preemption_delta'], 1)
        self.assertEqual(summary['server_running_peak'], 0)
        self.assertEqual(summary['server_waiting_peak'], 0)
        for call in http.calls:
            self.assertEqual(call['max_tokens'], 256)
            self.assertEqual(call['temperature'], .3)
            self.assertIs(call['stream'], False)
            self.assertEqual(call['chat_template_kwargs'], {'thinking': False})
            self.assertIn(call['messages'], [t['messages'] for t in self.data['tasks'][:32]])
        raw = (self.out / 'c016/summary.json').read_bytes()
        other_http, other_sampler = FakeHTTP(), FakeSampler()
        self.runner(other_http, other_sampler).run()
        self.assertEqual(other_http.calls, [])
        self.assertEqual(other_sampler.calls, 0)
        self.assertEqual(raw, (self.out / 'c016/summary.json').read_bytes())

    def test_failures_stop_dispatch_and_are_not_retried(self):
        http = FakeHTTP(failure=True)
        summary = self.runner(http).run()
        self.assertLessEqual(len(http.calls), 16)
        self.assertEqual(summary['successful'], 0)
        self.assertIn('failure_rate_above_10_percent', summary['stop_reasons'])
        self.assertEqual(len(http.calls), len({json.dumps(p['messages']) for p in http.calls}))
        self.assertGreater(summary['not_dispatched'], 0)

    def test_health_failure_prevents_all_posts(self):
        http = FakeHTTP()
        summary = self.runner(http, FakeSampler(healthy=False)).run()
        self.assertEqual(http.calls, [])
        self.assertIn('service_health_failure', summary['stop_reasons'])

    def test_wrong_response_model_is_not_success(self):
        summary = self.runner(FakeHTTP(model='wrong-model')).run()
        self.assertEqual(summary['successful'], 0)
        self.assertEqual(summary['valid_completion_tokens'], 0)
        results = [json.loads(path.read_text()) for path in (self.out / 'c016').glob('task_*/terminal.json')]
        self.assertTrue(results)
        self.assertTrue(all(row['error'] == 'response_model_mismatch' for row in results))

    def test_endpoint_capacity_rejected(self):
        self.endpoint['max_num_seqs'] = 8
        http = FakeHTTP()
        with self.assertRaisesRegex(ValueError, 'max_num_seqs'):
            self.runner(http).run()
        self.assertEqual(http.calls, [])

    def test_unknown_intent_is_not_reissued(self):
        http = FakeHTTP()
        runner = self.runner(http)
        runner.output.mkdir(parents=True)
        benchmark.base.atomic(runner.output / 'run_identity.json', runner.identity, exclusive=True)
        directory = runner.task_dir(self.data['tasks'][0])
        directory.mkdir()
        benchmark.base.atomic(directory / 'attempt_001.request.json', {'payload': 'uncertain'}, exclusive=True)
        summary = runner.run()
        self.assertEqual(http.calls, [])
        self.assertEqual(summary['unfinished_request_intents'], 1)
        self.assertIn('interrupted_round_requires_manual_review_no_redispatch', summary['stop_reasons'])

    def test_metrics_and_percentiles(self):
        parsed = benchmark.parse_metrics('''# HELP test
vllm:num_requests_running{engine="0"} 4
vllm:num_requests_running{engine="1"} 5
vllm:gpu_cache_usage_perc{engine="0"} 0.7
vllm:gpu_cache_usage_perc{engine="1"} 0.8
vllm:num_preemptions_total 3
vllm:generation_tokens_total 2e3
vllm:prefix_cache_queries_total 40
vllm:prefix_cache_hits 20
''')
        self.assertEqual(parsed['running'], 9)
        self.assertEqual(parsed['kv_cache_usage'], .8)
        self.assertEqual(parsed['generation_tokens_total'], 2000)
        self.assertEqual(parsed['prefix_cache_queries'], 40)
        self.assertEqual(parsed['prefix_cache_hits'], 20)
        self.assertEqual(benchmark.percentile([1, 2, 3, 4], .5), 2.5)

    def test_frozen_real_input_and_tampering(self):
        path = Path(__file__).resolve().parents[3] / 'data/pkulaw_concurrency_benchmark_20260927/input_benchmark.json'
        benchmark.validate(json.loads(path.read_text()))
        self.data['tasks'][0]['messages'][1]['content'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'hash'):
            benchmark.validate(self.data)


if __name__ == '__main__':
    unittest.main()
