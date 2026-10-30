import copy
import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import retry_gemini_refusals43_xhub as retry


class GeminiRetryTests(unittest.TestCase):
    def test_refusal_is_not_recovery_and_raw_score_stays_unchanged(self):
        result={'status':'success','evaluation':{'详细评价':retry.PREFIX,'总分':0}}
        original=copy.deepcopy(result)
        self.assertEqual(retry.classify(result),'still_refused')
        self.assertEqual(result,original)

    def test_five_dimension_names_without_scores_are_not_recovery(self):
        result={'status':'success','evaluation':{'详细评价':'\n'.join(retry.DIMENSIONS),'总分':0}}
        self.assertEqual(retry.classify(result),'unstructured_response_needs_review')

    def test_genuine_zero_scores_are_ratings(self):
        text='\n'.join(f'**【{d}】**得分：0分\n理由：未覆盖。' for d in retry.DIMENSIONS)
        self.assertEqual(retry.classify({'status':'success','evaluation':{'详细评价':text,'总分':0}}),'rating_returned')

    def test_api_failure_stays_failed(self):
        self.assertEqual(retry.classify({'status':'failed'}),'request_failed')

    def test_shared_lock_prevents_guard_or_api_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with (root/'run.lock').open('a+') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with patch.object(retry,'OUT',root), patch.object(retry,'GuardedTransport') as guard:
                    with patch('sys.argv',['retry','--execute','--retry-output',str(root/'retry')]):
                        with self.assertRaisesRegex(SystemExit,'shared run lock'):
                            retry.main()
                    guard.assert_not_called()


if __name__=='__main__':
    unittest.main()
