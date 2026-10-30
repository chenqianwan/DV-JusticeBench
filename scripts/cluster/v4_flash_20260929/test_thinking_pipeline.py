import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v4_flash_20260927'))
import run_thinking_pipeline as pipeline
from test_answer_batch128 import FakeHTTP, Sampler, reply
from test_selfscore_batch128_v2 import evaluation_text

REPO = Path(__file__).resolve().parents[3]
BUNDLE, BUNDLE_HASH = pipeline.load_bundle(REPO/'data/pkulaw_v4_selfscore_smoke_20260927')


def fixture():
    tasks, mapping = [], []
    for split in ('primary', 'secondary'):
        for i in range(5):
            row = dict(split=split, case_id=split+'_case', question_id=split+'_q'+str(i),
                       question_text='原问题'+str(i), case_text_masked='已经冻结的原案情',
                       judge_decision_masked='只在评分输入提供的原裁判理由', source_case_text_hash='a'*64)
            for raw, hashed in (('question_text', 'question_hash'), ('case_text_masked', 'masked_case_text_hash'),
                                ('judge_decision_masked', 'judge_decision_hash')):
                row[hashed] = pipeline.base.digest(row[raw].encode())
            row['answer_messages'] = BUNDLE.analysis_messages(row['case_text_masked'], row['question_text'])
            row['answer_prompt_hash'] = pipeline.base.digest(pipeline.base.canonical(row['answer_messages']))
            mapping.append(row)
            tasks.append({**{name: row[name] for name in pipeline.TASK_FIELDS-{'messages', 'prompt_hash'}},
                          'messages': row['answer_messages'], 'prompt_hash': row['answer_prompt_hash']})
    return dict(pipeline.CONFIG, tasks=tasks), mapping


def score(content=None, finish='stop', refusal=None):
    return reply(evaluation_text() if content is None else content, finish=finish, refusal=refusal)


