"""Offline tests. Every transport is fake; never loads .env or contacts XHub."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('local_pilot', Path(__file__).with_name('run.py'))
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class Response:
    def __init__(self, model='gpt-4o', finish='stop', answer='完整回答', code=200):
        self.status_code = code
        self.headers = {'x-request-id': 'test-request', 'Set-Cookie': 'must-not-retain'}
        self.text = json.dumps(dict(model=model, id='test-response', choices=[dict(
            finish_reason=finish, message={'content': answer})],
            usage=dict(prompt_tokens=10, completion_tokens=20, total_tokens=30)))

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError('HTTP ' + str(self.status_code))


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append(kwargs['json'])
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)
        self.task = dict(split='primary', case_id='c', question_id='q1', model='gpt-4o',
                         messages=[dict(role='system', content='系统'), dict(role='user', content='事实 问题')],
                         question_text='问题', question_hash=mod.sha('问题'))
        self.task['prompt_hash'] = mod.sha(mod.canonical(self.task['messages']))

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, responses):
        transport = FakeTransport(responses)
        runner = mod.Runner([self.task], self.out, transport, 'https://example.invalid/v1',
                            'fake-secret', sleep=lambda _: None)
        return runner, transport

    def test_complete_and_success_never_rerun(self):
        runner, transport = self.runner([Response()])
        result = runner.task(self.task)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(runner.task(self.task), result)
        self.assertEqual(len(transport.requests), 1)
        self.assertNotIn('thinking', transport.requests[0])
        self.assertEqual(transport.requests[0]['temperature'], .3)
        receipt = mod.read(self.out / mod.task_id(self.task) / 'attempt_001.response.json')
        self.assertEqual(json.loads(receipt['raw_body']), receipt['raw_response'])
        self.assertNotIn('set-cookie', receipt['headers'])

    def test_one_length_escalation_only(self):
        runner, transport = self.runner([Response(finish='length'), Response(finish='length')])
        result = runner.task(self.task)
        self.assertEqual(result['status'], 'truncated')
        self.assertEqual([x['max_tokens'] for x in transport.requests], [3000, 6000])
        self.assertEqual(result['usage']['total_tokens'], 60)

    def test_length_then_full_response(self):
        runner, transport = self.runner([Response(finish='length'), Response()])
        result = runner.task(self.task)
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['length_retried'])

    def test_transport_uncertainty_is_not_retried(self):
        runner, transport = self.runner([TimeoutError('fake-secret timeout')])
        result = runner.task(self.task)
        self.assertEqual(result['status'], 'needs_reconcile')
        self.assertEqual(len(transport.requests), 1)
        receipt = mod.read(self.out / mod.task_id(self.task) / 'attempt_001.response.json')
        self.assertNotIn('fake-secret', json.dumps(receipt))

    def test_gateway_timeout_is_not_retried(self):
        runner, transport = self.runner([Response(code=504)])
        self.assertEqual(runner.task(self.task)['status'], 'needs_reconcile')
        self.assertEqual(len(transport.requests), 1)

    def test_refusal_is_not_retried(self):
        response = Response()
        body = response.json()
        body['choices'][0]['message'] = {'refusal': '不能回答'}
        response.text = json.dumps(body)
        runner, transport = self.runner([response])
        result = runner.task(self.task)
        self.assertEqual(result['status'], 'refusal')
        self.assertEqual(result['answer'], '不能回答')
        self.assertEqual(len(transport.requests), 1)

    def test_known_interface_errors_bounded(self):
        runner, transport = self.runner([Response(code=503) for _ in range(9)])
        self.assertEqual(runner.task(self.task)['status'], 'interface_error')
        self.assertEqual(len(transport.requests), 9)

    def test_missing_terminal_never_replays(self):
        directory = self.out / mod.task_id(self.task)
        mod.write(directory / 'attempt_001.request.json', {'payload': mod.payload(self.task, 3000)}, True)
        runner, transport = self.runner([])
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            runner.task(self.task)
        self.assertEqual(transport.requests, [])

    def test_budget_stop_has_explicit_unsent_receipt(self):
        runner, transport = self.runner([mod.BudgetStop('cap')])
        self.assertIsNone(runner.task(self.task))
        receipt = mod.read(self.out / mod.task_id(self.task) / 'attempt_001.response.json')
        self.assertTrue(receipt['not_sent'])
        self.assertFalse((self.out / mod.task_id(self.task) / 'terminal.json').exists())

    def test_price_formula_verified(self):
        entries = {m: dict(model_name=m, quota_type=0, model_ratio=1, completion_ratio=4) for m in mod.MODELS}
        market = dict(checked_at=mod.now(), endpoint='https://example.invalid/v1',
                      catalog_present=list(mod.MODELS), quota_per_unit=500000,
                      conservative_group_ratio=1, pricing_entries=entries,
                      prices_usd_per_million={m: [2, 8] for m in mod.MODELS})
        mod.validate_market(market)
        market['prices_usd_per_million']['gpt-4o'][0] = .1
        with self.assertRaisesRegex(ValueError, 'metadata'):
            mod.validate_market(market)

    def test_guard_records_every_physical_attempt_cost(self):
        def fake_get(*args, **kwargs):
            result = Response()
            result.text = json.dumps({'data': {'total_used': 0}})
            return result
        with patch('requests.get', side_effect=fake_get), patch('requests.post', return_value=Response()):
            guard = mod.GuardedTransport('https://example.invalid/v1', 'fake-secret', self.out,
                                         {'gpt-4o': [2.5, 10]}, 500000, 1, prepaid_usd=0)
            guard.post('https://example.invalid/v1/chat/completions', json=mod.payload(self.task, 3000))
        ledger = guard.snapshot()
        self.assertEqual(ledger['completed_http_attempts'], 1)
        self.assertAlmostEqual(ledger['estimated_all_http_usd'], .000225)
        self.assertEqual(ledger['reservations'], {})
        receipt = mod.read(self.out / 'http_receipts/http_0000001.json')
        self.assertEqual(receipt['request'], mod.payload(self.task, 3000))
        self.assertEqual(receipt['usage']['total_tokens'], 30)

    def test_guard_denies_without_paid_call(self):
        with patch('requests.get', side_effect=RuntimeError('offline')), patch('requests.post') as paid:
            guard = mod.GuardedTransport('https://example.invalid/v1', 'fake-secret', self.out,
                                         {'gpt-4o': [2.5, 10]}, 500000, .000001, prepaid_usd=0)
            with self.assertRaises(mod.BudgetStop):
                guard.post('https://example.invalid/v1/chat/completions', json=mod.payload(self.task, 3000))
            paid.assert_not_called()


if __name__ == '__main__':
    unittest.main()
