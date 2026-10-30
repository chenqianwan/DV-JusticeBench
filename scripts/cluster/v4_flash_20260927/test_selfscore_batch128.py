import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest

import run_selfscore_batch128 as score
from test_answer_batch128 import Sampler


REPO = Path(__file__).resolve().parents[3]
BUNDLE, BUNDLE_HASH = score.load_bundle(REPO / 'data/pkulaw_v4_selfscore_smoke_20260927')
DIMENSIONS = list(BUNDLE.AnswerEvaluator(api=object(), flag_parser_version='legacy').scoring_criteria)


def evaluation_text(values=None, minor='无'):
    values = values or [4] * 5
    return '\n\n'.join('【' + dimension + '】得分：' + str(value) + '分\n理由：原评分理由。'
                       for dimension, value in zip(DIMENSIONS, values)) + (
                       '\n\n【错误标记】：\n- 微小错误：' + minor + '\n- 明显错误：无\n- 重大错误：无')


def fixture():
    tasks = []
    for split in ('primary', 'secondary'):
        for i in range(5):
            task = dict(split=split, case_id=split + '_case', question_id=split + '_q' + str(i),
                        question_text='原问题' + str(i), case_text_masked='原脱敏案情', judge_decision_masked='原脱敏裁判理由',
                        answer='已完成的原回答', answer_status='ok', answering_model='deepseek_v4_flash',
                        scoring_model='deepseek_v4_flash', source_case_text_hash='a' * 64,
                        answer_prompt_hash='b' * 64, answer_terminal_sha256='c' * 64)
            for raw, hashed in (('question_text', 'question_hash'), ('case_text_masked', 'masked_case_text_hash'),
                                ('judge_decision_masked', 'judge_decision_hash'), ('answer', 'answer_hash')):
                task[hashed] = score.base.digest(task[raw].encode())
            capture = score.legacy.Capture()
            score.legacy.evaluate(BUNDLE, capture, task, task['answer'])
            task['scoring_prompt_hash'] = score.base.digest(score.base.canonical(BUNDLE.analysis_messages(capture.prompts[0], None)))
            tasks.append(task)
    return dict(score.CONFIG, tasks=tasks, bundle_sha256=BUNDLE_HASH, upstream_answers_sha256='d' * 64)


