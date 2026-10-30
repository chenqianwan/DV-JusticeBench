import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest

import run_answer_smoke as smoke


def fixture():
    data = dict(smoke.CONFIG, tasks=[])
    for split in ('primary', 'secondary'):
        for i in range(5):
            question = split + '_question_' + str(i)
            messages = [{'role': 'system', 'content': 'Original system prompt'},
                        {'role': 'user', 'content': 'Masked facts; question: ' + question}]
            data['tasks'].append(dict(split=split, case_id=split + '_case', question_id=str(i),
                messages=messages, prompt_hash=smoke.digest(smoke.canonical(messages)),
                source_case_text_hash='a' * 64, masked_case_text_hash='b' * 64,
                question_hash=smoke.digest(question.encode()), question_text=question))
    return data


def response(content='answer', finish='stop', refusal=None):
    return 200, {'x-request-id': 'http-request-id'}, json.dumps({'id': 'response-id',
        'model': 'deepseek_v4_flash', 'choices': [{'finish_reason': finish,
        'message': {'content': content, 'reasoning_content': 'not the answer', 'refusal': refusal}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})


class FakeHTTP:
    def __init__(self, sequence=()):
        self.sequence, self.calls = list(sequence), []
        self.lock = threading.Lock()

    def __call__(self, payload):
        with self.lock:
            self.calls.append(copy.deepcopy(payload))
            return self.sequence.pop(0) if self.sequence else response()


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / 'results'
        self.data = fixture()

    def runner(self, http, data=None):
        return smoke.Runner(smoke.canonical(data or self.data), self.out, http, sleep=lambda _: None)

    def test_truncation_once_parameters_and_no_judge_leakage(self):
        http = FakeHTTP([response('partial', 'length')])
        summary = self.runner(http).run()
        self.assertEqual(summary['success'], 10)
        self.assertEqual(summary['actual_attempts'], 11)
        self.assertEqual(summary['usage_totals']['total_tokens'], 165)
        self.assertEqual(sum(x['max_tokens'] == 6000 for x in http.calls), 1)
        allowed = {'model', 'messages', 'temperature', 'max_tokens', 'chat_template_kwargs'}
        original = [t['messages'] for t in self.data['tasks']]
        for payload in http.calls:
            self.assertEqual(set(payload), allowed)
            self.assertIn(payload['messages'], original)
            self.assertEqual(payload['chat_template_kwargs'], {'thinking': False})
            self.assertEqual(payload['temperature'], 0.3)
        rows = [json.loads(x) for split in ('primary', 'secondary')
                for x in (self.out / ('answers_deepseek_v4_flash_' + split + '.jsonl')).read_text().splitlines()]
        retried = next(row for row in rows if row['attempts'] == 2)
        self.assertEqual(retried['usage']['total_tokens'], 30)
        self.assertEqual(retried['final_response_usage']['total_tokens'], 15)
        self.assertEqual(retried['requested_model'], 'deepseek_v4_flash')
        self.assertEqual(retried['response_model'], 'deepseek_v4_flash')
        self.assertEqual(retried['response_id'], 'response-id')
        self.assertEqual(retried['request_id'], 'http-request-id')
        self.assertEqual(retried['temperature'], 0.3)
        self.assertIs(retried['thinking'], False)
        self.assertGreaterEqual(retried['elapsed_s'], 0)
        before = {split: (self.out / ('answers_deepseek_v4_flash_' + split + '.jsonl')).read_bytes()
                  for split in ('primary', 'secondary')}
        resumed_http = FakeHTTP()
        self.runner(resumed_http).run()
        self.assertEqual(resumed_http.calls, [])
        for split in before:
            self.assertEqual(before[split], (self.out / ('answers_deepseek_v4_flash_' + split + '.jsonl')).read_bytes())

    def test_explicit_refusal_not_retried_and_reasoning_not_answer(self):
        http = FakeHTTP([response('', refusal='cannot answer')])
        summary = self.runner(http).run()
        self.assertEqual(summary['actual_attempts'], 10)
        self.assertEqual(summary['counts']['refusal'], 1)
        failures = [json.loads(x) for x in (self.out / 'failures.jsonl').read_text().splitlines()]
        self.assertEqual(failures[0]['answer'], '')
        self.assertEqual(failures[0]['status'], 'refusal')

    def test_content_filter_not_retried(self):
        http = FakeHTTP([response('', 'content_filter')])
        summary = self.runner(http).run()
        self.assertEqual(summary['actual_attempts'], 10)
        self.assertEqual(summary['counts']['content_filter'], 1)

    def test_finished_resume_makes_no_requests(self):
        self.runner(FakeHTTP()).run()
        http = FakeHTTP()
        summary = self.runner(http).run()
        self.assertEqual(http.calls, [])
        self.assertEqual(summary['actual_attempts'], 10)

    def test_unknown_inflight_not_resent(self):
        runner = self.runner(FakeHTTP())
        self.out.mkdir()
        smoke.atomic(self.out / 'run_identity.json', runner.identity, exclusive=True)
        task = self.data['tasks'][0]
        directory = runner.task_dir(task)
        directory.mkdir()
        payload = {'model': smoke.CONFIG['model'], 'messages': task['messages'], 'temperature': 0.3,
                   'max_tokens': 3000, 'chat_template_kwargs': {'thinking': False}}
        smoke.atomic(directory / 'attempt_001.request.json', {'payload': payload}, exclusive=True)
        http = FakeHTTP()
        summary = self.runner(http).run()
        self.assertEqual(len(http.calls), 9)
        self.assertEqual(summary['counts']['interrupted'], 1)
        self.assertEqual(summary['uncertain_request_intents'], 1)

    def test_validation(self):
        for mutate in (lambda d: d.update(temperature=0.7),
                       lambda d: d['tasks'][0].update(judge_decision='secret judgment'),
                       lambda d: d['tasks'].__setitem__(1, copy.deepcopy(d['tasks'][0])),
                       lambda d: d['tasks'][0]['messages'][1].update(content='tampered')):
            data = copy.deepcopy(self.data)
            mutate(data)
            with self.assertRaises(ValueError):
                self.runner(FakeHTTP(), data)

    def test_resume_input_change_rejected(self):
        self.runner(FakeHTTP()).run()
        data = copy.deepcopy(self.data)
        data['input_manifest_hash'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'Resume refused'):
            self.runner(FakeHTTP(), data).run()

    def test_http_limit_persisted_and_usage_for_empty_retries(self):
        http = FakeHTTP([response('') for _ in range(30)])
        summary = self.runner(http).run()
        self.assertEqual(summary['success'], 0)
        self.assertEqual(summary['actual_attempts'], 30)
        self.assertEqual(summary['counts']['empty'], 10)
        self.assertEqual(summary['usage_totals']['total_tokens'], 450)
        http2 = FakeHTTP()
        self.runner(http2).run()
        self.assertEqual(http2.calls, [])


if __name__ == '__main__':
    unittest.main()
