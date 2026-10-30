"""Offline tests: no SSH, API, Slurm invocation, or process signalling."""
import ast
import importlib.util
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('tune',HERE/'tune_benchmark_service.py')
TUNE=importlib.util.module_from_spec(spec)
spec.loader.exec_module(TUNE)


def source_value(tree,name):
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError('Missing '+name)


class TuneOfflineTest(unittest.TestCase):
    def setUp(self):
        self.launch=(HERE/'launch_service.py').read_bytes()
        self.validate=(HERE/'validate_cli.py').read_bytes()
        self.files=TUNE.transform_files(self.launch,self.validate)
        self.remote=ast.parse(TUNE.REMOTE)
        self.node=source_value(self.remote,'NODE')

    def test_all_three_python_layers_compile(self):
        compile((HERE/'tune_benchmark_service.py').read_text(),'controller','exec')
        compile(TUNE.REMOTE,'login-controller','exec')
        compile(self.node,'node-controller','exec')

    def test_exact_launcher_diff_only_two_values(self):
        expected=self.launch.decode().replace("'--max-num-seqs', '16'","'--max-num-seqs', '128'",1)
        expected=expected.replace("'max_num_seqs': 16","'max_num_seqs': 128",1)
        self.assertEqual(self.files['launch_service.py'].decode(),expected)
        # Do not execute main(): extract the pure argv function only.
        tree=ast.parse(expected)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='service_args')
        ctx={'RUNTIME':Path('/runtime'),'MODEL':Path('/model'),'ALIAS':'alias','PORT':18044}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'argv','exec'),ctx)
        argv=ctx['service_args']()
        self.assertEqual(argv[argv.index('--max-num-seqs')+1],'128')
        self.assertEqual(argv[argv.index('--tensor-parallel-size')+1],'8')
        self.assertIn('--enforce-eager',argv)

    def test_validator_only_adds_assertion(self):
        self.assertEqual(self.files['validate_cli.py'].decode().replace('    assert args.max_num_seqs == 128\n',''),self.validate.decode())

    def test_reject_already_modified_or_ambiguous_inputs(self):
        with self.assertRaises(ValueError):TUNE.transform_files(self.files['launch_service.py'],self.validate)
        with self.assertRaises(ValueError):TUNE.transform_files(self.launch,self.files['validate_cli.py'])
        with self.assertRaises(ValueError):TUNE.transform_files(self.launch+self.launch,self.validate)

    def test_no_allocation_submission_cancellation_or_kill(self):
        self.assertNotIn("'sbatch'",TUNE.REMOTE)
        self.assertNotIn('scancel',TUNE.REMOTE)
        self.assertNotIn('SIGKILL',TUNE.REMOTE)
        calls=[n for n in ast.walk(ast.parse(self.node)) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='kill']
        self.assertEqual(len(calls),1)
        self.assertEqual(ast.unparse(calls[0]),"os.kill(launcher['pid'], signal.SIGTERM)")

    def test_restart_is_atomic_non_overwriting_publish(self):
        self.assertIn('os.link(tmp,p)',TUNE.REMOTE)
        self.assertIn("'bench128-20260927' not in state['handled_request_ids']",TUNE.REMOTE)
        self.assertIn("state['attempt']=='001'",TUNE.REMOTE)
        self.assertIn("probe.bind(('0.0.0.0',18044))",self.node)

    def test_stop_requires_all_answers_and_idle_engine(self):
        for fragment in ["summary['success']==10","summary['counts']=={'ok':10}","values[suffix]==0",
                         "fcntl.LOCK_EX|fcntl.LOCK_NB","ident(launcher['pid'])==launcher"]:
            self.assertIn(fragment,self.node)

    def test_same_allocation_cpu_step_and_hidden_cuda_cli(self):
        self.assertIn("'--jobid=402816'",TUNE.REMOTE)
        self.assertIn("'--gres=none'",TUNE.REMOTE)
        self.assertIn("gres='--gres=gpu:8' if action=='cli-check' else '--gres=none'",TUNE.REMOTE)
        self.assertIn("'CUDA_VISIBLE_DEVICES':''",self.node)
        self.assertIn("fields.get('TimeLimit')!='5-00:00:00'",TUNE.REMOTE)


if __name__=='__main__':unittest.main()
