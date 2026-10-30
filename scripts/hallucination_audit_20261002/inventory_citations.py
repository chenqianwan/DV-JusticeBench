#!/usr/bin/env python3
"""Offline citation-candidate inventory, not a legal-source or hallucination audit.

This intentionally conservative extractor neither resolves source aliases nor
checks whether a source, provision, or asserted legal proposition is true.
"""
import argparse
from bisect import bisect_right
from collections import Counter
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import uuid


REPO = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO / 'data/pkulaw_hallucination_audit_20261002/answers.jsonl'
DEFAULT_OUTPUT = REPO / 'outputs/pkulaw_hallucination_audit_20261002'
KEY_FIELDS = ('answer_condition', 'split', 'case_id', 'question_id')
NUMERAL = r'[0-9０-９〇零一二三四五六七八九十百千万亿两壹贰叁肆伍陆柒捌玖拾佰仟]+'
LOCATOR_RE = re.compile(r'第[ \t]*(' + NUMERAL + r')[ \t]*(条|款|项|目)'
                        r'(?:[ \t]*之[ \t]*(' + NUMERAL + r'))?')
BOUNDARY_RE = re.compile(r'[。！？!?；;\r\n.]+')
DIRECT_BRIDGE_RE = re.compile(r'(?:[ \t*_：:，,、（）()]|的|规定|中|之|所|相关|有关|依据|根据|参照|适用)*')
LIST_BRIDGE_RE = re.compile(r'[ \t*_、，,及和与或至到\-—–~～]*')
TITLE_LIST_BRIDGE_RE = re.compile(r'[ \t*_、，,及和与或]+')
LEGAL_TITLE_HINT_RE = re.compile(r'(?:法典|宪法|法|条例|规定|办法|司法解释|解释|决定|通知|意见|纪要|规则|准则|规程)(?:[（(][^（）()]*[）)])?$')
RANK = {'条': 0, '款': 1, '项': 2, '目': 3}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha256(value):
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def stream_fingerprint(stream):
    checksum, size = hashlib.sha256(), 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        checksum.update(chunk)
        size += len(chunk)
    return {'sha256': checksum.hexdigest(), 'bytes': size}


def gzip_artifact(path, final_path):
    with Path(path).open('rb') as source:
        compressed = stream_fingerprint(source)
    with gzip.open(path, 'rb') as source:
        uncompressed = stream_fingerprint(source)
    return dict(compressed, path=str(final_path), compression='gzip',
                uncompressed_sha256=uncompressed['sha256'], uncompressed_bytes=uncompressed['bytes'])


def write_gzip_json(path, result):
    with Path(path).open('xb') as destination:
        with gzip.GzipFile(filename='', fileobj=destination, mode='wb', compresslevel=1, mtime=0) as zipped:
            with io.TextIOWrapper(zipped, encoding='utf-8', newline='\n') as output:
                json.dump(result, output, ensure_ascii=False, sort_keys=True, indent=2)
                output.write('\n')


def small_summary(result, inventory_artifact):
    """No representative text/provenance repetition in the user-facing summary."""
    return {key: result[key] for key in ('schema_version', 'scope', 'input', 'summary',
            'answer_count_by_answer_condition', 'answer_count_by_split', 'answer_count_by_condition_split')} | {
        'candidate_counts': {table: len(result[table]) for table in
                             ('title_candidates', 'locators_with_local_title', 'unresolved_locator_patterns')},
        'artifacts': {'inventory': inventory_artifact, 'occurrences': result['occurrence_file']},
        'top_title_candidates': [{key: row[key] for key in ('title_raw', 'candidate_kind', 'unique_answer_count')}
                                 for row in result['title_candidates'][:20]],
        'hallucination_classification_performed': False,
        'note': 'Literal candidates only. Counts are deduplicated within each answer; not hallucination counts or rates. gzip files restore the complete logical records losslessly.'}


