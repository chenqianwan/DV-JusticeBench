#!/usr/bin/env python3
"""Prepare the original 108 cases for classification, offline and non-destructively."""
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/cases/cases.json'
PROMPT = ROOT / 'prompts/dv_relevance_screening_zh.txt'
OUT = ROOT / 'data/original108_dv_classification_20261002'
EXPECTED_SOURCE_SHA = '4d27d86737fdf23a6ef96b4bdc8d80fde5e617661f1cf2bd1f1665f7e41c027e'

def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()

def prepare(source_bytes, template):
    assert sha(source_bytes) == EXPECTED_SOURCE_SHA, 'Original source changed; audit before proceeding'
    cases = json.loads(source_bytes)
    assert len(cases) == 108
    pattern = re.compile(r'\{(title|case_text|judge_decision)\}')
    assert Counter(pattern.findall(template)) == {'title': 1, 'case_text': 1, 'judge_decision': 1}
    tasks = []
    for cid, case in cases.items():
        assert cid.startswith('case_') and (case.get('id') in (None, cid))
        assert not (case.get('content') and case.get('case_text')), 'Ambiguous source fields'
        field = 'content' if case.get('content') else 'case_text'
        inputs = {'title': case['title'], 'case_text': case[field], 'judge_decision': case['judge_decision']}
        assert all(isinstance(v, str) and v.strip() for v in inputs.values())
        # One-pass substitution leaves all original text, braces and JSON instructions intact.
        prompt = pattern.sub(lambda m: inputs[m.group(1)], template)
        messages = [{'role': 'user', 'content': prompt}]
        tasks.append({'case_id': cid, **inputs, 'case_text_source_field': field,
                      'source_hashes': {k: sha(v) for k, v in inputs.items()},
                      'keyword_hit': any(w in ''.join(inputs.values()) for w in ('家庭暴力', '家暴')),
                      'messages': messages, 'messages_sha256': sha(canonical(messages))})
    assert len({t['case_id'] for t in tasks}) == 108
    return tasks

def main():
    source_bytes, prompt_bytes = SOURCE.read_bytes(), PROMPT.read_bytes()
    tasks = prepare(source_bytes, prompt_bytes.decode('utf-8'))
    records = b''.join(canonical(t) + b'\n' for t in tasks)
    manifest = {
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'state': 'prepared_not_started_awaiting_model_route',
        'source': str(SOURCE.relative_to(ROOT)), 'source_sha256': sha(source_bytes),
        'prompt': str(PROMPT.relative_to(ROOT)), 'prompt_sha256': sha(prompt_bytes),
        'tasks_sha256': sha(records), 'cases': 108,
        'fields': dict(Counter(t['case_text_source_field'] for t in tasks)),
        'keyword_hit': sum(t['keyword_hit'] for t in tasks), 'keyword_screening_applied': False,
        'input_truncated': False, 'labels': ['primary', 'secondary', 'irrelevant'],
        'earlier_engineering_group_rule': 'primary OR (secondary AND court_addressed_dv=true) -> primary; remaining secondary -> secondary; irrelevant -> irrelevant',
        'raw_label_must_be_preserved': True,
        'prior_classifier_parameters': {'temperature': 0.1, 'initial_max_tokens': 512,
                                        'length_retry_max_tokens': 1024, 'use_thinking': False},
        'model_route_selected': None, 'model_calls': 0,
        'source_limitation': 'Original stored pipeline materials; some cases are summaries/protection orders/rulings, not certified full judgments.',
    }
    OUT.mkdir(exist_ok=False)
    (OUT / 'input_cases.jsonl').write_bytes(records)
    (OUT / 'prompt.txt').write_bytes(prompt_bytes)
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: manifest[k] for k in ['state', 'cases', 'fields', 'keyword_hit', 'input_truncated', 'model_calls']}, ensure_ascii=False))

if __name__ == '__main__':
    main()
