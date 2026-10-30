"""Synthetic offline coverage; never connects to a service or changes a corpus."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import inventory_citations as C


def row(answer, condition='gpt-4o', split='primary', case='c1', question='q1'):
    return {'answer': answer, 'answer_condition': condition, 'split': split,
            'case_id': case, 'question_id': question, 'answer_sha256': C.sha256(answer),
            'status': 'complete', 'main_denominator_eligible': True,
            'sources': {'answer_record': {'path': '/source/answers.jsonl',
                                          'file_sha256': 'a' * 64, 'record_sha256': 'b' * 64}}}


class CitationTests(unittest.TestCase):
    def build(self, records):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / 'answers.jsonl'
        source.write_text(''.join(C.canonical(record) + '\n' for record in records), encoding='utf-8')
        result = C.inventory(source, root / 'output')
        with gzip.open(root / 'output/citation_occurrences.jsonl.gz', 'rt', encoding='utf-8') as stream:
            occurrences = [json.loads(line) for line in stream]
        return result, occurrences, source, root

    def test_repeated_reference_counted_once_per_answer(self):
        result, occurrences, _, _ = self.build([row('《民法典》第一条。再次引用《民法典》第一条。')])
        self.assertEqual(result['title_candidates'][0]['unique_answer_count'], 1)
        self.assertEqual(result['title_candidates'][0]['occurrence_count'], 2)
        locator = result['locators_with_local_title'][0]
        self.assertEqual(locator['unique_answer_count'], 1)
        self.assertEqual(locator['occurrence_count'], 2)
        self.assertEqual(len(occurrences), 4)

    def test_same_provision_in_distinct_sources_is_not_merged(self):
        result, _, _, _ = self.build([row('《民法典》第一条及《婚姻法》第一条。')])
        self.assertEqual(len(result['locators_with_local_title']), 2)
        self.assertEqual({x['source_title_raw'] for x in result['locators_with_local_title']}, {'《民法典》', '《婚姻法》'})

    def test_aliases_not_merged(self):
        result, _, _, _ = self.build([row('《民法典》第一条。《中华人民共和国民法典》第一条。')])
        self.assertEqual(len(result['title_candidates']), 2)
        self.assertEqual(len(result['locators_with_local_title']), 2)

    def test_negation_and_examples_are_only_candidates(self):
        result, occurrences, _, _ = self.build([row('不能依据《民法典》第9999条。例如，“《虚构法》第一条”只是示例。')])
        self.assertEqual(len(result['locators_with_local_title']), 2)
        for occurrence in occurrences:
            self.assertEqual(occurrence['authority_verification_status'], 'not_verified')
            self.assertEqual(occurrence['hallucination_status'], 'not_assessed')
            self.assertEqual(occurrence['assertion_polarity'], 'not_classified')

    def test_source_does_not_cross_sentence_semicolon_or_newline(self):
        for separator in ['。', '；', '\n', '!', '?', '.']:
            _, locators, _ = C.extract_candidates('《民法典》' + separator + '第十条规定了某事项。')
            self.assertEqual(locators[0]['source_link_status'], 'unresolved', separator)
            self.assertIsNone(locators[0]['source_title_raw'])

    def test_source_does_not_cross_unrelated_prose(self):
        _, locators, _ = C.extract_candidates('《民法典》值得研究，另一本无名文件的第十条可能不同。')
        self.assertEqual(locators[0]['source_link_status'], 'unresolved')

    def test_multiple_adjacent_titles_stay_unresolved(self):
        _, locators, _ = C.extract_candidates('《甲法》和《乙法》第十条。')
        self.assertEqual(locators[0]['source_link_status'], 'unresolved')
        self.assertEqual(locators[0]['competing_title_candidates'], ['《甲法》', '《乙法》'])

    def test_compound_locator_and_exact_offsets(self):
        text = '😀依照《民法典》第 一千零六十二 条 第一 款 第（二）项。'
        titles, locators, _ = C.extract_candidates(text)
        self.assertEqual(len(locators), 1)
        self.assertEqual([c['unit'] for c in locators[0]['components']], ['条', '款'])
        for candidate in titles + locators:
            location = candidate['span']
            self.assertEqual(text[location['start']:location['end']], location['text'])
        self.assertEqual(locators[0]['components'][0]['number_raw'], '一千零六十二')

    def test_article_subnumber_and_enumeration(self):
        _, locators, _ = C.extract_candidates('《刑法》第一百三十三条之一、第二百三十二条。')
        self.assertEqual(len(locators), 2)
        self.assertEqual(locators[0]['components'][0]['subnumber_raw'], '一')
        self.assertEqual(locators[1]['source_link_reason'], 'explicit_same_clause_locator_list')

    def test_clause_only_does_not_invent_article_parent(self):
        _, locators, _ = C.extract_candidates('《民法典》第二款；第三项。')
        self.assertEqual(locators[0]['article_anchor_status'], 'unresolved_article_parent')
        self.assertEqual(locators[1]['source_link_status'], 'unresolved')

    def test_nonlegal_book_title_is_not_declared_authority(self):
        titles, locators, _ = C.extract_candidates('《红楼梦》第三条只是读书笔记标签。')
        self.assertEqual(titles[0]['candidate_kind'], 'book_title_candidate')
        self.assertEqual(locators[0]['source_link_status'], 'local_title_candidate')

    def test_nested_book_titles_and_unbalanced_markers(self):
        titles, locators, unbalanced = C.extract_candidates('《关于《甲法》的解释》第一条。另有《未闭合。')
        self.assertEqual(len(titles), 2)
        self.assertEqual(locators[0]['source_title_raw'], '《关于《甲法》的解释》')
        self.assertEqual(unbalanced, 1)

    def test_model_split_counts_and_hash_traceability(self):
        records = [row('《民法典》第一条。'),
                   row('《民法典》第一条。', 'qwen-max', 'secondary'),
                   row('没有显式引用。', 'ds_thinking', 'secondary')]
        result, occurrences, source, root = self.build(records)
        title = result['title_candidates'][0]
        self.assertEqual(title['unique_answer_count'], 2)
        self.assertEqual(title['unique_answer_count_by_answer_condition'], {'gpt-4o': 1, 'qwen-max': 1})
        self.assertEqual(title['unique_answer_count_by_split'], {'primary': 1, 'secondary': 1})
        self.assertEqual(result['input']['sha256'], C.sha256(source.read_bytes()))
        self.assertEqual(result['occurrence_file']['sha256'], C.sha256((root / 'output/citation_occurrences.jsonl.gz').read_bytes()))
        with gzip.open(root / 'output/citation_occurrences.jsonl.gz', 'rb') as stream:
            self.assertEqual(result['occurrence_file']['uncompressed_sha256'], C.sha256(stream.read()))
        summary = json.loads((root / 'output/citation_summary.json').read_text())
        self.assertLess((root / 'output/citation_summary.json').stat().st_size, 16000)
        self.assertEqual(summary['candidate_counts']['title_candidates'], 1)
        self.assertEqual(occurrences[0]['sources'], records[0]['sources'])
        self.assertEqual(occurrences[0]['answer_sha256'], C.sha256(records[0]['answer']))
        self.assertEqual(occurrences[0]['corpus_record_sha256'], C.sha256(source.read_bytes().splitlines(keepends=True)[0]))

    def test_unknown_locator_is_kept(self):
        result, _, _, _ = self.build([row('应考虑第一条第二款。')])
        self.assertEqual(len(result['unresolved_locator_patterns']), 1)
        self.assertFalse(result['locators_with_local_title'])

    def test_input_hash_mismatch_and_duplicate_keys_fail(self):
        wrong = row('《民法典》第一条。')
        wrong['answer_sha256'] = '0' * 64
        for records in ([wrong], [row('甲'), row('乙')]):
            with self.assertRaises(ValueError):
                self.build(records)

    def test_refuses_ineligible_answers_and_prior_output(self):
        wrong = row('不完整回答')
        wrong['main_denominator_eligible'] = False
        with self.assertRaises(ValueError):
            self.build([wrong])
        _, _, source, root = self.build([row('《民法典》第一条。')])
        existing = (root / 'output/citation_inventory.json.gz').read_bytes()
        with self.assertRaises(FileExistsError):
            C.inventory(source, root / 'output')
        self.assertEqual(existing, (root / 'output/citation_inventory.json.gz').read_bytes())

    def test_detailed_inventory_gzip_roundtrip(self):
        result, _, _, root = self.build([row('《民法典》第一条。')])
        with gzip.open(root / 'output/citation_inventory.json.gz', 'rt', encoding='utf-8') as stream:
            self.assertEqual(json.load(stream), result)

    def test_migration_deletion_is_exactly_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                C.compress_existing(temporary, remove_uncompressed=True)


if __name__ == '__main__':
    unittest.main()
