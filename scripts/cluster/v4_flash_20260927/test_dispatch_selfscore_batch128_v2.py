"""Offline deployment guards; no SSH, model requests, or real client starts."""
import ast
from pathlib import Path
import unittest

import dispatch_selfscore_batch128_v2 as D


def node_source():
    for node in ast.parse(D.REMOTE).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'NODE' for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError('NODE missing')


class DispatchSelfscoreTests(unittest.TestCase):
    def test_compile_all_layers(self):
        for name, code in [('remote', D.REMOTE), ('node', node_source()), ('wrapper', D.WRAPPER)]:
            compile(code, name, 'exec')

    def test_pack_integrity(self):
        for raw in [b'', '评分输入'.encode(), b'abc' * 500000]:
            self.assertEqual(D.unpack(D.pack(raw)), raw)
        item = D.pack(b'original')
        item['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            D.unpack(item)

    def test_exact_stage_files(self):
        self.assertEqual(set(D.DATA_FILES), {'input_scoring.json', 'input_manifest.json',
                                            'excluded_cases.json', 'legacy_scoring_bundle.py', 'reused_scores.jsonl', 'reused_tasks.json'})
        self.assertEqual(set(D.SCRIPT_FILES), {'run_selfscore_batch128_v2.py', 'run_selfscore_smoke.py',
                          'run_answer_batch128.py', 'run_answer_smoke.py', 'benchmark_concurrency.py'})
        self.assertIn("len(inputs['tasks'])==11093", D.REMOTE)
        self.assertIn("inputs['thinking'] is True", D.REMOTE)
        self.assertIn("not any(t['case_id'] in excludeids", D.REMOTE)
        self.assertNotIn('reused_answers.jsonl', D.REMOTE)

    def test_audited_reuse_and_explicit_resume_guard(self):
        for text in ["if mode=='start':assert maximum is None", "len(reused)==2",
                     "len(network_keys)==11093", "len(all_keys)==11095", "set(groups.values())=={5}",
                     "row['evaluation']==original['legacy_evaluation']",
                     "sha(original_bytes)==row['reuse_source']['terminal_sha256']",
                     "summary.get('needs_reconcile',0)==0", "'exit' not in info",
                     "Previous login launcher still active", "existing_dispatch_no_duplicate"]:
            self.assertIn(text, D.REMOTE)
        self.assertIn("'--max-new-tasks',str(maximum)", D.WRAPPER)
        self.assertIn("if intent['resume']:command.append('--resume')", D.WRAPPER)

    def test_preflight_answers_finished_and_services_idle(self):
        code = node_source()
        for text in ["upstream_summary['success']==11085", "['exit_code']==0",
                     "dvjustice_selfscore_smoke_20260927", "dvjustice_answer_batch128_20260928",
                     "not any(result['known_processes'].values())", "fcntl.LOCK_EX|fcntl.LOCK_NB",
                     "result['metrics'][suffix]==0"]:
            self.assertIn(text, code)

    def test_same_allocation_no_resource_release_or_answers(self):
        self.assertIn("'--jobid=402816'", D.REMOTE)
        self.assertIn("'--cpus-per-task=8'", D.REMOTE)
        self.assertIn("'--gres=gpu:8'", D.REMOTE)
        self.assertIn("str(R/'run_selfscore_batch128_v2.py')", D.WRAPPER)
        self.assertNotIn("str(R/'run_answer_batch128.py')", D.WRAPPER)
        for code in [D.REMOTE, D.WRAPPER]:
            for command in ('scancel', "'sbatch'", 'SIGTERM', 'SIGKILL'):
                self.assertNotIn(command, code)

    def test_wrapper_process_identity_and_exit(self):
        for text in ["runner_identity=identity(child.pid)", "code=child.wait()", "'.exit.json'",
                     "os.link(temporary,p)", "'max_new_tasks':maximum"]:
            self.assertIn(text, D.WRAPPER)

    def test_stage_validation_on_existing_compute_allocation(self):
        source = Path(D.__file__).read_text()
        self.assertIn("if args.mode=='stage':\n        # Decode/hash/validate", source)
        self.assertIn("'--cpus-per-task=2','--gres=none'", source)


if __name__ == '__main__':
    unittest.main()
