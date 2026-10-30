"""Offline paired-report checks; no model calls."""
import json
import unittest
from scripts.cluster.admin_ds_sentence_retry_20261003 import report

c = report.load_module(report.REPO / 'scripts/hallucination_h1_h5_sentence_ids_20261003/contract.py', '_test_report_contract')
old = report.load_module(report.REPO / 'scripts/hallucination_h1_h5_20261002/contract.py', '_test_report_old')


def response(content):
    return {'model': 'deepseek_v4_flash', 'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}


def row():
    answer = '原告报警。被告被判刑。'
    return {'request_id': 'r1', 'answer': answer, 'sentences': c.build_sentences(answer)}


def event():
    return {'sentence_ids': ['S000002'], 'category': 'H1', 'reason': '无此事实', 'correct_content': '仅记载报警', 'confidence': .9}


class ReportTests(unittest.TestCase):
    def inspect(self, content, valid, new=True):
        return report.classify(response(content), {'complete': True, 'valid_format': valid, 'status': 'complete'},
                               row(), c if new else old, new=new)

    def test_none_is_not_quote(self):
        x = self.inspect('NONE', True)
        self.assertEqual((x['none'], x['quote_events'], x['valid']), (1, 0, 1))

    def test_hydration_exact(self):
        x = self.inspect(json.dumps([event()]), True)
        self.assertEqual(x['hydrated']['events'][0]['quote'], '被告被判刑。')
        self.assertEqual(x['quote_events'], 1)

    def test_json_error(self):
        self.assertEqual(self.inspect('[{', False)['json_errors'], 1)

    def test_id_error(self):
        e = event(); e['sentence_ids'] = ['S999999']
        self.assertEqual(self.inspect(json.dumps([e]), False)['id_errors'], 1)

    def test_old_quote_error(self):
        e = event(); e.pop('sentence_ids'); e['quote'] = '不在原文的引文'
        self.assertEqual(self.inspect(json.dumps([e]), False, new=False)['quote_errors'], 1)

    def test_terminal_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, 'validity'):
            self.inspect('NONE', False)

    def test_denominators(self):
        valid = self.inspect('NONE', True)
        invalid = self.inspect('[{', False)
        truncated = report.classify(None, {'status': 'truncated'}, row(), c, new=True)
        x = report.total([valid, invalid, truncated])
        self.assertEqual(x['valid_rate_all_requests'], 1 / 3)
        self.assertEqual(x['valid_rate_complete_responses'], .5)

    def test_stratified_twenty_samples(self):
        events = [{'endpoint': host, 'source_condition': model, 'request_id': str(index)}
                  for host in ('a', 'b') for model in ('A', 'B', 'C', 'D', 'E') for index in range(5)]
        result = report.select_review(events)
        self.assertEqual(len(result), 20)
        self.assertEqual(report.select_review(list(reversed(events))), result)
        self.assertEqual(set(report.Counter((item['endpoint'], item['source_condition']) for item in result).values()), {2})


if __name__ == '__main__':
    unittest.main()
