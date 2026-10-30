"""Offline protocol, mode, durability and legacy-score regression tests."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner as pipeline

REPO = Path(__file__).resolve().parents[3]
BUNDLE, BUNDLE_HASH = pipeline.load_bundle(REPO/'data/pkulaw_v4_selfscore_smoke_20260927')
DIMENSIONS = list(BUNDLE.AnswerEvaluator(api=object(), flag_parser_version='legacy').scoring_criteria)


def fixture():
    tasks, mapping = [], []
    for split in ('primary', 'secondary'):
        for i in range(5):
            row = dict(split=split, case_id=split+'_case', question_id=split+'_q'+str(i),
                       question_text='原问题'+str(i), case_text_masked='冻结原案情',
                       judge_decision_masked='仅评分时提供裁判理由', source_case_text_hash='a'*64)
            for raw, hashed in (('question_text', 'question_hash'), ('case_text_masked', 'masked_case_text_hash'),
                                ('judge_decision_masked', 'judge_decision_hash')):
                row[hashed] = pipeline.base.digest(row[raw].encode())
            row['answer_messages'] = BUNDLE.analysis_messages(row['case_text_masked'], row['question_text'])
            row['answer_prompt_hash'] = pipeline.base.digest(pipeline.base.canonical(row['answer_messages']))
            mapping.append(row)
            tasks.append({**{name: row[name] for name in pipeline.TASK_FIELDS-{'messages', 'prompt_hash'}},
                          'messages': row['answer_messages'], 'prompt_hash': row['answer_prompt_hash']})
    return dict(pipeline.CONFIG, tasks=tasks), mapping


def evaluation_text(values=None, minor='无'):
    values = values or [4]*5
    return '\n\n'.join('【'+dimension+'】得分：'+str(value)+'分\n理由：原评分理由。'
        for dimension, value in zip(DIMENSIONS, values)) + (
        '\n\n【错误标记】：\n- 微小错误：'+minor+'\n- 明显错误：无\n- 重大错误：无')


def reply(content='完整正式回答', finish='stop', reasoning='', model='deepseek_v4_flash'):
    return 200, {'x-request-id': 'test'}, json.dumps(dict(id='test-response', model=model,
        choices=[dict(message=dict(content=content, reasoning_content=reasoning), finish_reason=finish)],
        usage=dict(prompt_tokens=10, completion_tokens=15, total_tokens=25)))


def score(content=None, finish='stop'):
    return reply(evaluation_text() if content is None else content, finish, reasoning='评分思考')


class FakeHTTP:
    def __init__(self, sequence=()):
        self.calls, self.sequence, self.lock = [], list(sequence), threading.Lock()

    def __call__(self, payload):
        with self.lock:
            self.calls.append(copy.deepcopy(payload))
            result = self.sequence.pop(0) if self.sequence else (
                score() if payload['chat_template_kwargs']['thinking'] else reply())
        if isinstance(result, Exception):
            raise result
        return result


class Sampler:
    def __init__(self, healthy=True, running=0):
        self.healthy, self.running = healthy, running

    def __call__(self):
        return dict(healthy=self.healthy, metrics=dict(running=self.running, waiting=0,
                    kv_cache_usage=.1, preemptions_total=0))


class Tests(unittest.TestCase):
    def setUp(self):
        pipeline.configure('pkulaw_admin_ds_nt_rerun_20261004', 'ep146')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root/'ep146'/'results'
        self.data, self.mapping = fixture()
        self.identity_patch = patch.object(pipeline, 'process_identity', side_effect=lambda pid:
            dict(pid=99999999, start_ticks='123', state='R') if pid == os.getpid() else None)
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)

    def runner(self, http=None, **kwargs):
        sampler = kwargs.pop('sampler', Sampler())
        return pipeline.Runner(pipeline.base.canonical(self.data), self.output, http or FakeHTTP(), sampler,
            {}, BUNDLE, BUNDLE_HASH, self.mapping, sleep=lambda _: None, **kwargs)

    def terminal(self):
        return json.loads(next(self.output.glob('task_*/terminal.json')).read_bytes())

    def exit(self):
        return json.loads(sorted((self.output.parent/'sessions').glob('*/exit.json'))[-1].read_bytes())

    def test_exact_stage_modes_messages_and_legacy_score(self):
        http = FakeHTTP([reply('独立新回答'), score(evaluation_text([0,4,4,4,4], minor='措辞不精确'))])
        result = self.runner(http, max_new_tasks=1).run()
        row = self.terminal()
        self.assertEqual(result['success'], 1)
        self.assertEqual([p['chat_template_kwargs']['thinking'] for p in http.calls], [False, True])
        self.assertEqual(http.calls[0]['messages'], self.data['tasks'][0]['messages'])
        self.assertNotIn('仅评分时提供裁判理由', http.calls[0]['messages'][1]['content'])
        self.assertIn('仅评分时提供裁判理由', http.calls[1]['messages'][1]['content'])
        self.assertIn('独立新回答', http.calls[1]['messages'][1]['content'])
        capture = pipeline.legacy.Capture({'answer': row['raw_evaluation'], 'thinking': row['reasoning_content']})
        _, expected = pipeline.legacy.evaluate(BUNDLE, capture, self.mapping[0], row['answer'])
        self.assertEqual(row['evaluation'], expected)
        self.assertEqual(row['legacy_evaluation'], expected)
        self.assertFalse(row['thinking'])
        self.assertTrue(row['scoring_thinking'])
        self.assertEqual(row['answer_reasoning_content'], '')
        for payload in http.calls:
            self.assertEqual(set(payload), {'model','messages','temperature','max_tokens','chat_template_kwargs'})
            self.assertEqual(payload['temperature'], .3)
            self.assertNotIn('seed', payload)
        self.assertTrue(self.exit()['local_futures_finished'])
        self.assertTrue(self.exit()['drained'])

    def test_explicit_resume_preserves_all_existing_raw_files(self):
        self.runner(max_new_tasks=1).run()
        original = {p:p.read_bytes() for p in self.output.glob('task_*/*.json')}
        with self.assertRaisesRegex(ValueError, 'explicit --resume'):
            self.runner().run()
        http = FakeHTTP()
        result = self.runner(http, resume=True).run()
        self.assertEqual(result['success'], 10)
        self.assertEqual(len(http.calls), 18)
        for path, raw in original.items():
            self.assertEqual(path.read_bytes(), raw)
        last = FakeHTTP()
        self.runner(last, resume=True).run()
        self.assertEqual(last.calls, [])

    def test_scoring_failure_preserves_legacy_coupled_retry(self):
        error = (500, {}, '{}')
        http = FakeHTTP([reply('第一轮'), error,error,error, reply('第二轮'), score()])
        self.runner(http, max_new_tasks=1).run()
        row = self.terminal()
        self.assertEqual(row['pipeline_attempt'], 2)
        self.assertEqual(row['answer'], '第二轮')
        self.assertEqual(row['pipeline_attempt_history'][0]['generation']['content'], '第一轮')
        self.assertEqual(http.calls[0], http.calls[4])

    def test_single_length_upgrade_per_chat(self):
        http = FakeHTTP([reply('部分', 'length'), reply(), score('部分评分','length'), score()])
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual([p['max_tokens'] for p in http.calls], [3000,6000,3000,6000])
        self.assertEqual(self.terminal()['status'], 'ok')

    def test_truncation_at_6000_not_mislabeled_success(self):
        http = FakeHTTP([reply('部分一','length'), reply('部分二','length'), score()])
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(self.terminal()['status'], 'truncated')
        self.assertIsNone(self.terminal()['evaluation'])

    def test_original_parser_unchanged_even_negation_item(self):
        raw = evaluation_text([4,4,4,4,1], minor='无。AI回答没有错误。').replace('明显错误：无', '明显错误：无。没有逻辑断裂。')
        http = FakeHTTP([reply(), score(raw)])
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual(self.terminal()['evaluation']['总分'], 13.94)

    def test_format_failure_not_retried_or_validated_as_success(self):
        http = FakeHTTP([reply(), score('未按维度格式输出')])
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(self.terminal()['status'], 'parse_incomplete')
        self.assertIsNone(self.terminal()['evaluation'])

    def test_mode_mismatch_halts_and_preserves_receipt(self):
        http = FakeHTTP([reply(reasoning='服务错误地开启了思考')])
        result = self.runner(http, max_new_tasks=1).run()
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(self.terminal()['status'], 'protocol_error')
        self.assertIn('nonthinking_generation_returned_reasoning', result['stop_reasons'])
        self.assertEqual(len(list(self.output.glob('task_*/attempt_*.response.json'))), 1)

    def test_transport_unknown_blocks_resume_without_repost(self):
        http = FakeHTTP([TimeoutError()])
        self.runner(http, max_new_tasks=1).run()
        self.assertEqual(self.terminal()['status'], 'needs_reconcile')
        later = FakeHTTP()
        with self.assertRaisesRegex(ValueError, 'drained, reconciled exit'):
            self.runner(later, resume=True).run()
        self.assertEqual(later.calls, [])

    def test_missing_exit_blocks_resume(self):
        self.runner(max_new_tasks=1).run()
        path = next((self.output.parent/'sessions').glob('*/exit.json'))
        path.rename(path.with_name('test_removed_exit.json'))
        http = FakeHTTP()
        with self.assertRaises(FileNotFoundError):
            self.runner(http, resume=True).run()
        self.assertEqual(http.calls, [])

    def test_live_previous_pid_blocks_resume(self):
        self.runner(max_new_tasks=1).run()
        with patch.object(pipeline, 'process_identity', return_value={'pid':99999999,'start_ticks':'123','state':'S'}):
            with self.assertRaisesRegex(ValueError, 'still alive'):
                self.runner(resume=True).run()

    def test_partial_task_not_redispatched_after_clean_stop(self):
        stop = threading.Event()
        class StopAfterAnswer(FakeHTTP):
            def __call__(self, payload):
                result = super().__call__(payload)
                stop.set()
                return result
        http = StopAfterAnswer([reply('已经送出的回答')])
        self.runner(http, max_new_tasks=1, stop=stop).run()
        later = FakeHTTP()
        with self.assertRaisesRegex(ValueError, 'Incomplete prior task'):
            self.runner(later, resume=True).run()
        self.assertEqual(later.calls, [])

    def test_busy_endpoint_never_starts_model_request(self):
        http = FakeHTTP()
        self.runner(http, sampler=Sampler(running=1), max_new_tasks=1).run()
        self.assertEqual(http.calls, [])
        self.assertFalse(self.exit()['drained'])

    def test_frozen_parameters_and_judgment_tampering_rejected(self):
        self.data['thinking'] = True
        with self.assertRaisesRegex(ValueError, 'parameter mismatch'):
            self.runner()
        self.data, self.mapping = fixture()
        self.mapping[0]['judge_decision_masked'] += '修改'
        with self.assertRaisesRegex(ValueError, 'source hash mismatch'):
            self.runner()

    def test_endpoint_allowlist(self):
        with self.assertRaises(ValueError):
            pipeline.configure('pkulaw_admin_ds_nt_rerun_20261004', 'ep999')
        with self.assertRaises(ValueError):
            pipeline.Sampler('https://api.xhub.example/v1')


if __name__ == '__main__':
    unittest.main()
