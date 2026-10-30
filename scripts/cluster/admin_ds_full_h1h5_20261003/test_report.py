import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('full_report', HERE / 'report.py')
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)
REPO = HERE.parents[2]
contract = r.load_module(REPO / 'scripts/hallucination_h1_h5_sentence_ids_20261003/contract.py', '_test_report_contract')
bench = r.load_module(REPO / 'scripts/cluster/v4_flash_20261002_autotune/bench.py', '_test_report_bench')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.canonical(value) + b'\n')


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.snapshot = self.base / 'snapshot'
        self.snapshot.mkdir()
        self.output = self.base / 'report'
        self.patch_total = patch.object(r, 'TOTAL', 4)
        self.patch_reuse = patch.object(r, 'REUSE_COUNT', 1)
        self.patch_total.start()
        self.patch_reuse.start()
        self.addCleanup(self.patch_total.stop)
        self.addCleanup(self.patch_reuse.stop)
        self.build()

    def build(self):
        self.parameters = {'model': 'deepseek_v4_flash', 'temperature': 0.1, 'max_tokens': 3000,
                           'chat_template_kwargs': {'thinking': False}}
        self.requests, self.mapping, self.reuse, shards = [], [], [], {h: [] for h in r.HOSTS}
        content = [json.dumps([{'sentence_ids': ['S000001'], 'category': 'H1', 'reason': '依据',
                               'correct_content': '核对内容', 'confidence': .9}], ensure_ascii=False),
                   'NONE', '{broken', 'cut off']
        for index in range(4):
            key = ('primary' if index < 2 else 'secondary', 'case' + str(index), 'q' + str(index), 'source')
            rid = 'perf_' + r.sha(r.canonical(list(key)))
            host = r.HOSTS[index % 2]
            answer = '原始事实。另一个句子。'
            table = contract.build_sentences(answer)
            packet = {'case_text': '案情', 'question': '问题', 'judge_decision': '裁判',
                      'reference_pack': {'evidence': [], 'checklists': [], 'coverage': {}}, 'sentence_table': table}
            messages = [{'role': 'system', 'content': 'frozen system'}, {'role': 'user', 'content': json.dumps(packet, ensure_ascii=False)}]
            row = {'request_id': rid, 'global_index': index, 'messages': messages, 'answer': answer,
                   'sentences': table, 'answer_sha256': r.sha(answer.encode()), 'packet_sha256': r.sha(messages[1]['content'].encode())}
            meta = dict(zip(r.KEYS, key))
            meta.update(request_id=rid, global_index=index, source_line=index + 1, host=host,
                        answer_sha256=row['answer_sha256'], packet_sha256=row['packet_sha256'],
                        masked_case_text_hash=r.sha('案情'.encode()), question_hash=r.sha('问题'.encode()), judge_decision_hash=r.sha('裁判'.encode()))
            raw = r.canonical({'model': 'deepseek_v4_flash', 'id': 'receipt-' + str(index), 'usage': {'completion_tokens': 50},
                'choices': [{'finish_reason': 'length' if index == 3 else 'stop', 'message': {'content': content[index]}}]})
            parsed = {}
            def parser(text, original):
                parsed['native'] = contract.parse_output(text, original, table)
                parsed['hydrated'] = contract.hydrate_output(parsed['native'], original, table)
                return parsed['native']
            terminal = bench.inspect_response(raw.decode(), answer, status=200, parser=parser)
            terminal.update(request_id=rid, sent_intent=True)
            if terminal['valid_format']:
                terminal['event_count'] = 0 if parsed['native'] == 'NONE' else len(parsed['native'])
            http = {'status': 200, 'body_complete': True, 'raw_sha256': r.sha(raw)}
            if index == 0:
                self.reuse.append({'request_id': rid, 'global_index': index, 'host': host,
                    'answer_sha256': row['answer_sha256'], 'packet_sha256': row['packet_sha256'], 'terminal': terminal,
                    'response_raw': raw.decode(), 'http': http, 'native': parsed.get('native'), 'hydrated': parsed.get('hydrated'),
                    'provenance': {'reference_ready': False, 'legal_truth_verified': False}})
            else:
                directory = self.snapshot / 'sessions/session01' / host / 'requests' / f'{index:06d}_{r.sha(rid.encode())[:16]}'
                payload = dict(self.parameters, messages=messages)
                write(directory / 'intent.json', {'request_id': rid, 'answer_sha256': row['answer_sha256'],
                    'packet_sha256': row['packet_sha256'], 'sentences_sha256': r.sha(r.canonical(table)),
                    'payload': payload, 'payload_sha256': r.sha(r.canonical(payload))})
                write(directory / 'terminal.json', terminal)
                (directory / 'response.raw.txt').write_bytes(raw)
                write(directory / 'response.http.json', http)
                if terminal['valid_format']:
                    write(directory / 'parsed.native.json', parsed['native'])
                    write(directory / 'hydrated.json', parsed['hydrated'])
            self.requests.append(row)
            self.mapping.append(meta)
            shards[host].append(row)
        prepared = self.snapshot / 'prepared'
        prepared.mkdir()
        def jsonl(name, values):
            (prepared / name).write_bytes(b''.join(r.canonical(x) + b'\n' for x in values))
            return {'path': name, 'count': len(values), 'sha256': r.file_sha(prepared / name)}
        self.manifest = {'state': 'frozen_not_sent', 'request_count': 4, 'provisional': True,
            'protocol_id': 'dvj-H1-H5-full-sentence-id-provisional-v1', 'input_sha256': r.SOURCE_SHA,
            'excluded_sha256': r.EXCLUDED_SHA, 'dirty_cases_sha256': r.DIRTY_SHA,
            'concurrency_per_endpoint': 128, 'retries': 0, 'length_retry': False, 'request_deadline_s': 1800,
            'reference_ready': False, 'legal_truth_verified': False, 'formal_hallucination_statistics_allowed': False,
            'parameters': self.parameters, 'prompt_sha256': r.sha(b'frozen system'),
            'shards': {h: jsonl('requests_' + h.rsplit('.', 1)[-1] + '.jsonl', shards[h]) for h in r.HOSTS},
            'private_mapping': jsonl('private_mapping.jsonl', self.mapping), 'reused': jsonl('reused.jsonl', self.reuse),
            'excluded_slots': 239, 'permanently_excluded_cases': 18}
        code = self.snapshot / 'code'
        code.mkdir()
        for name in (*r.CODE, 'manager.py'):
            (code / name).write_text('# inert fixture\n')
        shutil.copyfile(REPO / 'scripts/hallucination_h1_h5_sentence_ids_20261003/contract.py', code / 'sentence_contract.py')
        shutil.copyfile(REPO / 'scripts/cluster/v4_flash_20261002_autotune/bench.py', code / 'bench.py')
        self.manifest['code_sha256'] = {name: r.file_sha(code / name) for name in r.CODE}
        self.manifest['sentence_contract_sha256'] = self.manifest['code_sha256']['sentence_contract.py']
        write(prepared / 'manifest.json', self.manifest)
        write(self.snapshot / 'stage_manifest.json', {'files': {name: r.file_sha(self.snapshot / name) for name in r.FILES}})
        session = self.snapshot / 'sessions/session01'
        write(session / 'start.json', {'session': 'session01', 'manifest_sha256': r.file_sha(prepared / 'manifest.json')})
        for root in [session, *(session / h for h in r.HOSTS)]:
            report = {'drained': True, 'complete': True}
            write(root / 'round.json', report)
            write(root / 'exit.json', {'drained': True, 'local_futures_finished': True, 'corpus_complete': True,
                                     'all_inputs_accounted': True, 'round_sha256': r.sha(r.canonical(report))})
        write(self.snapshot / 'latest_session.json', {'session': 'session01'})

    def attempt(self, index):
        row, meta = self.requests[index], self.mapping[index]
        return self.snapshot / 'sessions/session01' / meta['host'] / 'requests' / f"{index:06d}_{r.sha(row['request_id'].encode())[:16]}"

    def test_complete_report_unique_counts_and_exact_quotes(self):
        result = r.audit(self.snapshot, self.output)
        self.assertEqual(result['overall']['slots'], 4)
        self.assertEqual(result['overall']['statuses'], {'events_candidate': 1, 'none_candidate': 1, 'invalid_format': 1, 'truncated': 1})
        self.assertEqual(result['overall']['candidate_events'], 1)
        self.assertFalse(result['none_is_verified_negative'])
        self.assertFalse(result['formal_hallucination_statistics_allowed'])
        final = list(r.rows(self.output / 'provisional_results.jsonl'))
        self.assertEqual(len({x['request_id'] for x in final}), 4)
        self.assertEqual(next(x for x in final if x['source'] == 'pilot_reuse')['hydrated']['events'][0]['quote'], '原始事实。')

    def test_missing_exit_prevents_final_output(self):
        (self.snapshot / 'sessions/session01/exit.json').unlink()
        with self.assertRaisesRegex(ValueError, 'real finished/drained'):
            r.audit(self.snapshot, self.output)
        self.assertFalse(self.output.exists())

    def test_partial_unknown_and_unstarted_are_separate(self):
        (self.attempt(1) / 'terminal.json').unlink()
        shutil.rmtree(self.attempt(3))
        result = r.audit(self.snapshot, self.output, allow_partial=True)
        self.assertEqual(result['report_state'], 'partial_provisional_snapshot')
        self.assertEqual(result['overall']['statuses']['needs_reconcile'], 1)
        self.assertEqual(result['overall']['statuses']['unstarted'], 1)
        self.assertEqual(result['output_files']['needs_reconcile.jsonl']['rows'], 1)
        self.assertEqual(result['output_files']['unstarted.jsonl']['rows'], 1)

    def test_missing_terminal_does_not_become_none(self):
        (self.attempt(1) / 'terminal.json').unlink()
        result = r.audit(self.snapshot, self.output, allow_partial=True)
        self.assertEqual(result['overall']['statuses']['none_candidate'], 0)
        self.assertEqual(result['overall']['statuses']['needs_reconcile'], 1)

    def test_raw_response_tampering_rejected(self):
        (self.attempt(1) / 'response.raw.txt').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'receipt digest'):
            r.audit(self.snapshot, self.output)

    def test_wrong_cached_hydration_rejected(self):
        write(self.attempt(1) / 'hydrated.json', {'faked': True})
        with self.assertRaisesRegex(ValueError, 'Cached parsed/hydrated'):
            r.audit(self.snapshot, self.output)

    def test_payload_change_rejected(self):
        path = self.attempt(1) / 'intent.json'
        value = r.read(path)
        value['payload']['temperature'] = 1
        value['payload_sha256'] = r.sha(r.canonical(value['payload']))
        write(path, value)
        with self.assertRaisesRegex(ValueError, 'Actual payload differs'):
            r.audit(self.snapshot, self.output)

    def test_terminal_label_tampering_rejected(self):
        path = self.attempt(1) / 'terminal.json'
        value = r.read(path)
        value['valid_format'] = False
        write(path, value)
        with self.assertRaisesRegex(ValueError, 'Stored terminal differs'):
            r.audit(self.snapshot, self.output)

    def test_duplicate_reuse_attempt_rejected(self):
        row, meta = self.requests[0], self.mapping[0]
        directory = self.snapshot / 'sessions/session01' / meta['host'] / 'requests' / f"000000_{r.sha(row['request_id'].encode())[:16]}"
        directory.mkdir()
        with self.assertRaisesRegex(ValueError, 'Duplicate attempt'):
            r.audit(self.snapshot, self.output)

    def test_frozen_input_tampering_rejected(self):
        with (self.snapshot / 'prepared/requests_146.jsonl').open('ab') as stream:
            stream.write(b'\n')
        with self.assertRaisesRegex(ValueError, 'Staged artifact changed'):
            r.audit(self.snapshot, self.output)

    def test_output_never_overwrites_snapshot(self):
        with self.assertRaisesRegex(ValueError, 'must not mutate frozen'):
            r.audit(self.snapshot, self.snapshot / 'report')

    def test_final_unknown_cannot_pass_even_if_receipt_claims_complete(self):
        (self.attempt(1) / 'terminal.json').unlink()
        with self.assertRaisesRegex(ValueError, 'unknown/unstarted'):
            r.audit(self.snapshot, self.output)
        self.assertFalse((self.output / 'summary.json').exists())


if __name__ == '__main__':
    unittest.main()
