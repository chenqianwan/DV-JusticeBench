"""Offline invariants: answer-vs-score status, streaming, keys, provenance."""
import io
import json
from pathlib import Path
import tempfile
import unittest

import prepare_answers as prep


class StreamTests(unittest.TestCase):
    def test_streams_arbitrary_small_chunks_and_unicode(self):
        value = {'workers':128,'run':'a tasks string','tasks':[{'answer':'中文\n"[,]"','n':12345},{'x':'末尾'}],'other':True}
        text = json.dumps(value,ensure_ascii=False)
        for size in [1,2,3,7,10,101]:
            self.assertEqual(list(prep.streamed_tasks(io.StringIO(text),size)),value['tasks'])

    def test_empty_tasks_are_legal_parser_output(self):
        self.assertEqual(list(prep.streamed_tasks(io.StringIO('{"tasks":[]}'))),[])

    def test_duplicate_tasks_key_rejected(self):
        with self.assertRaises(ValueError):
            list(prep.streamed_tasks(io.StringIO('{"tasks":[],"tasks":[]}')))

    def test_truncation_not_silently_accepted(self):
        for text in ['{"tasks":[{"answer":"abc"}', '{"tasks":[', '{"workers":1}']:
            with self.assertRaises(ValueError):
                list(prep.streamed_tasks(io.StringIO(text),3))

    def test_trailing_data_or_commas_rejected(self):
        for text in ['{"tasks":[]} []','{"tasks":[{},]}','{"tasks":[],}']:
            with self.assertRaises(ValueError):
                list(prep.streamed_tasks(io.StringIO(text)))


class AnswerPolicyTests(unittest.TestCase):
    def test_complete_answer_not_excluded_by_scoring_failure(self):
        for scoring_status in ['ok','parse_incomplete','truncated','parse_error']:
            source={'answer_status':'ok','status':scoring_status,'answer':'真实回答'}
            self.assertEqual(prep.classify_answer(source['answer_status'],source['answer']),('complete',True))

    def test_truncated_filtered_unknown_never_counted_complete(self):
        for status in ['truncated','content_filter','needs_reconcile']:
            self.assertEqual(prep.classify_answer(status,'即使有片段也不能伪装完整回答'),(status,False))

    def test_missing_answer_not_filled_from_thinking(self):
        source={'answer':'','reasoning_content':'很多思考'}
        with self.assertRaises(ValueError):
            prep.classify_answer('ok',source['answer'])

    def test_unknown_status_fails_closed(self):
        with self.assertRaises(ValueError):
            prep.classify_answer('successish','text')

    def test_duplicate_identical_or_conflicting_record_rejected(self):
        items={}
        prep.insert_unique(items,('primary','case','q','ds_thinking'),'original')
        for value in ['original','conflict']:
            with self.assertRaises(ValueError):
                prep.insert_unique(items,('primary','case','q','ds_thinking'),value)

    def test_thinking_and_nonthinking_keys_are_separate(self):
        a=dict(split='primary',case_id='case',question_id='q',answer_condition='ds_thinking')
        b={**a,'answer_condition':'ds_nonthinking'}
        self.assertNotEqual(prep.record_key(a),prep.record_key(b))


class SourceTests(unittest.TestCase):
    def test_source_matches_old_report_hash_and_row_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'x.jsonl';path.write_bytes(b'{"x":1}\n')
            s=prep.Sources(root,{'x.jsonl':{'sha256':prep.file_hash(path),'rows':1}})
            rows=list(s.rows(path))
            self.assertEqual(rows[0][0],{'x':1})
            self.assertEqual(rows[0][1]['line_number'],1)
            self.assertTrue(s.entries['x.jsonl']['matched_old_report_hash'])

    def test_changed_hash_or_count_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'x.jsonl';path.write_bytes(b'{"x":1}\n')
            with self.assertRaises(ValueError):
                list(prep.Sources(root,{'x.jsonl':{'sha256':'0'*64,'rows':1}}).rows(path))
            with self.assertRaises(ValueError):
                list(prep.Sources(root,{'x.jsonl':{'sha256':prep.file_hash(path),'rows':2}}).rows(path))

    def test_partial_jsonl_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'x.jsonl';path.write_bytes(b'{"x":1}')
            with self.assertRaises(ValueError):
                list(prep.Sources(root,{}).rows(path))

    def test_provenance_hash_mismatch_rejected(self):
        row=dict(split='primary',case_id='c',question_id='q',question_text='question',
                 question_hash='q',masked_case_text_hash='f',source_case_text_hash='s',
                 answer_prompt_hash='p',judge_decision_hash='j')
        prep.verify_text_hashes(row,row,'test')
        for field in ['question_hash','masked_case_text_hash','source_case_text_hash','answer_prompt_hash','judge_decision_hash']:
            with self.assertRaises(ValueError):
                prep.verify_text_hashes({**row,field:'changed'},row,'test')


if __name__=='__main__':
    unittest.main()
