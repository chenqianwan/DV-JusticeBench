"""Offline regression checks. No SSH, Slurm, health, or model requests."""
import ast
from collections import Counter
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import dispatch_thinking_pipeline as d
import prepare_thinking_pipeline as p


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PreparedInputTests(unittest.TestCase):
    def test_actual_frozen_input(self):
        root = p.OUT
        manifest = json.loads((root / 'input_manifest.json').read_text())
        inputs = json.loads((root / 'input_tasks.json').read_text())
        mapping = (root / 'task_mapping.jsonl').read_bytes()
        self.assertEqual(sha(mapping), p.MAPPING_SHA)
        self.assertEqual(mapping, (p.SOURCE / 'task_mapping.jsonl').read_bytes())
        records = {tuple(r[k] for k in ('split', 'case_id', 'question_id')): r
                   for r in map(json.loads, mapping.splitlines())}
        excluded = {r['case_id'] for r in json.loads((root / 'excluded_cases.json').read_text())}
        self.assertEqual(len(excluded), 18)
        self.assertEqual(sha((root / 'legacy_scoring_bundle.py').read_bytes()), p.BUNDLE_SHA)
        self.assertEqual(manifest['retry_contract'], 'legacy108_coupled')
        self.assertEqual(manifest['reused_answers'], 0)
        for name, meta in manifest['files'].items():
            data = (root / name).read_bytes()
            self.assertEqual((len(data), sha(data)), (meta['bytes'], meta['sha256']))
        self.assertEqual(len(inputs['tasks']), 11095)
        keys, groups = set(), Counter()
        for task in inputs['tasks']:
            key = tuple(task[k] for k in ('split', 'case_id', 'question_id'))
            self.assertNotIn(key, keys)
            keys.add(key)
            groups[key[:2]] += 1
            source = records[key]
            self.assertEqual(set(task), p.TASK_FIELDS)
            self.assertEqual(task['messages'], source['answer_messages'])
            self.assertEqual(sha(p.canonical(task['messages'])), task['prompt_hash'])
            self.assertNotIn(source['judge_decision_masked'], task['messages'][1]['content'])
            self.assertNotIn(task['case_id'], excluded)
        self.assertEqual(set(groups.values()), {5})
        self.assertEqual(Counter(s for s, _ in groups), {'primary': 215, 'secondary': 2004})
        first = inputs['tasks'][:10]
        self.assertEqual(Counter(t['split'] for t in first), {'primary': 5, 'secondary': 5})
        self.assertEqual(len({(t['split'], t['case_id']) for t in first}), 2)


