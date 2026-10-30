import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest

import run_selfscore_batch128_v2 as score
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


    def test_real_pilot_outputs_regression_and_legacy_scores_unchanged(self):
        pilot = REPO / 'data/pkulaw_v4_selfscore_batch128_20260928/fetched/20260928T150612404103/results'
        terminals = {json.loads(p.read_text())['split']: json.loads(p.read_text())
                     for p in pilot.glob('task_*/terminal.json')}
        self.assertEqual(set(terminals), {'primary', 'secondary'})
        primary = terminals['primary']
        self.assertTrue(score.legacy.complete_format(primary['raw_evaluation'], DIMENSIONS))
        for split, original in terminals.items():
            self.assertEqual(score.complete_format(original['raw_evaluation'], DIMENSIONS), [])
        http = FakeHTTP([reply(primary['raw_evaluation'])])
        summary = self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(terminal['status'], 'ok')
        self.assertEqual(terminal['evaluation']['各维度得分'], primary['legacy_evaluation']['各维度得分'])
        self.assertEqual(terminal['evaluation']['总分'], 15)
        self.assertEqual(summary['reused_existing_scores'], 2)
        self.assertEqual(summary['combined_expected_scores'], 12)
        self.assertEqual(summary['combined_success'], 3)
        # Resume must validate this formerly rejected prose-prefixed format,
        # preserving its already-finished immutable terminal without another POST.
        before = {p: p.read_bytes() for p in self.output.glob('task_*/terminal.json')}
        resumed = FakeHTTP()
        self.runner(resumed, resume=True, max_new_tasks=1).run()
        self.assertEqual(len(resumed.calls), 1)
        self.assertNotEqual(resumed.calls[0]['messages'], http.calls[0]['messages'])
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_guard_rejects_missing_duplicate_conflicting_or_prose_only_dimensions(self):
        dimension = DIMENSIONS[0]
        full = evaluation_text()
        self.assertEqual(score.complete_format('前言提及' + '和'.join(DIMENSIONS) + '\n' + full, DIMENSIONS), [])
        self.assertEqual(score.complete_format('。'.join(name + '得分：4分' for name in DIMENSIONS), DIMENSIONS), DIMENSIONS)
        for duplicate in ('【' + dimension + '】得分：4分', '【' + dimension + '】得分：1分'):
            self.assertIn(dimension, score.complete_format(full + '\n' + duplicate, DIMENSIONS))
        missing = full.replace('【' + dimension + '】得分：4分', '【' + dimension + '】理由：未给分')
        self.assertEqual(score.complete_format(missing, DIMENSIONS), [dimension])
        # A later explanatory number may not fill a missing score.
        missing = full.replace('【' + dimension + '】得分：4分', '【' + dimension + '】\n理由：理论满分4分')
        self.assertEqual(score.complete_format(missing, DIMENSIONS), [dimension])

    def test_guard_requires_explicit_integer_zero_through_four(self):
        for value in ('-1', '5', '14', '3.5', '4.0', '3/4', '3-4', '+3', '3或4', '四', '**1**'):
            text = evaluation_text().replace('得分：4分', '得分：' + value + '分', 1)
            self.assertEqual(score.complete_format(text, DIMENSIONS), [DIMENSIONS[0]], value)
        for value in range(5):
            self.assertEqual(score.complete_format(evaluation_text([value] * 5), DIMENSIONS), [])
        markdown = evaluation_text().replace('【', '### **【').replace('】得分', '】**得分')
        self.assertEqual(score.complete_format(markdown, DIMENSIONS), [])
        # Legacy parsing gives priority to 得分 and can cross into the next
        # dimension if this one contains only a bare score; fail closed.
        bare = evaluation_text().replace('得分：4分', '1分', 1)
        self.assertEqual(score.complete_format(bare, DIMENSIONS), [DIMENSIONS[0]])
        inline = '前言引用【' + DIMENSIONS[0] + '】得分：1分\n' + evaluation_text()
        self.assertEqual(score.complete_format(inline, DIMENSIONS), [DIMENSIONS[0]])

    def test_allow_two_four_question_cases_but_not_three_question_case(self):
        self.data['tasks'] = [t for t in self.data['tasks'] if not t['question_id'].endswith('q0')]
        score.validate(self.data)
        self.data['tasks'].pop()
        with self.assertRaisesRegex(ValueError, 'four-question'):
            score.validate(self.data)

    def test_frozen_v1_guard_and_bundle_remain_unchanged(self):
        self.assertEqual(score.base.digest((Path(__file__).parent / 'run_selfscore_batch128.py').read_bytes()),
                         '4d93a5ce1b7051695484f0f5f2a9eab4d298a4e56b148e3d90839bc36c5790ef')
        self.assertEqual(BUNDLE_HASH, score.BUNDLE_SHA256)
        self.assertEqual(score.CONFIG['thinking'], True)
        self.assertEqual(score.CONFIG['temperature'], .3)
        self.assertEqual(score.CONFIG['initial_max_tokens'], 3000)

    def test_exact_production_network_and_reused_coverage_without_api(self):
        root = Path(self.temp.name) / 'release'
        root.mkdir()
        full = []
        for split, cases, offset in (('primary', 215, 0), ('secondary', 2004, 5)):
            for index in range(cases):
                for question in range(5):
                    task = dict(self.data['tasks'][offset + question])
                    task['case_id'] = split + '_case_' + str(index)
                    task['question_id'] = task['case_id'] + '_q' + str(question)
                    full.append(task)
        reused_tasks = [full[0], full[1075]]
        reused_keys = {task['question_id'] for task in reused_tasks}
        data = dict(self.data, tasks=[task for task in full if task['question_id'] not in reused_keys])
        input_bytes = score.base.canonical(data)
        scores = []
        for task in reused_tasks:
            inner, _ = score.messages_for(BUNDLE, task)
            replay = score.legacy.Capture({'answer': evaluation_text(), 'thinking': '原推理内容'}, expected=inner)
            _, evaluation = score.legacy.evaluate(BUNDLE, replay, task, task['answer'])
            result = {k: v for k, v in task.items() if k not in score.OMIT_TERMINAL}
            result.update(status='ok', finish_reason='stop', response_model=score.CONFIG['model'],
                          requested_model=score.CONFIG['model'], temperature=.3, thinking=True,
                          reused=True, score_zero_imputed=False, raw_evaluation=evaluation_text(),
                          reasoning_content='原推理内容', legacy_evaluation=evaluation, evaluation=evaluation)
            scores.append(result)
        reused_bytes = score.base.canonical(reused_tasks)
        scores_bytes = b'\n'.join(score.base.canonical(row) for row in scores) + b'\n'
        manifest = {'files': {name: {'sha256': score.base.digest(raw)} for name, raw in (
                    ('input_scoring.json', input_bytes), ('reused_tasks.json', reused_bytes),
                    ('reused_scores.jsonl', scores_bytes))}, 'excluded_case_ids': ['dirty_case']}
        score.base.atomic(root / 'input_manifest.json', manifest)
        score.base.atomic(root / 'reused_tasks.json', reused_bytes.decode(), raw=True)
        score.base.atomic(root / 'reused_scores.jsonl', scores_bytes.decode(), raw=True)
        identity = score.verify_release(root, input_bytes, BUNDLE)
        self.assertEqual(identity['count'], 2)
        # Manifest-bound score tampering must be caught even if its checksum is updated.
        scores[0]['evaluation'] = {'总分': 0}
        corrupt = b'\n'.join(score.base.canonical(row) for row in scores) + b'\n'
        manifest['files']['reused_scores.jsonl']['sha256'] = score.base.digest(corrupt)
        score.base.atomic(root / 'input_manifest.json', manifest)
        score.base.atomic(root / 'reused_scores.jsonl', corrupt.decode(), raw=True)
        with self.assertRaisesRegex(ValueError, 'original-legacy replay'):
            score.verify_release(root, input_bytes, BUNDLE)


if __name__ == '__main__':
    unittest.main()