ERROR = (500, {}, json.dumps({'error': {'type': 'server_error'}}))


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)/'results'
        self.data, self.mapping = fixture()
        self.endpoint = dict(status='ready', job_id='402816', model='deepseek_v4_flash',
                             hostname='dgx-040', max_num_seqs=128)

    def runner(self, http, **kwargs):
        sampler = kwargs.pop('sampler', Sampler())
        return pipeline.Runner(pipeline.base.canonical(self.data), self.output, http, sampler,
            self.endpoint, BUNDLE, BUNDLE_HASH, self.mapping, sleep=lambda _: None, **kwargs)

    def terminal(self):
        return json.loads(next(self.output.glob('task_*/terminal.json')).read_text())

    def test_exact_messages_parameters_and_legacy_score(self):
        http = FakeHTTP([reply('完整正式回答'), score(evaluation_text([0,4,4,4,4], minor='措辞不精确'))])
        result = self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(result['success'], 1)
        self.assertEqual(result['actual_attempts'], 2)
        self.assertEqual(http.calls[0]['messages'], self.data['tasks'][0]['messages'])
        self.assertNotIn(self.mapping[0]['judge_decision_masked'], http.calls[0]['messages'][1]['content'])
        capture = pipeline.legacy.Capture({'answer': terminal['raw_evaluation'], 'thinking': 'not answer'})
        _, expected = pipeline.legacy.evaluate(BUNDLE, capture, self.mapping[0], '完整正式回答')
        self.assertEqual(terminal['evaluation'], expected)
        self.assertEqual(terminal['legacy_evaluation'], expected)
        self.assertEqual(http.calls[1]['messages'], BUNDLE.analysis_messages(capture.prompts[0], None))
        self.assertIn(self.mapping[0]['judge_decision_masked'], http.calls[1]['messages'][1]['content'])
        for payload in http.calls:
            self.assertEqual(set(payload), {'model', 'messages', 'temperature', 'max_tokens', 'chat_template_kwargs'})
            self.assertEqual(payload['temperature'], .3)
            self.assertEqual(payload['chat_template_kwargs'], {'thinking': True})
            self.assertEqual(payload['model'], 'deepseek_v4_flash')
        self.assertEqual(terminal['usage']['total_tokens'], 50)

    def test_segment_resume_never_overwrites_success(self):
        first = FakeHTTP([reply(), score()])
        self.runner(first, max_new_tasks=1).run()
        original = {p: p.read_bytes() for p in self.output.glob('task_*/*.json')}
        with self.assertRaisesRegex(ValueError, 'explicit --resume'):
            self.runner(FakeHTTP()).run()
        class RoutedHTTP(FakeHTTP):
            def __call__(self, payload):
                with self.lock:
                    self.calls.append(copy.deepcopy(payload))
                return score() if '只在评分输入提供的原裁判理由' in payload['messages'][1]['content'] else reply()
        next_http = RoutedHTTP()
        summary = self.runner(next_http, resume=True).run()
        self.assertEqual(len(next_http.calls), 18)
        self.assertEqual(summary['success'], 10)
        self.assertEqual(summary['recorded_responses'], 20)
        self.assertEqual(summary['usage_totals']['total_tokens'], 500)
        for path, value in original.items():
            self.assertEqual(path.read_bytes(), value)
        third = FakeHTTP()
        self.runner(third, resume=True).run()
        self.assertEqual(third.calls, [])

    def test_scoring_interface_failure_regenerates_whole_question(self):
        http = FakeHTTP([reply('第一轮答案'), ERROR, ERROR, ERROR, reply('第二轮答案'), score()])
        self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(terminal['pipeline_attempt'], 2)
        self.assertEqual(terminal['answer'], '第二轮答案')
        self.assertEqual(terminal['processing_error'], '')
        self.assertEqual(http.calls[0], http.calls[4])
        self.assertIn('第一轮答案', http.calls[1]['messages'][1]['content'])
        self.assertIn('第二轮答案', http.calls[5]['messages'][1]['content'])
        self.assertEqual(terminal['pipeline_attempt_history'][0]['generation']['content'], '第一轮答案')
        self.assertEqual(terminal['status'], 'ok')

    def test_token_limits_reset_per_generation_and_scoring_chat(self):
        http = FakeHTTP([reply('短', 'length'), reply('完整'), score('', 'length'), score('', 'length'), score()])
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual([p['max_tokens'] for p in http.calls], [3000,6000,3000,6000,3000])
        terminal = self.terminal()
        self.assertEqual(terminal['pipeline_attempt'], 1)
        self.assertEqual(terminal['status'], 'ok')
        self.assertEqual(len(terminal['scoring_audit']['adapter_calls']), 2)

    def test_three_whole_rounds_after_empty_generation(self):
        http = FakeHTTP([reply(''), reply(''), reply('')])
        self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(terminal['pipeline_attempt'], 3)
        self.assertEqual(terminal['status'], 'empty')
        self.assertTrue(terminal['legacy_failure_zero_imputed'])
        self.assertFalse(terminal['score_zero_imputed'])
        self.assertEqual(terminal['legacy_evaluation']['总分'], 0)
        self.assertIsNone(terminal['evaluation'])

    def test_scoring_initial_plus_three_empty_retries_then_regenerates(self):
        http = FakeHTTP([reply('第一轮'), score(''), score(''), score(''), score(''), reply('第二轮'), score()])
        self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(len(http.calls), 7)
        self.assertEqual(terminal['pipeline_attempt'], 2)
        self.assertEqual(len(terminal['pipeline_attempt_history'][0]['scoring_calls']), 4)
        self.assertEqual(terminal['answer'], '第二轮')

    def test_empty_adapter_retries_can_tolerate_an_interface_failure(self):
        http = FakeHTTP([reply(), score(''), ERROR, ERROR, ERROR, score()])
        self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(terminal['pipeline_attempt'], 1)
        self.assertEqual(len(terminal['scoring_audit']['adapter_calls']), 3)
        self.assertEqual(terminal['status'], 'ok')

    def test_format_guard_does_not_retry_or_alter_legacy(self):
        http = FakeHTTP([reply(), score('格式不完整，未分列维度')])
        self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(terminal['status'], 'parse_incomplete')
        self.assertTrue(terminal['legacy_pipeline_success'])
        self.assertIsNotNone(terminal['legacy_evaluation'])
        self.assertIsNone(terminal['evaluation'])
        self.assertFalse(terminal['legacy_failure_zero_imputed'])

    def test_format_guard_exception_does_not_retry(self):
        http = FakeHTTP([reply(), score()])
        with patch.object(pipeline.guard_module, 'complete_format', side_effect=ValueError('guard')):
            self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(self.terminal()['scoring_audit']['format_guard_error'], 'ValueError')

    def test_refused_text_scored_as_legacy_but_not_valid_success(self):
        http = FakeHTTP([reply('无法作答', refusal='policy'), score()])
        self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(terminal['status'], 'refusal')
        self.assertTrue(terminal['legacy_pipeline_success'])
        self.assertIsNotNone(terminal['legacy_evaluation'])
        self.assertIsNone(terminal['evaluation'])

    def test_truncated_answer_still_scored_but_not_complete_success(self):
        http = FakeHTTP([reply('部分1', 'length'), reply('部分2', 'length'), score()])
        self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(terminal['status'], 'truncated')
        self.assertEqual(terminal['answer'], '部分2')
        self.assertTrue(terminal['legacy_pipeline_success'])
        self.assertIsNone(terminal['evaluation'])

    def test_truncated_score_no_more_than_6000_and_preserved(self):
        http = FakeHTTP([reply(), score('原部分评分1', 'length'), score('原部分评分2', 'length')])
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual([p['max_tokens'] for p in http.calls], [3000,3000,6000])
        terminal = self.terminal()
        self.assertEqual(terminal['raw_evaluation'], '原部分评分2')
        self.assertEqual(terminal['status'], 'truncated')
        self.assertIsNotNone(terminal['legacy_evaluation'])

    def test_transport_timeout_requires_reconcile_and_no_replay(self):
        http = FakeHTTP([TimeoutError()])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(summary['status'], 'paused')
        self.assertEqual(self.terminal()['status'], 'needs_reconcile')
        new_http = FakeHTTP()
        self.runner(new_http, resume=True).run()
        self.assertEqual(new_http.calls, [])

    def test_gateway504_requires_reconcile(self):
        http = FakeHTTP([(504, {}, '{}')])
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(self.terminal()['status'], 'needs_reconcile')

    def test_persisted_intent_without_response_never_reposts(self):
        runner = self.runner(FakeHTTP(), max_new_tasks=1)
        self.output.mkdir()
        directory = runner.task_dir(self.data['tasks'][0])
        directory.mkdir()
        payload = dict(model='deepseek_v4_flash', messages=self.data['tasks'][0]['messages'],
                       temperature=.3, max_tokens=3000, chat_template_kwargs={'thinking': True})
        context = dict(stage='generation', pipeline_round=1, adapter_call=0, length_round=1, http_attempt=1)
        pipeline.base.atomic(directory/'attempt_001.request.json', {'payload': payload, 'context': context})
        result = runner.task(self.data['tasks'][0])
        self.assertEqual(result['status'], 'needs_reconcile')
        self.assertEqual(runner.underlying_transport.calls, [])

    def test_changed_persisted_intent_stops_without_next_round(self):
        runner = self.runner(FakeHTTP(), max_new_tasks=1)
        self.output.mkdir()
        directory = runner.task_dir(self.data['tasks'][0])
        directory.mkdir()
        pipeline.base.atomic(directory/'attempt_001.request.json', {'payload': {'wrong': 'payload'}, 'context': {}})
        with self.assertRaisesRegex(pipeline.EvidenceIntegrityError, 'deterministic replay'):
            runner.task(self.data['tasks'][0])
        self.assertEqual(runner.underlying_transport.calls, [])
        self.assertFalse((directory/'terminal.json').exists())
        self.assertFalse((directory/'attempt_002.request.json').exists())

    def test_file_write_failure_cannot_be_retried_as_model_failure(self):
        runner = self.runner(FakeHTTP(), max_new_tasks=1)
        self.output.mkdir()
        original = pipeline.base.atomic
        def fail_request(path, *args, **kwargs):
            if Path(path).name.endswith('.request.json'):
                raise OSError('disk full')
            return original(path, *args, **kwargs)
        with patch.object(pipeline.base, 'atomic', side_effect=fail_request):
            with self.assertRaisesRegex(OSError, 'disk full'):
                runner.task(self.data['tasks'][0])
        self.assertEqual(runner.underlying_transport.calls, [])

    def test_paused_between_answer_and_score_replays_existing_answer_only(self):
        stop = threading.Event()
        class StopAfterAnswer(FakeHTTP):
            def __call__(self, payload):
                value = super().__call__(payload)
                stop.set()
                return value
        http = StopAfterAnswer([reply('可复用已发回答')])
        summary = self.runner(http, max_new_tasks=1, stop=stop).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(summary['completed'], 0)
        self.assertEqual(len(list(self.output.glob('task_*/terminal.json'))), 0)
        resumed = FakeHTTP([score()])
        self.runner(resumed, max_new_tasks=1, resume=True).run()
        self.assertEqual(len(resumed.calls), 1)
        self.assertIn('可复用已发回答', resumed.calls[0]['messages'][1]['content'])
        self.assertEqual(self.terminal()['status'], 'ok')

    def test_worst_case_legacy_bound_is_90_and_all_receipts_preserved(self):
        generation = [ERROR, ERROR, reply('部分', 'length'), ERROR, ERROR, reply('完整')]
        empty_score = [ERROR, ERROR, score('', 'length'), ERROR, ERROR, score('')]
        http = FakeHTTP((generation+empty_score*4)*3)
        summary = self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(len(http.calls), 90)
        self.assertEqual(summary['actual_attempts'], 90)
        self.assertEqual(summary['recorded_responses'], 90)
        self.assertEqual(terminal['attempts'], 90)
        self.assertEqual(terminal['pipeline_attempt'], 3)
        self.assertEqual(terminal['status'], 'empty')
        self.assertEqual(len(list(self.output.glob('task_*/attempt_*.request.json'))), 90)

    def test_response_model_mismatch_pauses_without_new_round(self):
        http = FakeHTTP([reply(model='wrong-model')])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(self.terminal()['status'], 'protocol_error')
        self.assertEqual(summary['status'], 'paused')

    def test_mapping_tampering_and_network_judgment_fields_rejected(self):
        self.mapping[0]['judge_decision_masked'] += 'changed'
        with self.assertRaisesRegex(ValueError, 'Mapping source hash'):
            self.runner(FakeHTTP())
        self.data, self.mapping = fixture()
        self.data['tasks'][0]['judge_decision_masked'] = 'bad extra'
        with self.assertRaisesRegex(ValueError, 'only original answering'):
            self.runner(FakeHTTP())

    def test_wrong_thinking_and_resume_parameter_identity_rejected(self):
        self.data['thinking'] = False
        with self.assertRaisesRegex(ValueError, 'thinking parameter'):
            self.runner(FakeHTTP())
        self.data, self.mapping = fixture()
        self.runner(FakeHTTP([reply(), score()]), max_new_tasks=1).run()
        self.data['source_manifest_sha256'] = 'b'*64
        with self.assertRaisesRegex(ValueError, 'Resume identity mismatch'):
            self.runner(FakeHTTP(), resume=True).run()

    def test_actual_prepared_release_offline(self):
        root = REPO/'data/pkulaw_v4_thinking_pipeline_20260929'
        if not root.exists():
            self.skipTest('Prepared production input is not present in this checkout')
        raw, mapped = (root/'input_tasks.json').read_bytes(), (root/'task_mapping.jsonl').read_bytes()
        identity = pipeline.verify_release(root, raw, mapped)
        data = json.loads(raw)
        mapping = [json.loads(line) for line in mapped.splitlines() if line.strip()]
        verified = pipeline.verify_mapping(data['tasks'], mapping, BUNDLE)
        self.assertEqual(len(verified), 11095)
        self.assertEqual(identity['task_mapping_sha256'], 'ac70b5739bd2fa471441ff8c970709c15643df16030b831ed8a19d9136e969e2')
        self.assertEqual([t['split'] for t in data['tasks'][:10]], ['primary']*5+['secondary']*5)


if __name__ == '__main__':
    unittest.main()