def write_summary(path, summary):
    with Path(path).open('x', encoding='utf-8') as output:
        json.dump(summary, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write('\n')


def span(text, start, end):
    return {'start': start, 'end': end, 'text': text[start:end]}


def title_spans(text):
    """Preserve exact balanced 《...》 spans, including nested titles."""
    stack, matches, unmatched = [], [], 0
    for offset, char in enumerate(text):
        if char == '《':
            stack.append(offset)
        elif char == '》':
            if stack:
                start = stack.pop()
                if offset > start + 1:
                    matches.append((start, offset + 1))
            else:
                unmatched += 1
    matches.sort()
    return matches, unmatched + len(stack)


def title_kind(title):
    # Lexical hints are routing aids only; a novel could have the same suffix.
    return ('legal_source_title_candidate' if LEGAL_TITLE_HINT_RE.search(title)
            else 'book_title_candidate')


def locator_groups(text):
    groups = []
    for match in LOCATOR_RE.finditer(text):
        component = {'number_raw': match[1], 'unit': match[2],
                     'subnumber_raw': match[3], 'span': span(text, *match.span())}
        if (groups and RANK[groups[-1]['components'][-1]['unit']] < RANK[match[2]]
                and re.fullmatch(r'[ \t]*', text[groups[-1]['end']:match.start()])):
            groups[-1]['end'] = match.end()
            groups[-1]['components'].append(component)
        else:
            groups.append({'start': match.start(), 'end': match.end(), 'components': [component]})
    return groups


def extract_candidates(text):
    """Return offsets into the unmodified answer (Python Unicode code points)."""
    title_pairs, unbalanced = title_spans(text)
    titles = []
    for start, end in title_pairs:
        raw = text[start:end]
        titles.append({'candidate_kind': title_kind(raw[1:-1]),
                       'title_raw': raw, 'title_text_exact': raw[1:-1],
                       'span': span(text, start, end),
                       'candidate_id': 'title_' + sha256(raw)[:24]})
    boundaries = [match.end() for match in BOUNDARY_RE.finditer(text)]
    references, previous = [], None
    for group in locator_groups(text):
        start, end = group['start'], group['end']
        boundary_index = bisect_right(boundaries, start)
        lower = boundaries[boundary_index - 1] if boundary_index else 0
        eligible_titles = [title for title in titles
                           if lower <= title['span']['start'] and title['span']['end'] <= start]
        anchor = max(eligible_titles, key=lambda title: title['span']['end'], default=None)
        linked, reason, competing = None, 'no_local_title_anchor', []
        if anchor is not None:
            gap = text[anchor['span']['end']:start]
            if len(gap) <= 64 and DIRECT_BRIDGE_RE.fullmatch(gap):
                adjacent = [title for title in eligible_titles
                            if title['span']['end'] <= anchor['span']['start']
                            and TITLE_LIST_BRIDGE_RE.fullmatch(
                                text[title['span']['end']:anchor['span']['start']])]
                if adjacent:
                    competing = [title['title_raw'] for title in adjacent] + [anchor['title_raw']]
                    reason = 'multiple_adjacent_title_candidates'
                else:
                    linked, reason = anchor, 'direct_local_title_candidate'
            else:
                reason = 'nonlocal_or_ambiguous_title_gap'
        # An explicit enumeration may continue a directly anchored reference.
        # No prose, sentence boundary, or unlinked reference propagates a source.
        if linked is None and not competing and previous and previous['linked_title']:
            gap = text[previous['span']['end']:start]
            if (previous['span']['start'] >= lower and gap and len(gap) <= 24
                    and LIST_BRIDGE_RE.fullmatch(gap)):
                linked, reason = previous['linked_title'], 'explicit_same_clause_locator_list'
        components = group['components']
        signature = [{'number_raw': item['number_raw'], 'unit': item['unit'],
                      'subnumber_raw': item['subnumber_raw']} for item in components]
        raw_title = linked['title_raw'] if linked else None
        identity = {'title_raw': raw_title, 'locator_components': signature}
        reference = {'candidate_kind': 'provision_locator_candidate',
                     'candidate_id': 'locator_' + sha256(canonical(identity))[:24],
                     'span': span(text, start, end), 'components': components,
                     'locator_signature': signature,
                     'source_title_raw': raw_title,
                     'source_link_status': 'local_title_candidate' if linked else 'unresolved',
                     'source_link_reason': reason,
                     'competing_title_candidates': competing,
                     'article_anchor_status': ('article_in_locator' if any(c['unit'] == '条' for c in components)
                                               else 'unresolved_article_parent'),
                     'linked_title': linked}
        references.append(reference)
        previous = reference
    return titles, references, unbalanced


class Aggregate:
    def __init__(self, description, representative_limit):
        self.description = description
        self.representative_limit = representative_limit
        self.answer_keys = set()
        self.by_condition = Counter()
        self.by_split = Counter()
        self.by_condition_split = {}
        self.occurrences = 0
        self.representatives = []

    def add(self, occurrence):
        self.occurrences += 1
        key = tuple(occurrence['answer_key'][field] for field in KEY_FIELDS)
        if key in self.answer_keys:
            return
        self.answer_keys.add(key)
        condition, split = key[:2]
        self.by_condition[condition] += 1
        self.by_split[split] += 1
        self.by_condition_split.setdefault(condition, Counter())[split] += 1
        if len(self.representatives) < self.representative_limit:
            self.representatives.append(occurrence)

    def export(self):
        return dict(self.description, occurrence_count=self.occurrences,
                    unique_answer_count=len(self.answer_keys),
                    unique_answer_count_by_answer_condition=dict(sorted(self.by_condition.items())),
                    unique_answer_count_by_split=dict(sorted(self.by_split.items())),
                    unique_answer_count_by_condition_split={key: dict(sorted(value.items()))
                                                           for key, value in sorted(self.by_condition_split.items())},
                    representatives=self.representatives)


def inventory(input_path, output_dir, representative_limit=3):
    """Read immutable JSONL and create new inventory files; never overwrite."""
    if representative_limit < 1:
        raise ValueError('At least one representative is required')
    input_path, output_dir = Path(input_path).resolve(), Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    inventory_path = output_dir / 'citation_inventory.json.gz'
    occurrences_path = output_dir / 'citation_occurrences.jsonl.gz'
    summary_path = output_dir / 'citation_summary.json'
    if any(path.exists() for path in (inventory_path, occurrences_path, summary_path)):
        raise FileExistsError('Citation outputs already exist; choose a new output directory')
    token = uuid.uuid4().hex
    temp_occurrences = output_dir / ('.citation_occurrences.' + token + '.tmp')
    temp_inventory = output_dir / ('.citation_inventory.' + token + '.tmp')
    temp_summary = output_dir / ('.citation_summary.' + token + '.tmp')
    inputs_hash, occurrences_hash = hashlib.sha256(), hashlib.sha256()
    seen, tables = set(), {'title_candidates': {}, 'locators_with_local_title': {}, 'unresolved_locator_patterns': {}}
    totals, conditions, splits, condition_splits = Counter(), Counter(), Counter(), {}
    initial_stat = input_path.stat()
    published = []
    try:
        with input_path.open('rb') as source, temp_occurrences.open('xb') as compressed_output, \
                gzip.GzipFile(filename='', fileobj=compressed_output, mode='wb', compresslevel=1, mtime=0) as output:
            for line_number, raw_line in enumerate(source, 1):
                inputs_hash.update(raw_line)
                if not raw_line.strip():
                    totals['blank_input_lines'] += 1
                    continue
                row = json.loads(raw_line)
                if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] for k in KEY_FIELDS):
                    raise ValueError('Invalid answer key at input line ' + str(line_number))
                answer = row.get('answer')
                if not isinstance(answer, str) or not answer.strip() or row.get('main_denominator_eligible') is False:
                    raise ValueError('Non-eligible/empty answer at input line ' + str(line_number))
                if 'status' in row and row['status'] != 'complete':
                    raise ValueError('Non-complete answer at input line ' + str(line_number))
                key = tuple(row[field] for field in KEY_FIELDS)
                if key in seen:
                    raise ValueError('Duplicate answer key at input line ' + str(line_number))
                seen.add(key)
                answer_hash = sha256(answer)
                for hash_field in ('answer_sha256', 'answer_hash'):
                    if hash_field in row and row[hash_field] != answer_hash:
                        raise ValueError('Answer hash mismatch at input line ' + str(line_number))
                source_provenance = row.get('sources', {})
                if not isinstance(source_provenance, dict):
                    raise ValueError('Invalid source provenance at input line ' + str(line_number))
                provenance = {'answer_key': dict(zip(KEY_FIELDS, key)), 'answer_sha256': answer_hash,
                              'answer_characters': len(answer), 'corpus_input_line': line_number,
                              'corpus_record_sha256': sha256(raw_line), 'sources': source_provenance}
                titles, locators, unbalanced = extract_candidates(answer)
                totals['answers'] += 1
                totals['answers_with_candidates'] += bool(titles or locators)
                totals['answers_with_unbalanced_book_title_markers'] += bool(unbalanced)
                totals['unbalanced_book_title_markers'] += unbalanced
                conditions[key[0]] += 1
                splits[key[1]] += 1
                condition_splits.setdefault(key[0], Counter())[key[1]] += 1
                for item in titles + locators:
                    occurrence = dict(provenance, **item,
                                      authority_verification_status='not_verified',
                                      hallucination_status='not_assessed',
                                      assertion_polarity='not_classified')
                    location = item['span']
                    occurrence['occurrence_id'] = 'occ_' + sha256(canonical(
                        [key, answer_hash, item['candidate_kind'], location['start'], location['end']]))[:24]
                    occurrence['context'] = span(answer, max(0, location['start'] - 80),
                                                min(len(answer), location['end'] + 80))
                    if item['candidate_kind'] == 'provision_locator_candidate':
                        table = ('locators_with_local_title' if item['source_link_status'] == 'local_title_candidate'
                                 else 'unresolved_locator_patterns')
                        description = {field: item[field] for field in ('candidate_id', 'candidate_kind',
                            'source_title_raw', 'source_link_status', 'locator_signature', 'article_anchor_status')}
                    else:
                        table = 'title_candidates'
                        description = {field: item[field] for field in ('candidate_id', 'candidate_kind',
                                                                       'title_raw', 'title_text_exact')}
                    aggregate = tables[table].setdefault(item['candidate_id'], Aggregate(description, representative_limit))
                    aggregate.add(occurrence)
                    data = (canonical(occurrence) + '\n').encode('utf-8')
                    output.write(data)
                    occurrences_hash.update(data)
                    totals['uncompressed_occurrence_bytes'] += len(data)
                    totals['occurrences'] += 1
                    totals[table + '_occurrences'] += 1
        final_stat = input_path.stat()
        if (initial_stat.st_dev, initial_stat.st_ino, initial_stat.st_size, initial_stat.st_mtime_ns) != (
                final_stat.st_dev, final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns):
            raise RuntimeError('Input changed during citation inventory')
        occurrence_artifact = gzip_artifact(temp_occurrences, occurrences_path)
        if (occurrence_artifact['uncompressed_sha256'] != occurrences_hash.hexdigest()
                or occurrence_artifact['uncompressed_bytes'] != totals['uncompressed_occurrence_bytes']):
            raise RuntimeError('Compressed occurrence output does not reproduce the logical stream')
        result = {'schema_version': 1, 'scope': 'offline_literal_citation_candidates_only',
                  'input': {'path': str(input_path), 'sha256': inputs_hash.hexdigest(),
                            'bytes': final_stat.st_size, 'answer_count': len(seen)},
                  'extractor': {'path': str(Path(__file__).resolve()),
                                'sha256': sha256(Path(__file__).read_bytes()),
                                'offset_unit': 'Unicode code points; start inclusive, end exclusive',
                                'answer_key_fields': list(KEY_FIELDS),
                                'title_alias_merging': False, 'numeral_conversion': False,
                                'source_linking': 'direct local title or explicit same-clause locator list only; no cross-sentence carry',
                                'external_source_checks': False, 'model_requests': 0},
                  'limitations': [
                      'Candidate inventory is not a hallucination classification, legal-source validation, or exhaustive citation detector.',
                      'Every balanced book-title span is a candidate; legal-looking suffixes are lexical hints, not proof of legal authority.',
                      'Negated, quoted, hypothetical and example citations are retained without treating them as asserted legal rules.',
                      'Unresolved locator patterns can refer to different unknown sources; shared raw locators do not establish shared authority.',
                      'Spelled-out aliases, unbracketed sources, abbreviated/range citations and implicit contextual references may be missed.',
                      'Counts are deduplicated within each answer and each exact candidate; occurrence counts must not be used as hallucination rates.',
                      'Clause/item-only locators do not inherit an article number; original numeral spelling and exact title spelling are retained.'],
                  'summary': dict(totals),
                  'answer_count_by_answer_condition': dict(sorted(conditions.items())),
                  'answer_count_by_split': dict(sorted(splits.items())),
                  'answer_count_by_condition_split': {key: dict(sorted(value.items())) for key, value in sorted(condition_splits.items())},
                  'occurrence_file': dict(occurrence_artifact, count=totals['occurrences']),
                  'artifact_format': 'Lossless gzip; artifact sha256/bytes refer to compressed files. uncompressed_sha256/bytes fingerprint the restored logical JSON/JSONL.',
                  'representatives_per_candidate_limit': representative_limit}
        for table, values in tables.items():
            result[table] = sorted((aggregate.export() for aggregate in values.values()),
                                   key=lambda row: (-row['unique_answer_count'], row['candidate_id']))
        write_gzip_json(temp_inventory, result)
        summary = small_summary(result, gzip_artifact(temp_inventory, inventory_path))
        write_summary(temp_summary, summary)
        # Exclusive hard links avoid clobbering results created by another run.
        os.link(temp_occurrences, occurrences_path)
        published.append(occurrences_path)
        os.link(temp_inventory, inventory_path)
        published.append(inventory_path)
        os.link(temp_summary, summary_path)
        published.append(summary_path)
        return result
    except BaseException:
        for path in published:
            path.unlink()  # Only links created by this invocation, never prior outputs.
        raise
    finally:
        for path in (temp_occurrences, temp_inventory, temp_summary):
            if path.exists():
                path.unlink()


