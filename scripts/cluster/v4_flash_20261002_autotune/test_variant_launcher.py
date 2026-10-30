"""Offline tests only: no SSH, GPU execution, vLLM import, or network calls."""
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


BASE = Path(__file__).resolve().parent
v = load('variant_test_module', BASE / 'variant_launcher.py')
original = load('frozen_launcher_test', BASE.with_name('v4_flash_20261002') / 'launch_service.py')


class ArgumentTests(unittest.TestCase):
    def test_exactly_one_service_argument_changes(self):
        before = original.service_args()
        for cap in v.CONCURRENCIES:
            with self.subTest(cap=cap):
                after = v.variant_args(before, cap)
                idx = before.index('--max-num-seqs') + 1
                self.assertEqual(after[idx], str(cap))
                self.assertEqual(after[:idx] + after[idx + 1:], before[:idx] + before[idx + 1:])
                self.assertEqual(before, original.service_args())

    def test_bad_capacity_or_argv_rejected(self):
        for cap in (16, 129, 1024, True, '128'):
            with self.subTest(cap=cap), self.assertRaises(RuntimeError):
                v.variant_args(original.service_args(), cap)
        for args in ([], ['--max-num-seqs'], ['--max-num-seqs', '64'],
                     ['--max-num-seqs', '128', '--max-num-seqs', '128']):
            with self.assertRaises(RuntimeError):
                v.variant_args(args, 256)

    def test_allocation_guard(self):
        good = {'SLURM_JOB_ID': '407198', 'SLURM_JOB_NUM_NODES': '1'}
        v.validate_context(good, 1083)
        for env, uid in (({}, 1083), (good, 0), ({**good, 'SLURM_JOB_ID': '402816'}, 1083),
                         ({**good, 'SLURM_JOB_NUM_NODES': '2'}, 1083)):
            with self.assertRaises(RuntimeError):
                v.validate_context(env, uid)

    def test_original_idle_required(self):
        good = {'job_id': '407198', 'allocation_state': 'waiting_for_repair',
                'waiting_for_repair': True, 'service_pid': None, 'cleanup_unknown': False,
                'cleanup_survivors': [], 'process_inspection': 'verified'}
        v.verify_original_idle(good)
        for change in ({'service_pid': 123}, {'cleanup_unknown': True}, {'cleanup_survivors': [1]},
                       {'process_inspection': 'unavailable'}, {'job_id': '402816'},
                       {'allocation_state': 'service_running'}):
            with self.assertRaises(RuntimeError):
                v.verify_original_idle({**good, **change})

    def test_gpu_validation(self):
        good = '\n'.join(['NVIDIA H800, 81559'] * 8)
        self.assertEqual(len(v.gpu_inventory(good)), 8)
        for raw in (good.replace('H800', 'A800'), '\n'.join(['NVIDIA H800, 81559'] * 7),
                    good.replace('81559', '40000')):
            with self.assertRaises(RuntimeError):
                v.gpu_inventory(raw)


class PathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.tune = self.root / 'tune'
        self.old = self.root / 'old'
        self.old.mkdir()
        self.patchers = [patch.object(v, 'TUNE_ROOT', self.tune), patch.object(v, 'ORIGINAL_ROOT', self.old)]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.temp.cleanup()

    def test_create_new_only(self):
        run = self.tune / 'runs' / 'cap256-a001'
        self.assertEqual(v.create_run_dir(run), run)
        with self.assertRaises(RuntimeError):
            v.create_run_dir(run)
        for target in (self.tune, self.old / 'foo', Path('relative'), self.tune / '..' / 'escape'):
            with self.assertRaises(RuntimeError):
                v.create_run_dir(target)

    def test_symlink_rejected(self):
        self.tune.mkdir()
        (self.tune / 'linked').symlink_to(self.old, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            v.create_run_dir(self.tune / 'linked' / 'run')
        (self.tune / 'broken').symlink_to(self.root / 'missing')
        with self.assertRaises(RuntimeError):
            v.create_run_dir(self.tune / 'broken')

    def test_owner_rejected(self):
        with self.assertRaises(RuntimeError):
            v.owned_path(self.old, self.root, uid=os.getuid() + 1)

    def test_outputs_only_in_run(self):
        run = v.create_run_dir(self.tune / 'run')
        v.write_json(run, 'endpoint.json', {'status': 'starting'})
        self.assertEqual(json.loads((run / 'endpoint.json').read_text()), {'status': 'starting'})
        with self.assertRaises(RuntimeError):
            v.write_json(run, '../endpoint.json', {})
        self.assertEqual(list(self.old.iterdir()), [])

    def test_original_lock_never_created_or_modified(self):
        with self.assertRaises(FileNotFoundError):
            v.acquire_original_service_lock()
        lock = self.old / 'service.lock'
        lock.write_bytes(b'original')
        fd = v.acquire_original_service_lock()
        try:
            with self.assertRaises(BlockingIOError):
                v.acquire_original_service_lock()
        finally:
            os.close(fd)
        self.assertEqual(lock.read_bytes(), b'original')

    def test_environment_preserves_gpu_visibility_and_contains_paths(self):
        run = v.create_run_dir(self.tune / 'run')
        temp = v.create_temp_dir(run)
        env = v.build_environment({'CUDA_VISIBLE_DEVICES': '0,1,2,3,4,5,6,7', 'SLURM_JOB_ID': v.JOB,
            'VLLM_UNREVIEWED': 'bad', 'SGLANG_BAD': 'bad', 'PYTHONPATH': '/bad'},
            run, Path('/runtime'), 'secret', temp)
        self.assertEqual(env['CUDA_VISIBLE_DEVICES'], '0,1,2,3,4,5,6,7')
        for key in ('VLLM_UNREVIEWED', 'SGLANG_BAD', 'PYTHONPATH'):
            self.assertNotIn(key, env)
        for key in ('HF_HOME', 'VLLM_CACHE_ROOT', 'XDG_CONFIG_HOME', 'CUDA_CACHE_PATH'):
            self.assertIn(run, Path(env[key]).parents)
        self.assertEqual(env['TMPDIR'], str(temp))
        self.assertNotIn('VLLM_RPC_BASE_PATH', env)
        self.assertEqual(env['VLLM_API_KEY'], 'secret')
        self.assertEqual(list(self.old.iterdir()), [])

    def test_short_temp_unique_and_never_reused(self):
        run = v.create_run_dir(self.tune / 'run')
        temp = v.create_temp_dir(run)
        self.assertEqual(temp.parent, self.root / 't')
        self.assertTrue(temp.name.startswith('dvjt407198'))
        with self.assertRaises(RuntimeError):
            v.create_temp_dir(run)
        other = v.create_temp_dir(v.create_run_dir(self.tune / 'other'))
        self.assertNotEqual(temp, other)
        self.assertTrue(temp.is_dir())

    def test_short_temp_symlink_rejected(self):
        (self.root / 't').symlink_to(self.old, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            v.create_temp_dir(self.tune / 'run')

    def frozen_fixture(self):
        model, runtime = self.root / 'model', self.root / 'runtime'
        model.mkdir(); runtime.mkdir()
        for name in v.DEPLOYMENT_FILES:
            (self.old / name).write_text('frozen-' + name)
        (model / 'config.json').write_text('{}')
        (model / 'model-1.safetensors').write_bytes(b'weight')
        (runtime / 'code.py').write_text('# frozen runtime')
        manifest = {'deployment_files': {n: v.sha(self.old / n) for n in v.DEPLOYMENT_FILES},
                    'model_files': {'config.json': v.sha(model / 'config.json')},
                    'runtime_files': {str(runtime / 'code.py'): v.sha(runtime / 'code.py')},
                    'shards': [{'file': 'model-1.safetensors', 'bytes': 6}], 'model_revision': 'fixed'}
        module = SimpleNamespace(ROOT=self.old, MODEL=model, RUNTIME=runtime, PORT=18044,
                                 ALIAS='deepseek_v4_flash', service_args=original.service_args)
        return module, manifest

    def verify_fixture(self, module, manifest):
        spec = Mock()
        with patch.object(v, 'read_original', return_value=manifest), \
             patch.object(v.importlib.util, 'spec_from_file_location', return_value=spec), \
             patch.object(v.importlib.util, 'module_from_spec', return_value=module):
            return v.verify_original()

    def test_frozen_inventory_passes(self):
        module, manifest = self.frozen_fixture()
        self.assertEqual(self.verify_fixture(module, manifest), (module, manifest))

    def test_frozen_deployment_mismatch_fails_before_import(self):
        module, manifest = self.frozen_fixture()
        (self.old / 'launch_service.py').write_text('changed')
        with patch.object(v.importlib.util, 'module_from_spec') as imported, self.assertRaises(RuntimeError):
            with patch.object(v, 'read_original', return_value=manifest):
                v.verify_original()
        imported.assert_not_called()

    def test_model_runtime_and_shard_changes_each_fail(self):
        module, manifest = self.frozen_fixture()
        for path in (module.MODEL / 'config.json', module.RUNTIME / 'code.py',
                     module.MODEL / 'model-1.safetensors'):
            before = path.read_bytes()
            path.write_bytes(before + b'changed')
            try:
                with self.assertRaises(RuntimeError):
                    self.verify_fixture(module, manifest)
            finally:
                path.write_bytes(before)

    def test_main_loop_writes_only_variant_outputs_and_binds_identity(self):
        module, manifest = self.frozen_fixture()
        (self.old / 'preflight.json').write_text(json.dumps(manifest))
        (self.old / 'api_key').write_text('sensitive-key-' + 'x' * 40)
        (self.old / 'api_key').chmod(0o600)
        (self.old / 'service.lock').write_bytes(b'unchanged')
        idle = {'job_id': v.JOB, 'allocation_state': 'waiting_for_repair', 'waiting_for_repair': True,
                'service_pid': None, 'cleanup_unknown': False, 'cleanup_survivors': [],
                'process_inspection': 'verified'}
        (self.old / 'allocation_status.json').write_text(json.dumps(idle))
        before = {p.name: p.read_bytes() for p in self.old.iterdir()}
        child = Mock(pid=99999)
        child.poll.return_value = None
        event = Mock()
        event.is_set.side_effect = [False, True, True]
        event.wait.return_value = True
        run = self.tune / 'cap256'
        def cleaned(process):
            process.poll.return_value = -15
        with patch.object(v, 'validate_context'), patch.object(v, 'verify_original', return_value=(module, manifest)), \
             patch.object(v.socket, 'socket'), patch.object(v.subprocess, 'run', return_value=SimpleNamespace(
                 stdout='\n'.join(['NVIDIA H800, 81559'] * 8))), \
             patch.object(v.subprocess, 'Popen', return_value=child) as popen, \
             patch.object(v, 'process_identity', side_effect=lambda pid: {'pid': pid, 'started': '123'}), \
             patch.object(v.threading, 'Event', return_value=event), \
             patch.object(v, 'wait_ready', return_value={'usage': {'total_tokens': 5}}), \
             patch.object(v, 'terminate_group', side_effect=cleaned), patch('sys.stdout', new_callable=io.StringIO):
            code = v.run_variant(256, run)
        self.assertEqual(code, 0)
        endpoint = json.loads((run / 'endpoint.json').read_text())
        self.assertEqual(endpoint['status'], 'stopped')
        self.assertEqual(endpoint['max_num_seqs'], 256)
        self.assertEqual(endpoint['server_pid'], 99999)
        self.assertEqual(endpoint['launcher_started'], '123')
        self.assertTrue((run / 'exit.json').exists())
        self.assertNotIn('sensitive-key', json.dumps(endpoint))
        self.assertEqual(popen.call_args.kwargs['cwd'], run)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.old.iterdir()})


