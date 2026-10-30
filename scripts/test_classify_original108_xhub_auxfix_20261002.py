import json
import unittest
from scripts.classify_original108_xhub_auxfix_20261002 import parse_label


class AuxiliaryFixTests(unittest.TestCase):
    def setUp(self):
        self.item = {'label':'primary','court_addressed_dv':True,'dispute_types':['是否构成家暴'],
                     'confidence':-1,'reason':'法院明确认定家暴'}

    def test_invalid_confidence_preserved(self):
        result, flags = parse_label(json.dumps(self.item))
        self.assertEqual(result, self.item)
        self.assertEqual(flags, ['auxiliary_confidence_invalid_raw_value_preserved'])

    def test_valid_confidence_unchanged(self):
        self.item['confidence'] = .9
        result, flags = parse_label(json.dumps(self.item))
        self.assertEqual(result, self.item)
        self.assertEqual(flags, [])

    def test_bad_json_and_main_fields_still_fail(self):
        with self.assertRaises(ValueError):
            parse_label(json.dumps(self.item).replace('-1', '—'))
        for name, value in [('label','ir'),('court_addressed_dv','true'),('reason','')]:
            bad = dict(self.item, **{name:value})
            with self.assertRaises(ValueError):
                parse_label(json.dumps(bad))

    def test_secondary_not_promoted(self):
        self.item['label'] = 'secondary'
        result, flags = parse_label(json.dumps(self.item))
        self.assertEqual(result['label'], 'secondary')
        self.assertIn('raw_label_court_flag_inconsistent_with_strict_prompt', flags)


if __name__ == '__main__':
    unittest.main()