def reply(content=None, finish='stop', refusal=None, model='deepseek_v4_flash'):
    return 200, {'x-request-id': 'http-id'}, json.dumps(dict(id='response-id', model=model,
        choices=[dict(message=dict(content=evaluation_text() if content is None else content,
                                   refusal=refusal, reasoning_content='原推理内容'), finish_reason=finish)],
        usage=dict(prompt_tokens=100, completion_tokens=200, total_tokens=300,
                   completion_tokens_details={'reasoning_tokens': 50})))


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


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'results'
        self.data = fixture()
        self.endpoint = dict(status='ready', job_id='402816', model='deepseek_v4_flash', hostname='dgx-040', max_num_seqs=128)

    def runner(self, http, **kwargs):
        return score.Runner(score.base.canonical(self.data), self.output, http, Sampler(), self.endpoint,
                            BUNDLE, BUNDLE_HASH, sleep=lambda seconds: None, **kwargs)

    def terminal(self):
        return json.loads(next(self.output.glob('task_*/terminal.json')).read_text())

    def test_two_formal_scores_resume_no_duplicate_and_legacy_identity(self):
        http = FakeHTTP()
        summary = self.runner(http, max_new_tasks=2).run()
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(summary['success'], 2)
        self.assertEqual(summary['status'], 'segment_completed')
        before = {path: path.read_bytes() for path in self.output.glob('task_*/terminal.json')}
        expected_prompts = {task['scoring_prompt_hash']: score.messages_for(BUNDLE, task)
                            for task in self.data['tasks'][:2]}
        for payload in http.calls:
            inner, messages = expected_prompts[score.base.digest(score.base.canonical(payload['messages']))]
            self.assertEqual(payload, dict(model='deepseek_v4_flash', messages=messages, temperature=.3,
                                           max_tokens=3000, chat_template_kwargs={'thinking': True}))
            self.assertIn(inner, payload['messages'][1]['content'])
        with self.assertRaisesRegex(ValueError, 'explicit --resume'):
            self.runner(FakeHTTP()).run()
        resumed = FakeHTTP()
        summary = self.runner(resumed, resume=True).run()
        self.assertEqual(len(resumed.calls), 8)
        self.assertEqual(summary['success'], 10)
        self.assertEqual(summary['actual_attempts'], 10)
        self.assertEqual(summary['usage_totals']['total_tokens'], 3000)
        self.assertEqual(summary['usage_totals']['reasoning_tokens'], 500)
        self.assertTrue(summary['self_scoring'])
        self.assertFalse(summary['answers_regenerated'])
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        terminal = self.terminal()
        self.assertEqual(terminal['evaluation']['总分'], 20)
        self.assertEqual(terminal['evaluation']['评价Thinking'], '原推理内容')
        self.assertEqual(terminal['evaluation'], terminal['legacy_evaluation'])

    def test_original_threshold_and_penalty_rules_unchanged(self):
        content = evaluation_text([0, 4, 4, 4, 4], minor='引用文字有轻微不精确')
        self.runner(FakeHTTP([reply(content)]), max_new_tasks=1).run()
        task = self.data['tasks'][0]
        capture = score.legacy.Capture({'answer': content, 'thinking': '原推理内容'})
        _, expected = score.legacy.evaluate(BUNDLE, capture, task, task['answer'])
        terminal = self.terminal()
        self.assertEqual(terminal['status'], 'ok')
        self.assertEqual(terminal['evaluation'], expected)
        self.assertLess(terminal['evaluation']['总分'], 10)

    def test_truncation_once_then_complete_counts_all_usage(self):
        http = FakeHTTP([reply('部分评价', 'length'), reply()])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual([call['max_tokens'] for call in http.calls], [3000, 6000])
        self.assertEqual(summary['success'], 1)
        self.assertEqual(summary['length_retries'], 1)
        terminal = self.terminal()
        self.assertEqual(terminal['usage']['total_tokens'], 600)
        self.assertEqual(terminal['final_response_usage']['total_tokens'], 300)
        self.assertEqual(terminal['usage']['reasoning_tokens'], 100)

    def test_two_truncations_never_become_zero_or_valid_score(self):
        http = FakeHTTP(always=reply(evaluation_text(), 'length'))
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(summary['counts']['truncated'], 1)
        terminal = self.terminal()
        self.assertIsNone(terminal['evaluation'])
        self.assertIsNone(terminal['legacy_evaluation'])
        self.assertFalse(terminal['score_zero_imputed'])

    def test_missing_dimensions_retains_raw_legacy_but_not_valid_score(self):
        self.runner(FakeHTTP([reply('【规范依据相关性】得分：4分')]), max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(terminal['status'], 'parse_incomplete')
        self.assertIsNone(terminal['evaluation'])
        self.assertIsNotNone(terminal['legacy_evaluation'])
        self.assertEqual(len(terminal['missing_or_invalid_dimensions']), 4)

    def test_refusal_stops_without_retry(self):
        http = FakeHTTP([reply('', refusal='refused')])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(summary['counts']['refusal'], 1)
        self.assertIsNone(self.terminal()['evaluation'])

    def test_timeout_and_existing_unknown_intent_do_not_retry(self):
        http = FakeHTTP([TimeoutError('secret')])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(summary['needs_reconcile'], 1)
        self.assertEqual(summary['status'], 'paused')
        resumed = FakeHTTP()
        self.runner(resumed, resume=True, max_new_tasks=1).run()
        self.assertEqual(len(resumed.calls), 1)
        self.assertNotEqual(http.calls[0]['messages'], resumed.calls[0]['messages'])

    def test_wrong_model_and_prompt_tamper_rejected(self):
        http = FakeHTTP([reply(model='other-model')])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(summary['counts']['interface_error'], 1)
        self.assertIsNone(self.terminal()['evaluation'])
        task = copy.deepcopy(self.data['tasks'][0])
        task['scoring_prompt_hash'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'pre-frozen legacy prompt'):
            score.messages_for(BUNDLE, task)

    def test_missing_response_receipt_is_never_redispatched(self):
        runner = self.runner(FakeHTTP())
        self.output.mkdir()
        score.base.atomic(self.output / 'run_identity.json', runner.identity, exclusive=True)
        task = self.data['tasks'][0]
        directory = runner.task_dir(task)
        directory.mkdir()
        _, messages = score.messages_for(BUNDLE, task)
        payload = dict(model='deepseek_v4_flash', messages=messages, temperature=.3,
                       max_tokens=3000, chat_template_kwargs={'thinking': True})
        score.base.atomic(directory / 'attempt_001.request.json', {'payload': payload}, exclusive=True)
        http = FakeHTTP()
        summary = self.runner(http, resume=True, max_new_tasks=1).run()
        self.assertEqual(http.calls, [])
        self.assertEqual(summary['needs_reconcile'], 1)
        self.assertEqual(summary['unmatched_intents'], 1)

    def test_input_parameters_and_source_answer_hash(self):
        score.validate(self.data)
        changed = copy.deepcopy(self.data)
        changed['thinking'] = False
        with self.assertRaises(ValueError):
            score.validate(changed)
        changed = copy.deepcopy(self.data)
        changed['tasks'][0]['answer'] = 'not the frozen answer'
        with self.assertRaisesRegex(ValueError, 'answer'):
            score.validate(changed)


if __name__ == '__main__':
    unittest.main()
