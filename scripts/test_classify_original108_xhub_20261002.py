import copy
import json
import unittest

from scripts.classify_original108_xhub_20261002 import parse_label, load_tasks


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.item = {'label':'secondary', 'court_addressed_dv':False, 'dispute_types':['离婚事由'],
                     'confidence':0.9,'reason':'家暴仅作背景'}

    def test_exact_inputs(self):
        tasks, manifest = load_tasks()
        self.assertEqual(len(tasks),108)
        self.assertEqual(len({t['case_id'] for t in tasks}),108)
        self.assertEqual(sum(t['keyword_hit'] for t in tasks),106)
        self.assertFalse(manifest['input_truncated'])

    def test_raw_and_fenced(self):
        for text in [json.dumps(self.item), '```json\n'+json.dumps(self.item)+'\n```']:
            result, flags = parse_label(text)
            self.assertEqual(result,self.item)
            self.assertEqual(flags,[])

    def test_semantic_flag_does_not_relabel(self):
        self.item['court_addressed_dv'] = True
        result, flags = parse_label(json.dumps(self.item))
        self.assertEqual(result['label'],'secondary')
        self.assertIn('raw_label_court_flag_inconsistent_with_strict_prompt',flags)

    def test_bad_schema_boolean_label_confidence(self):
        for field, val in [('label','ir'),('court_addressed_dv','false'),('confidence',True),
                           ('confidence',1.1),('dispute_types','离婚事由'),('reason','')]:
            obj = copy.deepcopy(self.item); obj[field]=val
            with self.assertRaises(ValueError):
                parse_label(json.dumps(obj))
        with self.assertRaises(ValueError):
            parse_label('解释'+json.dumps(self.item))

    def test_format_flags_are_preserved_not_rewritten(self):
        self.item['reason']='理由'*15
        self.item['dispute_types']=['精神损害赔偿']
        result, flags = parse_label(json.dumps(self.item))
        self.assertEqual(result,self.item)
        self.assertEqual(set(flags),{'reason_over_20_characters','dispute_type_outside_literal_enum'})


if __name__ == '__main__':
    unittest.main()
