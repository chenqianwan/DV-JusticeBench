import tempfile
import unittest
from pathlib import Path

from scripts.run_cross_judging_full108_xhub import (
    DIMENSIONS, atomic_write_json, rating_path, select_pending_tasks,
)


class RetrySelectionTests(unittest.TestCase):
    def test_transport_only_excludes_refusal_success_missing_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            tasks=[]
            errors={'proxy':'RuntimeError: ProxyError: connection failed',
                    'timeout':'RuntimeError: ReadTimeout: request timed out',
                    'rejected':'RuntimeError: HTTPError: 400 Client Error',
                    'explicit_refusal':'RuntimeError: content_filter',
                    'success':'', 'missing':''}
            for tid,error in errors.items():
                task=dict(task_id=tid,answer_sha256='same',source='new_api')
                tasks.append(task)
                if tid=='missing':continue
                result=dict(task,status='failed',error=error)
                if tid=='success':
                    result.update(status='success',evaluation={'各维度得分':{d:0 for d in DIMENSIONS}})
                atomic_write_json(rating_path(out,task),result)
            tasks.append(dict(task_id='old',source='reused_cross40',prior_rating_path='not_read'))
            selected=select_pending_tasks(out,tasks,transport_only=True)
            self.assertEqual([t['task_id'] for t in selected],['proxy','timeout'])
            default=select_pending_tasks(out,tasks)
            self.assertEqual([t['task_id'] for t in default],['proxy','timeout','rejected','missing'])


if __name__=='__main__':
    unittest.main()
