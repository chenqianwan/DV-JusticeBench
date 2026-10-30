"""Offline-only regression tests: fake transport, no cluster or service access."""
import ast
import copy
import fcntl
import json
from pathlib import Path
import tempfile
import unittest

import run_three_model_scoring as score
from test_answer_batch128 import Sampler
from test_selfscore_batch128_v2 import BUNDLE, BUNDLE_HASH, FakeHTTP, evaluation_text, reply
from test_selfscore_batch128_v2 import fixture as original_fixture


def fixture():
    tasks = []
    for model in score.ANSWER_MODELS:
        for task in original_fixture()['tasks']:
            task['answering_model'] = model
            task['answer'] = '已经生成且不可重写的回答：' + model + task['question_id']
            task['answer_hash'] = score.base.digest(task['answer'].encode())
            capture = score.legacy.Capture()
            score.legacy.evaluate(BUNDLE, capture, task, task['answer'])
            task['scoring_prompt_hash'] = score.base.digest(score.base.canonical(
                BUNDLE.analysis_messages(capture.prompts[0], None)))
            tasks.append(task)
    # The first six are one unchanged question for each model and each split.
    tasks.sort(key=lambda task: (task['question_id'][-1], task['split'], task['answering_model']))
    return dict(score.CONFIG, tasks=tasks, bundle_sha256=BUNDLE_HASH, upstream_answers_sha256='d'*64)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'results'
        self.data = fixture()
        self.endpoint = dict(status='ready', job_id='402816', model='deepseek_v4_flash',
                             hostname='dgx-040', max_num_seqs=128)

    def runner(self, http, **kwargs):
        sampler = kwargs.pop('sampler', Sampler())
        sleep = kwargs.pop('sleep', lambda seconds: None)
        return score.Runner(score.base.canonical(self.data), self.output, http, sampler, self.endpoint,
                            BUNDLE, BUNDLE_HASH, sleep=sleep, **kwargs)

    def terminal(self):
        return json.loads(next(self.output.glob('task_*/terminal.json')).read_text())

    def test_model_qualified_key_avoids_all_collisions(self):
        runner = self.runner(FakeHTTP())
        self.assertEqual(len(runner.tasks_by_dir), 30)
        first_question = self.data['tasks'][:3]
        self.assertEqual(len({task['question_id'] for task in first_question}), 1)
        self.assertEqual(len({runner.task_dir(task) for task in first_question}), 3)
        self.assertEqual(score.KEY_FIELDS, ('answering_model', 'split', 'case_id', 'question_id'))

    def test_release_reads_manifest_json_filename(self):
        root = Path(self.temp.name)
        score.base.atomic(root / 'manifest.json', {}, exclusive=True)
        with self.assertRaisesRegex(ValueError, 'Unexpected three-model release identity/coverage'):
            score.verify_release(root, score.base.canonical(self.data), BUNDLE)

    def test_six_cross_split_model_pilot_then_explicit_resume(self):
        http = FakeHTTP()
        summary = self.runner(http, max_new_tasks=6).run()
        self.assertEqual(summary['status'], 'segment_completed')
        self.assertEqual(summary['success'], 6)
        self.assertEqual(len(http.calls), 6)
        self.assertFalse(summary['self_scoring'])
        self.assertFalse(summary['answers_regenerated'])
        self.assertEqual(summary['reused_existing_scores'], 0)
        self.assertEqual(summary['success_by_model_split'],
                         {model: {'primary': 1, 'secondary': 1} for model in score.ANSWER_MODELS})
        originals = {path: path.read_bytes() for path in self.output.glob('task_*/terminal.json')}
        with self.assertRaisesRegex(ValueError, 'explicit --resume'):
            self.runner(FakeHTTP()).run()
        resumed = FakeHTTP()
        summary = self.runner(resumed, resume=True).run()
        self.assertEqual(summary['status'], 'completed')
        self.assertEqual(len(resumed.calls), 24)
        self.assertEqual(summary['actual_attempts'], 30)
        self.assertEqual(summary['success'], 30)
        for path, raw in originals.items():
            self.assertEqual(path.read_bytes(), raw)
        for model in score.ANSWER_MODELS:
            for split in ('primary', 'secondary'):
                rows = [json.loads(line) for line in
                        (self.output / ('scores_' + model + '_' + split + '.jsonl')).read_text().splitlines()]
                self.assertEqual(len(rows), 5)
                self.assertTrue(all(row['answering_model'] == model and row['split'] == split for row in rows))

    def test_scientific_task_ast_is_identical_except_self_scoring_label(self):
        def task_node(path):
            tree = ast.parse(Path(path).read_text())
            runner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Runner')
            return next(node for node in runner.body if isinstance(node, ast.FunctionDef) and node.name == 'task')
        current = task_node(score.__file__)
        changed = 0
        for node in ast.walk(current):
            if isinstance(node, ast.keyword) and node.arg == 'self_scoring':
                self.assertIs(node.value.value, False)
                node.value.value = True
                changed += 1
        self.assertEqual(changed, 1)
        self.assertEqual(ast.dump(current), ast.dump(task_node(score.guard_module.__file__)))
        self.assertIs(score.complete_format, score.guard_module.complete_format)
        self.assertIs(score.messages_for, score.guard_module.messages_for)
        self.assertEqual(BUNDLE_HASH, '6af07139a06994b3192e450e5fa79f23e69851b889e999a99187efaabc81d9ad')

    def test_original_prompts_payload_threshold_penalty_and_parser(self):
        content = evaluation_text([0, 4, 4, 4, 4], minor='引用文字有轻微不精确')
        http = FakeHTTP([reply(content)])
        self.runner(http, max_new_tasks=1).run()
        task = self.data['tasks'][0]
        inner, messages = score.messages_for(BUNDLE, task)
        self.assertEqual(http.calls[0], dict(model='deepseek_v4_flash', messages=messages,
                        temperature=.3, max_tokens=3000, chat_template_kwargs={'thinking': True}))
        self.assertIn(task['answer'], inner)
        replay = score.legacy.Capture({'answer': content, 'thinking': '原推理内容'}, expected=inner)
        _, expected = score.legacy.evaluate(BUNDLE, replay, task, task['answer'])
        terminal = self.terminal()
        self.assertEqual(terminal['evaluation'], expected)
        self.assertEqual(terminal['legacy_evaluation'], expected)
        self.assertEqual(expected['总分'], 9.7)
        self.assertFalse(terminal['self_scoring'])
        self.assertFalse(terminal['answers_regenerated'])

    def test_truncation_extends_once_and_never_becomes_valid_zero(self):
        http = FakeHTTP(always=reply(evaluation_text(), 'length'))
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual([call['max_tokens'] for call in http.calls], [3000, 6000])
        terminal = self.terminal()
        self.assertEqual(terminal['status'], 'truncated')
        self.assertIsNone(terminal['evaluation'])
        self.assertFalse(terminal['score_zero_imputed'])

    def test_empty_and_interface_retries_remain_bounded(self):
        http = FakeHTTP(always=reply(''))
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(self.terminal()['status'], 'empty')

    def test_http_failures_use_original_nine_attempt_bound(self):
        http = FakeHTTP(always=(500, {}, '{"error":{"type":"server_error"}}'))
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 9)
        self.assertTrue(all(call['max_tokens'] == 3000 for call in http.calls))
        self.assertEqual(self.terminal()['status'], 'interface_error')

    def test_429_and_503_backoff_preserves_original_contract(self):
        http = FakeHTTP([(429, {'retry-after': '7'}, '{"error":{}}'),
                         (503, {}, '{"error":{}}'), reply()])
        delays = []
        summary = self.runner(http, max_new_tasks=1, sleep=delays.append).run()
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(delays, [7.0, 10])
        self.assertEqual(summary['success'], 1)

    def test_gateway_timeout_requires_reconciliation_without_retry(self):
        http = FakeHTTP([(504, {}, '{"error":{"type":"gateway_timeout"}}')])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(summary['needs_reconcile'], 1)
        self.assertEqual(summary['status'], 'paused')

    def test_refusal_and_wrong_response_model_are_not_valid_scores(self):
        http = FakeHTTP([reply('', refusal='refused')])
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(self.terminal()['status'], 'refusal')
        self.assertIsNone(self.terminal()['evaluation'])

    def test_wrong_response_model_halts_before_further_dispatch(self):
        http = FakeHTTP([reply(model='wrong-model')])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertIn('response_model_mismatch', summary['stop_reasons'])
        self.assertIsNone(self.terminal()['evaluation'])

    def test_pre_dispatch_service_monitor_keeps_safety_guards(self):
        http = FakeHTTP()
        summary = self.runner(http, sampler=Sampler(kv=.9)).run()
        self.assertEqual(http.calls, [])
        self.assertEqual(summary['status'], 'paused')
        self.assertIn('kv_cache_at_or_above_85_percent', summary['stop_reasons'])

    def test_format_guard_retains_legacy_only_and_does_not_retry(self):
        http = FakeHTTP([reply('【规范依据相关性】得分：4分')])
        self.runner(http, max_new_tasks=1).run()
        terminal = self.terminal()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(terminal['status'], 'parse_incomplete')
        self.assertIsNotNone(terminal['legacy_evaluation'])
        self.assertIsNone(terminal['evaluation'])

    def test_uncertain_request_halts_and_explicit_resume_cannot_skip_it(self):
        http = FakeHTTP([TimeoutError('never expose secrets')])
        summary = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(summary['needs_reconcile'], 1)
        resumed = FakeHTTP()
        summary = self.runner(resumed, resume=True).run()
        self.assertEqual(resumed.calls, [])
        self.assertEqual(summary['status'], 'paused')

    def test_unknown_intent_cannot_be_redispatched(self):
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

    def test_resume_rejects_changed_input_and_foreign_terminal(self):
        self.runner(FakeHTTP(), max_new_tasks=1).run()
        self.data['upstream_answers_sha256'] = 'e'*64
        with self.assertRaisesRegex(ValueError, 'Resume identity mismatch'):
            self.runner(FakeHTTP(), resume=True).run()
        self.data['upstream_answers_sha256'] = 'd'*64
        path = next(self.output.glob('task_*/terminal.json'))
        row = json.loads(path.read_text())
        row['answering_model'] = 'qwen-max' if row['answering_model'] != 'qwen-max' else 'gpt-4o'
        score.base.atomic(path, row)
        with self.assertRaisesRegex(ValueError, 'provenance'):
            self.runner(FakeHTTP(), resume=True).run()

    def test_nonblocking_process_lock(self):
        self.output.mkdir()
        with (self.output / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.runner(FakeHTTP()).run()

    def test_validation_accepts_partial_cases_rejects_duplicates_and_tampering(self):
        partial = copy.deepcopy(self.data)
        partial['tasks'] = partial['tasks'][:6]
        score.validate(partial)
        bad = copy.deepcopy(partial)
        bad['tasks'].append(copy.deepcopy(bad['tasks'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate model-qualified'):
            score.validate(bad)
        bad = copy.deepcopy(partial)
        bad['tasks'][0]['answer'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'answer'):
            score.validate(bad)
        bad = copy.deepcopy(partial)
        bad['tasks'][0]['answering_model'] = 'deepseek_v4_flash'
        with self.assertRaisesRegex(ValueError, 'Unexpected answering model'):
            score.validate(bad)
        bad = copy.deepcopy(partial)
        bad['thinking'] = False
        with self.assertRaisesRegex(ValueError, 'Frozen scoring parameter'):
            score.validate(bad)


if __name__ == '__main__':
    unittest.main()
