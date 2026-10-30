"""Offline deployment checks: no SSH, Slurm submission or GPU use."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import unittest

BASE = Path(__file__).resolve().parent
OLD = BASE.with_name('v4_flash_20260927')
OLD_ROOT = '/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20260927'
ROOT = '/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002'


def module(name):
    spec = importlib.util.spec_from_file_location('deployment_test_' + name, BASE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class DeploymentTests(unittest.TestCase):
    def test_only_reviewed_configuration_changes(self):
        for name in ('deploy.py', 'launch_service.py', 'validate_cli.py', 'service.sbatch'):
            expected = (OLD / name).read_text().replace(OLD_ROOT, ROOT)
            if name == 'deploy.py':
                expected = expected.replace("comment='chenlong-dvjustice-v4flash-20260927'",
                                            "comment='chenlong-dvjustice-v4flash-20261002'")
                expected = expected.replace("'supersedes_failed_job':'397374'", "'supersedes_failed_job':'402816'")
            elif name == 'launch_service.py':
                expected = expected.replace("'--max-num-seqs', '16'", "'--max-num-seqs', '128'")
                expected = expected.replace("'max_num_seqs': 16", "'max_num_seqs': 128")
            elif name == 'validate_cli.py':
                expected = expected.replace('    assert args.max_model_len == 32768\n',
                                            '    assert args.max_model_len == 32768\n    assert args.max_num_seqs == 128\n')
            self.assertEqual((BASE / name).read_text(), expected, name)

    def test_launch_configuration(self):
        launcher = module('launch_service')
        self.assertEqual(str(launcher.ROOT), ROOT)
        self.assertEqual(str(launcher.MODEL), '/aifs4su/models/DeepSeek-V4-Flash')
        args = launcher.service_args()
        for key, value in {'--tensor-parallel-size': '8', '--max-num-seqs': '128',
                           '--max-model-len': '32768', '--port': '18044'}.items():
            self.assertEqual(args[args.index(key) + 1], value)

    def test_remote_guard_and_syntax(self):
        deploy = module('deploy')
        ast.parse(deploy.REMOTE)
        self.assertEqual(deploy.BASE, BASE)
        self.assertNotIn(OLD_ROOT, deploy.REMOTE)
        for text in ['submission.lock', 'Existing ambiguous intent', 'Matching job already exists',
                     "'-n','chenlong_dvj_v4flash'", "'--time':'5-00:00:00'"]:
            self.assertIn(text, deploy.REMOTE)
        subprocess.run(['/bin/bash', '-n', str(BASE / 'service.sbatch')], check=True)


if __name__ == '__main__':
    unittest.main()
