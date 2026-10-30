"""Offline dispatcher tests. Never SSH, call APIs, or start a real client."""
import ast
import base64
import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('dispatcher',HERE/'dispatch_answer_batch128.py')
D=importlib.util.module_from_spec(spec);spec.loader.exec_module(D)


def node_source():
    for n in ast.parse(D.REMOTE).body:
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='NODE' for t in n.targets):
            return ast.literal_eval(n.value)
    raise AssertionError('NODE missing')


class DispatcherOfflineTests(unittest.TestCase):
    def test_all_python_layers_compile(self):
        for name,source in [('remote',D.REMOTE),('node',node_source()),('wrapper',D.WRAPPER)]:
            compile(source,name,'exec')

    def test_gzip_roundtrip_utf8_empty_large(self):
        for raw in [b'', '原始案件问题'.encode(), b'ABCD'*500000]:
            self.assertEqual(D.unpack(D.pack(raw)),raw)

    def test_pack_is_deterministic(self):
        self.assertEqual(D.pack(b'x'*100),D.pack(b'x'*100))

    def test_reject_compressed_tampering(self):
        item=D.pack(b'payload');item['gzip_sha256']='0'*64
        with self.assertRaises(ValueError):D.unpack(item)

    def test_reject_size_or_plaintext_hash_tampering(self):
        for field,value in [('sha256','0'*64),('size',2),('size',True),('size',D.MAX_FILE_BYTES+1)]:
            item=D.pack(b'payload');item[field]=value
            with self.assertRaises(ValueError):D.unpack(item)

    def test_reject_gzip_trailing_stream(self):
        item=D.pack(b'hello');zipped=base64.b64decode(item['data'])+base64.b64decode(D.pack(b'world')['data'])
        item.update(data=base64.b64encode(zipped).decode(),gzip_size=len(zipped),gzip_sha256=D.digest(zipped))
        with self.assertRaises(ValueError):D.unpack(item)

    def test_fetch_snapshot_only_safe_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            out=D.save_fetch({'files':{'results/task_123/terminal.json':D.pack(b'{"status":"ok"}')},
                              'raw_attempts_included':False},Path(directory).resolve())
            self.assertEqual((Path(out['snapshot'])/'results/task_123/terminal.json').read_bytes(),b'{"status":"ok"}')
            with self.assertRaises(ValueError):
                D.save_fetch({'files':{'../secret':D.pack(b'x')},'raw_attempts_included':False},Path(directory).resolve())

    def test_exact_stage_whitelist_excludes_judgment_mapping(self):
        self.assertEqual(set(D.DATA_FILES),{'input_tasks.json','input_manifest.json','reused_answers.jsonl','excluded_cases.json'})
        self.assertNotIn('task_mapping.jsonl',D.DATA_FILES)
        self.assertIn("len(inputs['tasks'])==11085",D.REMOTE)
        self.assertIn("not any(t['case_id'] in excludeids",D.REMOTE)

    def test_explicit_resume_has_exit_pid_and_lock_guards(self):
        for fragment in ["mode=='resume'","'exit' not in info","Previous login launcher still active",
                         "info['node_identities'] if info else {}","existing_dispatch_no_duplicate"]:
            self.assertIn(fragment,D.REMOTE)
        for fragment in ["not any(result['known_processes'].values())","fcntl.LOCK_EX|fcntl.LOCK_NB",
                         "result['metrics'][suffix]==0"]:self.assertIn(fragment,node_source())

    def test_same_allocation_and_no_mutating_cluster_commands(self):
        self.assertIn("'--jobid=402816'",D.REMOTE)
        self.assertIn("'--cpus-per-task=8'",D.REMOTE)
        self.assertIn("'--gres=gpu:8'",D.REMOTE)
        for text in [D.REMOTE,D.WRAPPER]:
            self.assertNotIn('scancel',text);self.assertNotIn("'sbatch'",text)
            self.assertNotIn('SIGTERM',text);self.assertNotIn('SIGKILL',text)
        self.assertNotIn('api_key',D.WRAPPER)

    def test_wrapper_records_runner_and_exit(self):
        for fragment in ["runner_identity=identity(child.pid)","code=child.wait()","'.exit.json'","'--resume'"]:
            self.assertIn(fragment,D.WRAPPER)
        self.assertIn('os.link(temporary,p)',D.WRAPPER)


if __name__=='__main__':unittest.main()
