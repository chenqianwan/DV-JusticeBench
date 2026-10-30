#!/usr/bin/env python3
"""Preserve the first run and freeze only a client-wait extension, without API calls."""
import copy
import json
from pathlib import Path
from prepare_answer_smoke import sha, write_once

ROOT = Path(__file__).resolve().parents[3] / 'data/pkulaw_v4_answer_smoke_20260927'
OUT = ROOT.with_name(ROOT.name + '_wait900')


def main():
    original = json.loads((ROOT/'input_tasks.json').read_text())
    previous = json.loads((ROOT/'input_manifest.json').read_text())
    assert sha((ROOT/'input_tasks.json').read_bytes()) == previous['input_tasks_sha256']
    extended = copy.deepcopy(original)
    extended.update(run_id=OUT.name, timeout_s=900)
    changed = [k for k in original if original[k] != extended[k]]
    assert set(changed) == {'run_id', 'timeout_s'} and extended['tasks'] == original['tasks']
    manifest = copy.deepcopy(previous)
    manifest.update(parent_run=ROOT.name, parent_input_sha256=previous['input_tasks_sha256'],
                    adjustment={'fields': changed, 'timeout_s_before': 180, 'timeout_s_after': 900,
                                'reason': 'Local service is generating normally but 180-second nonstream requests time out.',
                                'sampling_and_prompts_changed': False},
                    token_accounting_note='Earlier timed-out requests have no response usage; report them separately, never as zero generation.')
    OUT.mkdir(exist_ok=True)
    write_once(OUT/'input_tasks.json', extended)
    manifest['input_tasks_sha256'] = sha((OUT/'input_tasks.json').read_bytes())
    write_once(OUT/'input_manifest.json', manifest)
    print(json.dumps({'output':str(OUT),'changed_fields':changed,
                      'input_tasks_sha256':manifest['input_tasks_sha256'],'api_calls':0},ensure_ascii=False))


if __name__ == '__main__':
    main()