class DispatchTests(unittest.TestCase):
    def test_stage_file_set_and_hash_checks(self):
        tree = ast.parse(d.STAGE)
        names_node = next(n.value for n in tree.body[0].body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'names' for t in n.targets))
        expected = set(d.DATA_FILES) | set(d.OLD_SCRIPTS) | {'run_thinking_pipeline.py', 'answer_dispatch_wrapper.py'}
        self.assertEqual(ast.literal_eval(names_node), expected)
        self.assertEqual(len(expected), 12)
        self.assertIn("manifest['input_tasks_sha256']==files['input_tasks.json']['sha256']", d.STAGE)
        self.assertIn("manifest['task_mapping_sha256']==files['task_mapping.jsonl']['sha256']", d.STAGE)
        self.assertIn("manifest['files'][name]['sha256']==files[name]['sha256']", d.STAGE)
        self.assertIn("manifest['files'][name]['bytes']==files[name]['size']", d.STAGE)
        self.assertIn("write_once(R/'stage_manifest.json',encoded(audit));checked_sources()", d.STAGE)

    def test_compressed_transport_rejects_hash_and_size_corruption(self):
        original = b'Frozen test bytes\n'
        packed = d.original.pack(original)
        self.assertEqual(d.original.unpack(packed), original)
        for field, value in [('size', len(original) + 1), ('gzip_size', 1),
                             ('sha256', '0' * 64), ('gzip_sha256', '0' * 64)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                d.original.unpack(dict(packed, **{field: value}))

    def test_generated_scripts_compile_and_are_isolated(self):
        tree = ast.parse(d.REMOTE)
        ast.parse(d.WRAPPER)
        self.assertIn("R/'run_thinking_pipeline.py'", d.WRAPPER)
        self.assertIn("'--max-new-tasks',str(intent['max_new_tasks'])", d.WRAPPER)
        self.assertNotIn("R/'run_answer_batch128.py'", d.WRAPPER)
        node = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'NODE' for t in n.targets))
        ast.parse(node)
        for old in ['dvjustice_answer_batch128_20260928', 'dvjustice_selfscore_batch128_20260928',
                    'dvjustice_selfscore_batch128_v2_20260928']:
            self.assertIn(old, node)
        self.assertNotIn('scancel', d.REMOTE)
        self.assertNotIn('sbatch', d.REMOTE)
        self.assertIn("'--jobid=402816'", d.REMOTE)

    def call_cli(self, args):
        with tempfile.TemporaryDirectory(prefix='thinking_cli_audit_') as directory:
            result = subprocess.CompletedProcess([], 0, '{}', '')
            with patch.object(d, 'LOCAL', Path(directory)), patch.object(sys, 'argv', ['dispatch', *args]), \
                 patch.object(d.subprocess, 'run', return_value=result) as run, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    d.main()
                self.assertEqual(raised.exception.code, 0)
                self.assertEqual(run.call_count, 1)
                return json.loads(run.call_args.kwargs['input'])

    def test_start_is_ten_only(self):
        self.assertEqual(self.call_cli(['start']), {'mode': 'start', 'max_new_tasks': 10})

    def test_resume_defaults_to_ten(self):
        self.assertEqual(self.call_cli(['resume']), {'mode': 'resume', 'max_new_tasks': 10})

    def test_full_requires_explicit_resume(self):
        self.assertEqual(self.call_cli(['resume', '--all-remaining']), {'mode': 'resume', 'max_new_tasks': None})
        with patch.object(sys, 'argv', ['dispatch', 'start', '--all-remaining']), \
             patch.object(d.subprocess, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                d.main()
            self.assertEqual(raised.exception.code, 2)
            run.assert_not_called()

    def run_remote_start_branch(self, mode, limit, info=None):
        root_if = next(n for n in ast.parse(d.REMOTE).body if isinstance(n, ast.If)
                       and ast.unparse(n.test) == "mode == 'stage'")
        branch = root_if.orelse[0]
        code = compile(ast.Module(body=branch.body, type_ignores=[]), '<offline-dispatch-branch>', 'exec')
        with tempfile.TemporaryDirectory(prefix='thinking_remote_audit_') as directory:
            root = Path(directory)
            (root / 'results').mkdir()
            if mode == 'resume':
                (root / 'results' / 'run_identity.json').touch()
            (root / 'results' / 'summary.json').touch()
            import fcntl
            namespace = dict(mode=mode, r={'mode': mode, 'max_new_tasks': limit}, R=root,
                             os=os, fcntl=fcntl, checked_sources=lambda: None,
                             job=lambda: {'EndTime': '2026-10-02T21:58:29'}, safe=lambda x: x,
                             latest=lambda: root / 'previous' if info is not None else None,
                             dispatch_info=lambda _: info, now=lambda: 'offline',
                             encoded=lambda x: json.dumps(x).encode(), identity=lambda pid: {'pid': pid, 'started': '1'},
                             read=lambda x: {'pending': 5} if x.name == 'summary.json' else
                             {'files': {'input_tasks.json': {'sha256': 'frozen'}}},
                             node=lambda action, identities: {'service_attempt': 'existing'},
                             write_once=lambda path, b: path.write_bytes(b),
                             B='NO_REMOTE_COMMANDS/', E={}, json=json, sys=sys,
                             subprocess=types.SimpleNamespace(Popen=lambda *a, **k: types.SimpleNamespace(pid=123),
                                                              DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT))
            with contextlib.redirect_stdout(io.StringIO()):
                exec(code, namespace)
            return [json.loads(f.read_text()) for f in (root / 'dispatches').glob('*.intent.json')]

    def test_remote_guard_rejects_full_or_partial_start(self):
        for limit in [None, 1, 11, 0, True]:
            with self.subTest(limit=limit), self.assertRaises(AssertionError):
                self.run_remote_start_branch('start', limit)

    def test_remote_start_records_pilot_limit(self):
        result = self.run_remote_start_branch('start', 10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['max_new_tasks'], 10)
        self.assertFalse(result[0]['new_gpu_allocation'])
        self.assertTrue(result[0]['scoring_enabled'])

    def test_resume_requires_previous_exit(self):
        base = {'intent': {'dispatch_id': 'attempt-001'}, 'node_identities': {}}
        for changed in [{}, {'exit': {'exit_code': None}}, {'launcher_alive': True, 'exit': {'exit_code': 0}}]:
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                self.run_remote_start_branch('resume', None, dict(base, **changed))
        result = self.run_remote_start_branch('resume', None, dict(base, exit={'exit_code': 0}))
        self.assertEqual(result[0]['dispatch_id'], 'attempt-002')
        self.assertIsNone(result[0]['max_new_tasks'])


if __name__ == '__main__':
    unittest.main()
