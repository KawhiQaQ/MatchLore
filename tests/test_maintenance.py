import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from highlights.api import history_operation, ingest, import_history, make_server, reanalyze_history
from highlights.adapters import ADAPTERS
from highlights.engine import Miner
from highlights.errors import AppError
from highlights.preflight import check_data, check_batch
from highlights.store import Store, read_json
from highlights.verify import verify_response
from scripts.make_demo import make_demo, dota, football


class Maintenance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'data';make_demo(self.root)
        self.store=Store(self.root)

    def source(self,mode='dota2',index=0):
        raw,meta=(dota if mode=='dota2' else football)(index)
        return dict(mode=mode,raw=raw,**({'metadata':meta} if meta else {}))

    def replacement(self,mode='dota2',index=0):
        body=self.source(mode,index)
        if mode=='dota2':
            body['raw']['players'][0]['kills_log']=[{'time':1100+i} for i in range(12)]
            body['raw']['players'][0]['kills']=12
        else:body['raw'][0]['timestamp']='00:20:05.000'
        body.update(match_id=f'{"dota" if mode=="dota2" else "epl"}-history-{index:02d}',expected_revision=0,reason='Correct source events')
        return body

    def test_check_no_side_effects_and_multiple_errors(self):
        empty=Store(Path(self.tmp.name)/'absent')
        report=check_data(empty,self.source())
        self.assertTrue(report['valid']);self.assertTrue(report['can_ingest']);self.assertFalse(empty.root.exists())
        bad=self.source();bad['raw'].pop('match_id');bad['raw']['duration']=-1;bad['raw']['players'][0]['kills_log'][0]['time']=float('inf')
        report=check_data(empty,bad)
        self.assertFalse(report['valid']);self.assertGreaterEqual(len(report['errors']),3)
        self.assertFalse(empty.root.exists())

    def test_optional_coverage_and_identity_warnings(self):
        body=self.source();p=body['raw']['players'][0]
        p.pop('obs_log');p['account_id']=None;body['raw'].pop('objectives')
        report=check_data(self.store,body)
        self.assertTrue(report['valid'])
        self.assertEqual(report['coverage']['ObserverWard']['covered_players'],9)
        self.assertFalse(report['coverage']['TowerLoss']['available'])
        self.assertIn('missing_player_identity',[w['code'] for w in report['warnings']])
        # Malformed optional data degrades coverage without blocking core statistics.
        body['raw']['objectives']=[None]
        self.assertTrue(check_data(self.store,body)['valid'])

    def test_epl_clock_goals_ids_and_missing_shot_outcome(self):
        for mutate in [lambda b:b['raw'][0].update(timestamp='00:99:00'),
                       lambda b:b['metadata'].update(home_score=5),
                       lambda b:b['raw'].append(copy.deepcopy(b['raw'][0])),
                       lambda b:b['raw'][0].pop('shot')]:
            body=self.source('epl');mutate(body)
            report=check_data(self.store,body)
            self.assertFalse(report['valid']);self.assertTrue(report['errors'])
            with self.assertRaises(AppError):ingest(self.store,body)

    def test_check_identical_vs_conflict_and_batch(self):
        good=self.source();self.assertTrue(check_data(self.store,good)['can_ingest'])
        bad=self.source();bad['raw']['players'][0]['kills_log'][0]['time']=401
        self.assertTrue(check_data(self.store,bad)['valid']);self.assertFalse(check_data(self.store,bad)['can_ingest'])
        empty=Store(Path(self.tmp.name)/'empty')
        report=check_batch(empty,{'matches':[good,bad]})
        self.assertFalse(report['can_ingest'])
        self.assertTrue(all(not r['can_ingest'] for r in report['results']))
        self.assertFalse(empty.root.exists())

    def test_replace_base_preserves_files_and_audits(self):
        base=self.root/'dota2/matches/dota-history-00.json.gz';original=base.read_bytes()
        body=self.replacement();result=history_operation(self.store,body,'replace')
        self.assertEqual(result['status'],'replaced');self.assertIn('dota-demo',result['affected_matches'])
        self.assertEqual(base.read_bytes(),original)
        state=self.store.history_status('dota2',body['match_id'])
        self.assertEqual(state['revision'],result['revision']);self.assertEqual(state['changes'][0]['reason'],body['reason'])
        self.assertEqual(len(self.store.match('dota2',body['match_id'])['events']),14)
        with sqlite3.connect(self.store.ledger_path) as db:
            old,new=db.execute('SELECT before_payload,after_payload FROM changes').fetchone()
            self.assertNotEqual(json.loads(old)['events'],json.loads(new)['events'])
        with self.assertRaises(AppError) as error:history_operation(self.store,body,'replace')
        self.assertEqual(error.exception.code,'revision_conflict')

    def test_withdraw_restore_no_accidental_reingestion(self):
        mid='dota-history-00';withdraw=dict(mode='dota2',match_id=mid,expected_revision=0,reason='Bad source')
        result=history_operation(self.store,withdraw,'withdraw')
        self.assertNotIn(mid,[m['match_id'] for m in self.store.corpus('dota2')])
        self.assertEqual(self.store.history_status('dota2',mid)['status'],'withdrawn')
        with self.assertRaises(AppError) as error:ingest(self.store,self.source())
        self.assertEqual(error.exception.code,'match_withdrawn')
        report=check_data(self.store,self.source());self.assertFalse(report['can_ingest'])
        body=self.replacement();body['expected_revision']=result['revision']
        restored=history_operation(self.store,body,'replace')
        self.assertEqual(restored['status'],'restored')
        self.assertEqual(self.store.match('dota2',mid)['role'],'historical')
        self.assertEqual(len(self.store.history_status('dota2',mid)['changes']),2)

    def test_development_restore_retains_role(self):
        result=self.store.change('dota2','dota-demo',0,'Withdraw demo')
        raw,_=dota(30,True)
        m=ADAPTERS['dota2'](raw)
        self.store.change('dota2','dota-demo',result['revision'],'Restore demo',m)
        self.assertEqual(self.store.match('dota2','dota-demo')['role'],'development')

    def test_replace_rejects_wrong_id_and_empty_reason(self):
        body=self.replacement();body['match_id']='dota-history-01'
        with self.assertRaises(AppError):history_operation(self.store,body,'replace')
        body=self.replacement();body['reason']=' '
        with self.assertRaises(AppError):history_operation(self.store,body,'replace')
        self.assertEqual(self.store.history_status('dota2','dota-history-00')['revision'],0)

    def test_batch_rollback_includes_change_log(self):
        empty=Store(Path(self.tmp.name)/'empty')
        one=self.source();bad=copy.deepcopy(one);bad['raw']['players'][0]['kills_log'][0]['time']=402
        with self.assertRaises(AppError):import_history(empty,{'matches':[one,bad]})
        self.assertEqual(empty.corpus('dota2'),[])
        with sqlite3.connect(empty.ledger_path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM changes').fetchone()[0],0)

    def test_concurrent_revision_writers_only_one_wins(self):
        barrier=threading.Barrier(2)
        def update(i):
            body=self.replacement();body['raw']['radiant_name']=f'Corrected {i}'
            store=Store(self.root);barrier.wait()
            try:return history_operation(store,body,'replace')['status']
            except AppError as exc:return exc.code
        with ThreadPoolExecutor(2) as executor:results=list(executor.map(update,[1,2]))
        self.assertCountEqual(results,['replaced','revision_conflict'])

    def test_old_database_migrates_without_data_loss(self):
        other=Store(Path(self.tmp.name)/'legacy');other.root.mkdir()
        m=ADAPTERS['dota2'](dota(0)[0]);m['role']='committed'
        with sqlite3.connect(other.ledger_path) as db:
            db.execute('CREATE TABLE matches (mode TEXT,match_id TEXT,fingerprint TEXT,payload TEXT,PRIMARY KEY(mode,match_id))')
            db.execute('INSERT INTO matches VALUES (?,?,?,?)',('dota2',m['match_id'],other.fingerprint(m),json.dumps(m)))
        self.assertEqual(other.history_status('dota2',m['match_id'])['revision'],0)
        self.assertEqual(other.ingest(m)['status'],'already_present')
        self.assertEqual(len(other.corpus('dota2')),1)
        self.assertEqual(other.change('dota2',m['match_id'],0,'Withdraw old data')['status'],'withdrawn')

    def test_long_lived_miner_sees_corrected_content_and_withdrawal(self):
        for mode,mid in [('epl','epl-demo'),('dota2','dota-demo')]:
            miner=Miner(self.store);before=miner.analyze(mode,mid,30)
            result=history_operation(Store(self.root),self.replacement(mode),'replace')
            after=miner.analyze(mode,mid,30);fresh=Miner(Store(self.root)).analyze(mode,mid,30)
            self.assertEqual(after['cards'],fresh['cards']);verify_response(self.store,after)
            self.assertNotEqual(before['provenance']['history_fingerprint'],after['provenance']['history_fingerprint'])
            self.assertEqual(len(miner._cubes),2)
            self.store.change(mode,self.replacement(mode)['match_id'],result['revision'],'Withdraw corrected')
            withdrawn=miner.analyze(mode,mid,30)
            self.assertEqual(withdrawn['context']['history_matches'],before['context']['history_matches']-1)
            self.assertEqual(withdrawn['cards'],Miner(Store(self.root)).analyze(mode,mid,30)['cards'])

    def test_reanalysis_uses_current_snapshot_and_skips_withdrawn_match(self):
        result=self.store.change('dota2','dota-history-00',0,'Withdraw first history')
        report=reanalyze_history(self.store,dict(mode='dota2',revision=result['revision'],as_of_minute=30))
        self.assertEqual(len(report['results']),20)
        self.assertNotIn('dota-history-00',[r['match_id'] for r in report['results']])
        final=next(r for r in report['results'] if r['match_id']=='dota-demo')
        self.assertEqual(final['cards'],Miner(self.store).analyze('dota2','dota-demo',30)['cards'])
        self.assertEqual(report['llm'],'off')
        skipped=reanalyze_history(self.store,dict(mode='dota2',revision=result['revision'],as_of_minute=50))
        self.assertEqual(len(skipped['skipped']),20);self.assertEqual(skipped['results'],[])
        with self.assertRaises(AppError):reanalyze_history(self.store,dict(mode='epl',revision=result['revision']))

    def test_corrected_development_can_be_committed(self):
        raw,_=dota(30,True);raw['radiant_name']='Corrected Demo'
        body=dict(mode='dota2',match_id='dota-demo',raw=raw,expected_revision=0,reason='Correct name')
        history_operation(self.store,body,'replace')
        result=ingest(self.store,dict(mode='dota2',match_id='dota-demo'))
        self.assertEqual(result['status'],'committed')
        self.assertEqual(ingest(self.store,dict(mode='dota2',match_id='dota-demo'))['status'],'already_present')

    def test_cli_check_exit_codes_and_maintenance(self):
        def run(*args):return subprocess.run([sys.executable,'-m','highlights',*args,'--data',str(self.root)],capture_output=True,text=True)
        raw=self.root/'dota2/raw/dota-history-00.json'
        good=run('check','--mode','dota2','--raw',str(raw));self.assertEqual(good.returncode,0,good.stderr)
        bad=Path(self.tmp.name)/'bad.json';bad.write_text('{}')
        fail=run('check','--mode','dota2','--raw',str(bad));self.assertEqual(fail.returncode,2);self.assertFalse(json.loads(fail.stdout)['valid'])
        state=run('history-status','--mode','dota2','--match-id','dota-history-00');self.assertEqual(json.loads(state.stdout)['revision'],0)
        result=run('withdraw','--mode','dota2','--match-id','dota-history-00','--expected-revision','0','--reason','Test withdrawal')
        self.assertEqual(result.returncode,0,result.stderr)
        rev=json.loads(result.stdout)['revision']
        result=run('replace','--mode','dota2','--match-id','dota-history-00','--expected-revision',str(rev),'--reason','Restore','--raw',str(raw))
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['status'],'restored')
        recomputed=run('reanalyze','--mode','dota2','--revision',str(rev),'--minute','30')
        self.assertEqual(recomputed.returncode,0,recomputed.stderr)
        self.assertEqual(json.loads(recomputed.stdout)['status'],'completed')
        batch=run('check','--mode','dota2','--manifest',str(self.root/'dota2/history.json'))
        self.assertEqual(batch.returncode,0,batch.stderr)

    def test_http_check_auth_revision_conflict_and_restore(self):
        server=make_server(self.store,port=0,api_key='test-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def post(path,body,auth=True):
            headers={'Content-Type':'application/json'}
            if auth:headers['Authorization']='Bearer test-token'
            return json.load(urlopen(Request(f'http://127.0.0.1:{server.server_port}'+path,json.dumps(body).encode(),headers)))
        try:
            with self.assertRaises(HTTPError) as exc:post('/v1/check',self.source(),False)
            self.assertEqual(exc.exception.code,401)
            self.assertTrue(post('/v1/check',self.source())['can_ingest'])
            self.assertTrue(post('/v1/history/check',{'matches':[self.source()]})['can_ingest'])
            key=dict(mode='dota2',match_id='dota-history-00')
            state=post('/v1/history/status',key);self.assertEqual(state['revision'],0)
            changed=post('/v1/history/replace',self.replacement())
            with self.assertRaises(HTTPError) as exc:post('/v1/history/replace',self.replacement())
            self.assertEqual(exc.exception.code,409)
            withdrawn=post('/v1/history/withdraw',dict(**key,expected_revision=changed['revision'],reason='Withdraw'))
            restored=self.replacement();restored['expected_revision']=withdrawn['revision']
            self.assertEqual(post('/v1/history/replace',restored)['status'],'restored')
            recomputed=post('/v1/history/reanalyze',dict(mode='dota2',revision=withdrawn['revision']))
            self.assertEqual(recomputed['status'],'completed')
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
