"""Read-only snapshot of benchmark receipt validity, grouped by answer source."""
from pathlib import Path
from datetime import datetime, timezone
import json
import shlex
import subprocess

REPO = Path(__file__).resolve().parents[3]
REMOTE = r'''
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
import importlib.util,json,time
mapping=json.load(__import__('sys').stdin)
root=Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_benchmark_20261003_c128')
input_path=Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_autotune_20261002/prepared/requests.jsonl')
answers={r['request_id']:r['answer'] for r in map(json.loads,input_path.read_text().splitlines())}
spec=importlib.util.spec_from_file_location('contract',root/'code/contract.py')
contract=importlib.util.module_from_spec(spec);spec.loader.exec_module(contract)
cutoff=time.time()
totals=Counter();endpoints={};groups={};mismatches=[]
def increment(c,terminal,reason):
 c['terminal']+=1;c['status:'+terminal['status']]+=1
 if terminal.get('complete'):c['complete']+=1
 if terminal.get('valid_format'):c['valid_format']+=1
 if terminal['status']=='complete_invalid_format':c['invalid_format']+=1;c['reason:'+reason]+=1
for host in ('10.33.4.146','10.33.4.152'):
 counter=Counter();endpoints[host]=counter
 paths=sorted((root/'results'/host/'requests').glob('*/terminal.json'))
 for path in paths:
  if path.stat().st_mtime>cutoff:continue
  terminal=json.loads(path.read_text());rid=terminal['request_id'];reason=None
  condition=mapping[rid]['answer_condition']
  if terminal['status']=='complete_invalid_format':
   response=json.loads((path.parent/'response.raw.txt').read_text())
   text=response['choices'][0]['message']['content']
   try:
    contract.parse_output(text,answers[rid]);reason='parser_now_passed';mismatches.append({'host':host,'request_id':rid})
   except json.JSONDecodeError:reason='json_syntax'
   except (ValueError,TypeError,KeyError) as error:
    reason='quote_not_verbatim' if str(error)=='Quote is not verbatim in original answer' else str(error)
  increment(counter,terminal,reason);increment(totals,terminal,reason)
  increment(groups.setdefault(condition,Counter()),terminal,reason)
exit_path=root/'results/exit.json'
print(json.dumps({'snapshot_at':datetime.fromtimestamp(cutoff,timezone.utc).isoformat(),
 'judge_model':'deepseek_v4_flash','per_endpoint_planned':1024,'endpoints':endpoints,
 'by_original_answer_condition':groups,'combined':totals,'parser_mismatches':mismatches,
 'exit_receipt':json.loads(exit_path.read_text()) if exit_path.exists() else None,
 'counts_are_responses_not_unique_answers':True,'formal_hallucination_statistics_allowed':False}))
'''


def main():
    source = REPO / 'data/pkulaw_v4_autotune_20261002/prepared/private_mapping.jsonl'
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    mapping = {row['request_id']: {'answer_condition': row['answer_condition']} for row in rows}
    assert len(mapping) == 1024
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=25',
                        'Group3_slogin', 'python3 -B -c ' + shlex.quote(REMOTE)],
                       input=json.dumps(mapping), capture_output=True, text=True, timeout=100)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'exit_code': p.returncode,
              'stderr': p.stderr}
    if p.returncode == 0:
        result['audit'] = json.loads(p.stdout)
    else:
        result['stdout'] = p.stdout
    output = REPO / 'data/pkulaw_admin_ds_benchmark_20261003_c128/format_audits'
    output.mkdir(exist_ok=True)
    destination = output / (stamp + '.json')
    with destination.open('x') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'record': str(destination), **result}, ensure_ascii=False, indent=2))
    raise SystemExit(p.returncode)


if __name__ == '__main__':
    main()
