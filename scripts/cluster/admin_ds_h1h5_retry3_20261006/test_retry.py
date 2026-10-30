"""Offline safety tests: no cluster connection or model calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('retry',Path(__file__).with_name('retry.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class RetryTests(unittest.TestCase):
    def test_only_confirmed_failures_selected(self):
        row={'audit_state':'invalid_format','terminal_status':'complete_invalid_format',
             'observed_raw_parse':{'status':'complete_invalid_format','http_status':200,'valid_format':False}}
        self.assertTrue(r.selected_failure(row))
        for field,value in [('audit_state','valid_candidate'),('terminal_status','timeout_needs_reconcile')]:
            self.assertFalse(r.selected_failure({**row,field:value}))
        for value in (None,503):
            self.assertFalse(r.selected_failure({**row,'observed_raw_parse':{**row['observed_raw_parse'],'http_status':value}}))

    def test_retry_bound_and_ambiguity(self):
        t={'status':'truncated','http_status':200,'valid_format':False,'sent_intent':True}
        self.assertTrue(r.can_retry(t,1));self.assertTrue(r.can_retry(t,2));self.assertFalse(r.can_retry(t,3))
        for change in ({'status':'complete'},{'status':'timeout_needs_reconcile'},
                       {'service_error':True},{'timeout':True},{'sent_intent':False},{'http_status':500}):
            self.assertFalse(r.can_retry({**t,**change},1))
        self.assertTrue(r.valid_success({'status':'complete','complete':True,'valid_format':True,'format_kind':'NONE'}))

    def test_controller_first_success_and_three_attempt_limit(self):
        base=r.load_base()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prepared=root/'prepared';prepared.mkdir()
            # Reuse one genuine immutable input, replacing only test identities.
            original=next(r.rows(r.OLD_LOCAL/'prepared/requests_152.jsonl'))[1]
            records=[{**original,'request_id':f'offline{i}','global_index':i} for i in range(3)]
            raw=b''.join(r.canonical(v)+b'\n' for v in records)
            (prepared/'requests.jsonl').write_bytes(raw)
            plan={'selection':[{'request_id':v['request_id']} for v in records],'input_sha256':r.digest(raw)}
            (root/'plan.json').write_text(json.dumps(plan))
            calls=[]
            def endpoint(root,phase,host,manifest,seen,gate):
                number=int(phase.name.split('_')[-1]);(phase/'requests').mkdir()
                for _,row in r.rows(root/'prepared'/manifest['shards'][host]['path']):
                    calls.append((number,row['request_id']))
                    ok=row['request_id']=='offline0' or (row['request_id']=='offline1' and number>=2)
                    response=r.canonical({'model':'deepseek_v4_flash','choices':[{'finish_reason':'stop','message':{'content':'NONE' if ok else 'bad'}}]})
                    with patch.object(base.ext,'http_once',return_value=(200,{},response)):
                        base.pilot.request_once(row,row['global_index'],phase,r.URL,gate)
                return {'drained':True,'local_futures_finished':True,'in_flight':0,'untouched':0,'worker_errors':[],'failure':None}
            with patch.object(r,'ROOT',root),patch.object(r,'OLD',root),patch.object(r,'EXPECTED',3),\
                 patch.object(r,'safe_root'),patch.object(r,'verify_code'),patch.object(r,'verify_input'),\
                 patch.object(r,'old_finished',return_value={}),patch.object(r,'load_base',return_value=base),\
                 patch.object(base,'coordinator',side_effect=lambda *a,**kw:__import__('contextlib').nullcontext()),\
                 patch.object(base.pilot,'read_identity',return_value={'pid':999,'start_ticks':'1'}),\
                 patch.object(base,'run_endpoint',side_effect=endpoint),patch.object(r.time,'sleep'):
                r.run('offline20261006')
            self.assertEqual(calls,[(1,'offline0'),(1,'offline1'),(1,'offline2'),(2,'offline1'),(2,'offline2'),(3,'offline2')])
            result=r.read(root/'sessions/offline20261006/exit.json')
            self.assertEqual(result['state'],'completed');self.assertEqual(result['resolved'],2)
            self.assertEqual(result['remaining_exceptions'],1);self.assertEqual(result['new_requests'],6)
            self.assertFalse(result['formal_hallucination_statistics_allowed'])

    def test_refuse_evidence_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'evidence';r.new_bytes(p,b'a');r.new_bytes(p,b'a')
            with self.assertRaises(RuntimeError):r.new_bytes(p,b'b')

if __name__=='__main__':unittest.main()
