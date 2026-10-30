"""Offline c256 checks: no SSH, Slurm submission, network, GPU or inference."""
import ast
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parent
OLD = BASE.with_name('v4_flash_20261002')
OLD_ROOT = '/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002'
ROOT = OLD_ROOT + '_c256'


def module(name):
    spec = importlib.util.spec_from_file_location('c256_test_' + name, BASE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class DeploymentTests(unittest.TestCase):
    def test_only_reviewed_configuration_changes(self):
        for name in ('deploy.py', 'launch_service.py', 'allocation_supervisor.py',
                     'validate_cli.py', 'service.sbatch'):
            expected = (OLD / name).read_text().replace(OLD_ROOT, ROOT)
            if name == 'deploy.py':
                expected = expected.replace("comment='chenlong-dvjustice-v4flash-20261002'",
                                            "comment='chenlong-dvjustice-v4flash-20261002-c256'")
                expected = expected.replace("'supersedes_failed_job':'402816'",
                                            "'parent_deployment_job':'407198'")
            elif name == 'launch_service.py':
                expected = expected.replace("'--max-num-seqs', '128'", "'--max-num-seqs', '256'")
                expected = expected.replace("'max_num_seqs': 128", "'max_num_seqs': 256")
            elif name == 'validate_cli.py':
                expected = expected.replace('assert args.max_num_seqs == 128',
                                            'assert args.max_num_seqs == 256')
            self.assertEqual((BASE / name).read_text(), expected, name)

    def test_all_supervisor_regression_tests_preserved(self):
        name = 'test_allocation_supervisor.py'
        self.assertEqual((BASE / name).read_bytes(), (OLD / name).read_bytes())

    def test_launch_configuration(self):
        launcher = module('launch_service')
        self.assertEqual(str(launcher.ROOT), ROOT)
        self.assertEqual(str(launcher.MODEL), '/aifs4su/models/DeepSeek-V4-Flash')
        self.assertEqual(str(launcher.RUNTIME), '/aifs4su/hansirui/vllm-dsv4-venv')
        args = launcher.service_args()
        for key, value in {'--tensor-parallel-size': '8', '--max-num-seqs': '256',
                           '--max-model-len': '32768', '--port': '18044',
                           '--max-num-batched-tokens': '8192', '--gpu-memory-utilization': '0.90',
                           '--kv-cache-dtype': 'fp8', '--block-size': '256',
                           '--tokenizer-mode': 'deepseek_v4', '--reasoning-parser': 'deepseek_v4',
                           '--served-model-name': 'deepseek_v4_flash', '--dtype': 'auto'}.items():
            self.assertEqual(args.count(key), 1)
            self.assertEqual(args[args.index(key) + 1], value)
        for option in ('--trust-remote-code', '--enforce-eager', '--no-enable-log-requests'):
            self.assertIn(option, args)

    def test_endpoint_metadata_and_cli_capacity_match(self):
        launcher_tree = ast.parse((BASE / 'launch_service.py').read_text())
        capacities = [value.value for node in ast.walk(launcher_tree) if isinstance(node, ast.Dict)
                      for key, value in zip(node.keys, node.values)
                      if isinstance(key, ast.Constant) and key.value == 'max_num_seqs']
        self.assertEqual(capacities, [256])
        cli_tree = ast.parse((BASE / 'validate_cli.py').read_text())
        checks = [node.test for node in ast.walk(cli_tree) if isinstance(node, ast.Assert)]
        self.assertTrue(any(isinstance(check, ast.Compare) and
                            isinstance(check.left, ast.Attribute) and
                            check.left.attr == 'max_num_seqs' and
                            isinstance(check.ops[0], ast.Eq) and
                            isinstance(check.comparators[0], ast.Constant) and
                            check.comparators[0].value == 256 for check in checks))

    def test_exact_slurm_resources_and_new_root(self):
        directives = {}
        for line in (BASE / 'service.sbatch').read_text().splitlines():
            if line.startswith('#SBATCH '):
                args = shlex.split(line[8:])
                self.assertEqual(len(args), 1)
                key, value = args[0].split('=', 1) if '=' in args[0] else (args[0], True)
                self.assertNotIn(key, directives)
                directives[key] = value
        self.assertEqual(directives, {
            '--job-name': 'chenlong_dvj_v4flash', '--partition': 'llm', '--qos': 'llm',
            '--account': 'hansirui', '--nice': '0', '--nodes': '1', '--ntasks': '1',
            '--cpus-per-task': '64', '--gres': 'gpu:8', '--mem': '512G',
            '--time': '5-00:00:00', '--no-requeue': True, '--export': 'NONE',
            '--chdir': ROOT, '--output': ROOT + '/logs/slurm-%j.out',
            '--error': ROOT + '/logs/slurm-%j.err'})

    def test_remote_guards_and_syntax(self):
        deploy = module('deploy')
        ast.parse(deploy.REMOTE)
        self.assertEqual(deploy.BASE, BASE)
        self.assertNotIn(OLD_ROOT + "'", deploy.REMOTE)
        for text in ['submission.lock', 'Existing ambiguous intent', 'Matching job already exists',
                     "'-n','chenlong_dvj_v4flash'", "'--time':'5-00:00:00'",
                     'Already has submission record; do not restage',
                     'Full CLI validation has not passed for this exact deployment',
                     'Runtime changed since validation', "'CUDA_VISIBLE_DEVICES':''",
                     "'parent_deployment_job':'407198'", 'do not resubmit automatically']:
            self.assertIn(text, deploy.REMOTE)
        self.assertNotIn("'supersedes_failed_job':'407198'", deploy.REMOTE)
        self.assertNotIn('scancel', deploy.REMOTE)
        for path in BASE.glob('*.py'):
            ast.parse(path.read_text(), filename=str(path))
        subprocess.run(['/bin/bash', '-n', str(BASE / 'service.sbatch')], check=True)

    def test_launcher_rejects_wrong_user_or_allocation_before_process(self):
        launcher = module('launch_service')
        for uid, job, nodes in ((1084, '123', '1'), (1083, '', '1'),
                                (1083, 'not-a-job', '1'), (1083, '123', '2')):
            with self.subTest(uid=uid, job=job, nodes=nodes), \
                 mock.patch.dict(launcher.os.environ, {'SLURM_JOB_ID': job,
                                                      'SLURM_JOB_NUM_NODES': nodes}, clear=True), \
                 mock.patch.object(launcher.os, 'getuid', return_value=uid), \
                 mock.patch.object(launcher.os, 'umask'), \
                 mock.patch.object(launcher, 'checked_dir') as checked, \
                 mock.patch.object(launcher.subprocess, 'Popen') as popen:
                with self.assertRaisesRegex(RuntimeError, 'authorized Unix account'):
                    launcher.main()
                checked.assert_not_called()
                popen.assert_not_called()

    def test_changed_deployment_metadata_blocks_launcher_before_process(self):
        launcher = module('launch_service')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'preflight.json').write_text(json.dumps({
                'deployment_files': {'launch_service.py': 'expected-digest'}}))
            with mock.patch.object(launcher, 'ROOT', root), \
                 mock.patch.object(launcher, 'checked_dir', side_effect=lambda p: p), \
                 mock.patch.object(launcher, 'sha', return_value='modified-digest'), \
                 mock.patch.dict(launcher.os.environ, {'SLURM_JOB_ID': '123',
                                                      'SLURM_JOB_NUM_NODES': '1'}, clear=True), \
                 mock.patch.object(launcher.os, 'getuid', return_value=1083), \
                 mock.patch.object(launcher.os, 'umask'), \
                 mock.patch.object(launcher.subprocess, 'Popen') as popen:
                with self.assertRaisesRegex(RuntimeError, 'Deployment file changed after staging'):
                    launcher.main()
                popen.assert_not_called()

    def test_non_h800_or_insufficient_gpu_allocation_blocks_launcher(self):
        launcher = module('launch_service')
        samples = ['NVIDIA H800, 81920\n' * 7,
                   'NVIDIA H100, 81920\n' * 8,
                   'NVIDIA H800, 78000\n' * 8]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'preflight.json').write_text(json.dumps({
                'deployment_files': {}, 'model_files': {}, 'runtime_files': {}, 'shards': []}))
            for output in samples:
                with self.subTest(gpu_rows=output), \
                     mock.patch.object(launcher, 'ROOT', root), \
                     mock.patch.object(launcher, 'checked_dir', side_effect=lambda p: p), \
                     mock.patch.dict(launcher.os.environ, {'SLURM_JOB_ID': '123',
                                                          'SLURM_JOB_NUM_NODES': '1'}, clear=True), \
                     mock.patch.object(launcher.os, 'getuid', return_value=1083), \
                     mock.patch.object(launcher.os, 'umask'), \
                     mock.patch.object(launcher.os, 'open', return_value=123), \
                     mock.patch.object(launcher.fcntl, 'flock'), \
                     mock.patch.object(launcher.socket, 'socket'), \
                     mock.patch.object(launcher.subprocess, 'run', return_value=SimpleNamespace(stdout=output)), \
                     mock.patch.object(launcher.subprocess, 'Popen') as popen:
                    with self.assertRaisesRegex(RuntimeError, 'not eight H800 80GB GPUs'):
                        launcher.main()
                    popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
