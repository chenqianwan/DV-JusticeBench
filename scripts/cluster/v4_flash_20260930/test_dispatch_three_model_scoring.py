"""Offline tests only: no SSH, API requests, or runner launch."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import pathlib
import re
import stat
import tempfile
import types
import unittest
import uuid
from unittest.mock import patch
import zlib

import dispatch_three_model_scoring as D


class DispatchTests(unittest.TestCase):
    def test_all_generated_layers_compile(self):
        for label, source in [('wrapper', D.WRAPPER), ('remote', D.REMOTE),
                              ('node', D.NODE), ('streaming', D.STAGE_HELPERS)]:
            compile(source, label, 'exec')

    def test_frozen_file_allowlist(self):
        self.assertEqual(set(D.DATA_FILES), {'input_scoring.json', 'manifest.json',
            'legacy_scoring_bundle.py', 'excluded_cases.json', 'answer_provenance.jsonl',
            'excluded_answers.jsonl', 'source_release_audit.json'})
        self.assertEqual(set(D.SCRIPT_FILES), {'run_three_model_scoring.py',
            'run_selfscore_batch128_v2.py', 'run_selfscore_smoke.py', 'run_answer_batch128.py',
            'run_answer_smoke.py', 'benchmark_concurrency.py'})
        self.assertEqual(D.MAX_FILE_BYTES, 1024 ** 3)
        self.assertNotIn("R/'input_manifest.json'", D.REMOTE)
        self.assertNotIn("R/'reused_scores.jsonl'", D.REMOTE)
        self.assertIn('scoring.verify_release', D.REMOTE)

    def test_compute_only_large_operations(self):
        for mode in ('stage', 'fetch'):
            command = D.remote_command(mode)
            self.assertIn('srun', command)
            self.assertIn('--jobid=402816', command)
            self.assertIn('--gres=none', command)
        self.assertIn("return node('sources',{})", D.REMOTE)
        self.assertIn("Stage only on existing compute allocation", D.REMOTE)
        self.assertNotIn('base64', D.STAGE_HELPERS)
        self.assertNotIn('read_bytes()', D.NODE)

    def test_explicit_six_task_acceptance_and_resume(self):
        self.assertIn("if mode=='start':assert maximum==6", D.REMOTE)
        for guard in ["existing_dispatch_no_duplicate", "Missing/ambiguous runner exit receipt",
                      "Previous login launcher still active", "summary.get('needs_reconcile',0)==0",
                      "summary.get('unmatched_intents',0)==0", "No existing dispatch to resume"]:
            self.assertIn(guard, D.REMOTE)
        self.assertIn("if intent['resume']:command.append('--resume')", D.WRAPPER)
        self.assertIn("'--max-new-tasks',str(maximum)", D.WRAPPER)

    def test_previous_experiments_and_idle_service_guard(self):
        for name in ['dvjustice_answer_batch128_20260928',
                     'dvjustice_selfscore_batch128_v2_20260928',
                     'dvjustice_thinking_pipeline_20260929']:
            self.assertIn(name, D.NODE)
            self.assertIn(name, D.WRAPPER)
        for code in ["summary['pending']==summary['inflight']==summary['needs_reconcile']==summary['unmatched_intents']==0",
                     "exit_record['exit_code']==0", "not alive(execution.get('runner_identity'))",
                     "not alive(wrapper.get('wrapper_identity'))", "result['metrics'][suffix]==0",
                     'fcntl.LOCK_EX|fcntl.LOCK_NB', '127.0.0.1:18044/v1/models']:
            self.assertIn(code, D.NODE)
        self.assertIn('locks.close()', D.WRAPPER)

    def test_process_identity_and_no_new_resources_or_answering(self):
        for code in ["runner_identity=identity(child.pid)", 'code=child.wait()',
                     "'.exit.json'", 'os.link(temporary,p)']:
            self.assertIn(code, D.WRAPPER)
        self.assertIn("str(R/'run_three_model_scoring.py')", D.WRAPPER)
        self.assertNotIn("str(R/'run_answer_batch128.py')", D.WRAPPER)
        self.assertIn("'--jobid=402816'", D.REMOTE)
        for code in [D.REMOTE, D.WRAPPER]:
            for forbidden in ['scancel', "'sbatch'", 'SIGTERM', 'SIGKILL', 'XHub']:
                self.assertNotIn(forbidden, code)

    def compressed(self, directory, raw):
        path = directory / 'source.gz'
        metadata = D.compress_source(io.BytesIO(raw), path)
        return metadata, path.read_bytes()

    def receiver(self, directory, zipped):
        def safe(path):
            path = Path(path)
            assert path.resolve() == path and directory in (path, *path.parents)
            return path
        namespace = dict(R=directory, MAX=D.MAX_FILE_BYTES, safe=safe, ast=ast, stat=stat,
                         hashlib=hashlib, pathlib=pathlib, json=json, os=os, re=re, uuid=uuid,
                         zlib=zlib, sys=types.SimpleNamespace(stdin=types.SimpleNamespace(buffer=io.BytesIO(zipped))))
        exec(compile(D.STAGE_HELPERS, '<streaming>', 'exec'), namespace)
        return namespace['receive_file']

    def test_streaming_roundtrip_and_frozen_restage(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            raw = '完整评分输入\n'.encode() * 200000
            metadata, zipped = self.compressed(directory, raw)
            for _ in range(2):
                result = self.receiver(directory, zipped)('input_scoring.json', metadata)
                self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
                self.assertEqual((directory / 'input_scoring.json').read_bytes(), raw)

    def test_streaming_rejects_corruption_bounds_trailing_and_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            metadata, zipped = self.compressed(directory, b'answer' * 100000)
            cases = [(dict(metadata, sha256='0' * 64), zipped),
                     (dict(metadata, gzip_sha256='0' * 64), zipped),
                     (dict(metadata, size=3), zipped),
                     (dict(metadata, size=D.MAX_FILE_BYTES + 1), zipped),
                     (metadata, zipped[:-1]),
                     (dict(metadata, gzip_size=len(zipped) + 1), zipped + b'x')]
            for item, blob in cases:
                with self.assertRaises((AssertionError, zlib.error)):
                    self.receiver(directory, blob)('rejected.json', item)
                self.assertFalse((directory / 'rejected.json').exists())
                self.assertFalse(list(directory.glob('.rejected.json.*')))

    def test_no_overwriting_conflicting_frozen_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            metadata, zipped = self.compressed(directory, b'new input')
            target = directory / 'input_scoring.json'
            target.write_bytes(b'original input')
            with self.assertRaises(AssertionError):
                self.receiver(directory, zipped)('input_scoring.json', metadata)
            self.assertEqual(target.read_bytes(), b'original input')

    def test_names_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            metadata, zipped = self.compressed(directory, b'input')
            for name in ['../escape.json', '/tmp/escape.json', 'nested/file.json']:
                with self.assertRaises(AssertionError):
                    self.receiver(directory, zipped)(name, metadata)

    def test_reject_unbounded_initial_start_without_network(self):
        with patch('sys.argv', [D.__file__, 'start']), patch.object(D.subprocess, 'run') as run:
            with self.assertRaises(SystemExit) as caught:
                D.main()
            self.assertEqual(caught.exception.code, 2)
            run.assert_not_called()


if __name__ == '__main__':unittest.main()