class ProbeTests(unittest.TestCase):
    def response(self, value, status=200):
        result = io.BytesIO(json.dumps(value).encode())
        result.status = status
        return result

    def test_readiness_requires_health_models_and_probe(self):
        opener = Mock()
        probe = {'choices': [{'message': {'content': '2'}}], 'usage': {'total_tokens': 5}}
        opener.open.side_effect = [self.response({}), self.response({'data': [{'id': 'deepseek_v4_flash'}]}),
                                    self.response(probe)]
        child = Mock(); child.poll.return_value = None
        with patch.object(v, 'write_json') as write:
            result = v.wait_ready(child, threading.Event(), opener, 'not-printed', 'deepseek_v4_flash', Path('/fake'))
        self.assertEqual(result, probe)
        self.assertEqual(opener.open.call_count, 3)
        payload = json.loads(opener.open.call_args_list[-1].args[0].data)
        self.assertEqual(payload['max_tokens'], 32)
        self.assertIs(payload['chat_template_kwargs']['thinking'], False)
        write.assert_called_once()

    def test_wrong_probe_fails_once(self):
        opener = Mock()
        opener.open.side_effect = [self.response({}), self.response({'data': [{'id': 'm'}]}),
            self.response({'choices': [{'message': {'content': 'incorrect'}}]})]
        child = Mock(); child.poll.return_value = None
        with patch.object(v, 'write_json'), self.assertRaises(RuntimeError):
            v.wait_ready(child, threading.Event(), opener, 'secret', 'm', Path('/fake'))
        self.assertEqual(opener.open.call_count, 3)

    def test_dead_child_no_network(self):
        child, opener = Mock(), Mock()
        child.poll.return_value = 1
        self.assertIsNone(v.wait_ready(child, threading.Event(), opener, 'x', 'm', Path('/fake')))
        opener.open.assert_not_called()

    def test_zero_timeout_no_network(self):
        child, opener = Mock(), Mock()
        child.poll.return_value = None
        self.assertIsNone(v.wait_ready(child, threading.Event(), opener, 'x', 'm', Path('/fake'), timeout=0))
        opener.open.assert_not_called()

    def test_sigterm_group_then_bounded_kill(self):
        child = Mock(pid=123)
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('fake', 1), 0]
        with patch.object(v.os, 'killpg') as kill:
            v.terminate_group(child, grace=1)
        self.assertEqual([x.args for x in kill.call_args_list], [(123, signal.SIGTERM), (123, signal.SIGKILL)])

    def test_already_exited_process_not_signalled(self):
        child = Mock(); child.poll.return_value = 0
        with patch.object(v.os, 'killpg') as kill:
            v.terminate_group(child)
        kill.assert_not_called()

    def test_import_does_not_read_remote_or_launch(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('Unexpected read')), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError('Unexpected process')):
            load('isolated_variant_import', BASE / 'variant_launcher.py')


if __name__ == '__main__':
    unittest.main()
