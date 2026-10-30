"""Adapter checks run in a subprocess to avoid changing the original test globals."""
from pathlib import Path
import subprocess
import sys
import unittest


class AdapterTests(unittest.TestCase):
    def test_frozen_input_and_original_generation_parameters(self):
        directory = Path(__file__).resolve().parent
        original_input = directory.parents[2] / 'data/pkulaw_v4_answer_smoke_20260927/input_tasks.json'
        code = r'''
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
import run_answer_smoke as original
original_config = copy.deepcopy(original.CONFIG)
original_key = original.KEY
original_source_hash = hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()
data = json.loads(Path(sys.argv[2]).read_bytes())
original_tasks = copy.deepcopy(data['tasks'])
original.validate(data)

import run_answer_smoke_wait900 as adapter
assert adapter.base.CONFIG['timeout_s'] == 900
assert adapter.base.CONFIG['run_id'] == 'pkulaw_v4_answer_smoke_20260927_wait900'
assert str(adapter.base.ROOT) == '/aifs4su/hansirui_3rd/chenlong/dvjustice_answering_smoke_20260927_wait900'
assert adapter.base.KEY == original_key
for key, value in original_config.items():
    if key not in ('timeout_s', 'run_id'):
        assert adapter.base.CONFIG[key] == value, key
data['timeout_s'] = 900
data['run_id'] = 'pkulaw_v4_answer_smoke_20260927_wait900'
adapter.base.validate(data)
assert data['tasks'] == original_tasks
with tempfile.TemporaryDirectory() as temporary:
    runner = adapter.Runner(adapter.base.canonical(data), Path(temporary) / 'unused',
                            lambda payload: (_ for _ in ()).throw(AssertionError('No HTTP allowed')))
    assert runner.identity['script_sha256'] == original_source_hash
    assert runner.identity['adapter_sha256'] == hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest()
    assert runner.identity['parameters']['timeout_s'] == 900
assert hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest() == original_source_hash
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(directory), str(original_input)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