def compress_existing(output_dir, remove_uncompressed=False):
    """Lossless format migration; retain/update the existing detailed JSON.

Removal is permitted only for this task's one explicitly authorized 4.2 GB
derived occurrence path, and only after decompressed fingerprint verification.
"""
    output_dir = Path(output_dir).resolve()
    raw_occurrences = output_dir / 'citation_occurrences.jsonl'
    raw_inventory = output_dir / 'citation_inventory.json'
    if remove_uncompressed and raw_occurrences != DEFAULT_OUTPUT / 'citation_occurrences.jsonl':
        raise ValueError('Deletion is restricted to the authorized derived occurrence file')
    occurrences_path = output_dir / 'citation_occurrences.jsonl.gz'
    inventory_path = output_dir / 'citation_inventory.json.gz'
    summary_path = output_dir / 'citation_summary.json'
    if any(path.exists() for path in (occurrences_path, inventory_path, summary_path)):
        raise FileExistsError('Compressed inventory outputs already exist')
    result = json.loads(raw_inventory.read_text())
    old_occurrence = result['occurrence_file']
    if Path(old_occurrence['path']).resolve() != raw_occurrences or old_occurrence.get('compression'):
        raise ValueError('Unexpected original occurrence artifact')
    old_stat = raw_occurrences.stat()
    token = uuid.uuid4().hex
    temporary = {name: output_dir / ('.citation_migration.' + token + '.' + name)
                 for name in ('occ.gz', 'inventory.gz', 'inventory.json', 'summary.json')}
    try:
        checksum, size = hashlib.sha256(), 0
        with raw_occurrences.open('rb') as source, temporary['occ.gz'].open('xb') as destination:
            with gzip.GzipFile(filename='', fileobj=destination, mode='wb', compresslevel=1, mtime=0) as zipped:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    checksum.update(chunk)
                    size += len(chunk)
                    zipped.write(chunk)
        artifact = gzip_artifact(temporary['occ.gz'], occurrences_path)
        if not (checksum.hexdigest() == old_occurrence['sha256'] == artifact['uncompressed_sha256']
                and size == old_occurrence['bytes'] == artifact['uncompressed_bytes']):
            raise RuntimeError('Lossless gzip verification failed; keep original occurrence file')
        current_stat = raw_occurrences.stat()
        if (old_stat.st_ino, old_stat.st_mtime_ns, old_stat.st_size) != (
                current_stat.st_ino, current_stat.st_mtime_ns, current_stat.st_size):
            raise RuntimeError('Original occurrence file changed during compression')
        result['occurrence_file'] = dict(artifact, count=old_occurrence['count'])
        result['artifact_format'] = 'Lossless gzip; artifact sha256/bytes refer to compressed files. uncompressed_sha256/bytes fingerprint the restored logical JSON/JSONL.'
        result['format_migration'] = {'converter_sha256': sha256(Path(__file__).read_bytes()),
                                    'logical_candidate_records_changed': False,
                                    'original_occurrences_sha256': old_occurrence['sha256'],
                                    'original_occurrences_bytes': old_occurrence['bytes'],
                                    'uncompressed_occurrences_removed': remove_uncompressed,
                                    'recovery': 'Decompress citation_occurrences.jsonl.gz to recover the original JSONL byte-for-byte.',
                                    'existing_uncompressed_inventory_retained_and_updated': True}
        write_gzip_json(temporary['inventory.gz'], result)
        inventory_artifact = gzip_artifact(temporary['inventory.gz'], inventory_path)
        summary = small_summary(result, inventory_artifact)
        summary['format_migration'] = result['format_migration']
        write_summary(temporary['summary.json'], summary)
        write_summary(temporary['inventory.json'], result)
        os.link(temporary['occ.gz'], occurrences_path)
        os.link(temporary['inventory.gz'], inventory_path)
        os.link(temporary['summary.json'], summary_path)
        os.replace(temporary['inventory.json'], raw_inventory)
        # These are the only non-temporary bytes removed by this migration.
        # Exact target checked above; original corpus and manifest are untouched.
        if remove_uncompressed:
            raw_occurrences.unlink()
        return summary
    finally:
        for path in temporary.values():
            if path.exists():
                path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--representatives', type=int, default=3)
    parser.add_argument('--compress-existing', action='store_true',
                        help='Losslessly compress this task\'s existing derived outputs, then remove only its verified raw occurrence JSONL')
    args = parser.parse_args()
    if args.compress_existing:
        print(json.dumps(compress_existing(args.output_dir, remove_uncompressed=True), ensure_ascii=False, indent=2))
        return
    result = inventory(args.input, args.output_dir, args.representatives)
    print(json.dumps({'input_answers': result['input']['answer_count'],
                      'summary': result['summary'],
                      'title_candidates': len(result['title_candidates']),
                      'linked_locator_patterns': len(result['locators_with_local_title']),
                      'unresolved_locator_patterns': len(result['unresolved_locator_patterns']),
                      'inventory_path': str(args.output_dir.resolve() / 'citation_inventory.json.gz'),
                      'summary_path': str(args.output_dir.resolve() / 'citation_summary.json'),
                      'occurrence_path': result['occurrence_file']['path'],
                      'hallucination_classification_performed': False}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
