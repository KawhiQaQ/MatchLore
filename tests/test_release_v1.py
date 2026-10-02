"""Regressions for the four functional issues found before the 1.0 release."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from highlights import __version__
from highlights.__main__ import main
from highlights.adapters import dota2
from highlights.api import dispatch, ingest, import_history, history_operation, make_server
from highlights.console import Console
from highlights.contracts import openapi
from highlights.engine import Miner
from highlights.errors import AppError
from highlights.preflight import check_data
from highlights.store import Store, SnapshotStore
from scripts.make_demo import make_demo, dota, football


class ReleaseV1(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'data';make_demo(self.root)
        self.store=Store(self.root)

    def test_raw_analysis_rejects_missing_outcomes_before_mining(self):
        raw,meta=football(30,True)
        for event in raw:
            if event['type']['name']=='Shot':event.pop('shot')
        body=dict(mode='epl',raw=raw,metadata=meta)
        self.assertFalse(check_data(self.store,body)['valid'])
        with patch.object(Miner,'analyze') as mine:
            with self.assertRaises(AppError) as exc:dispatch(Miner(self.store),body)
        self.assertEqual(exc.exception.code,'invalid_source');mine.assert_not_called()
        # The CLI uses the same route and exits without printing a false fact.
        raw_path=Path(self.tmp.name)/'events.json';meta_path=Path(self.tmp.name)/'metadata.json'
        raw_path.write_text(json.dumps(raw));meta_path.write_text(json.dumps(meta))
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err),self.assertRaises(SystemExit) as exc:
            main(['analyze','--data',str(self.root),'--mode','epl','--raw',str(raw_path),'--metadata',str(meta_path)])
        self.assertEqual(exc.exception.code,2);self.assertEqual(out.getvalue(),'')
        self.assertEqual(json.loads(err.getvalue())['error']['code'],'invalid_source')

    def test_dota_raw_analysis_requires_complete_match_like_ingestion(self):
        raw,_=dota(30,True);raw.pop('radiant_win')
        body=dict(mode='dota2',raw=raw)
        self.assertFalse(check_data(self.store,body)['valid'])
        with self.assertRaises(AppError):dispatch(Miner(self.store),body)

    def test_duplicate_identity_rejected_across_all_raw_entrypoints(self):
        for duplicate in (1000,'1000','0000001000'):
            with self.subTest(account_id=duplicate):
                raw,_=dota(0);raw['players'][5]['account_id']=duplicate
                body=dict(mode='dota2',raw=raw)
                report=check_data(self.store,body)
                self.assertFalse(report['valid']);self.assertFalse(report['can_ingest'])
                self.assertIn('raw.players[5].account_id',[e['path'] for e in report['errors']])
                with self.assertRaises(AppError):dispatch(Miner(self.store),body)
                with self.assertRaises(AppError):ingest(self.store,body)
                with self.assertRaises(AppError):history_operation(self.store,dict(body,match_id='dota-history-00',expected_revision=0,reason='Invalid correction'),'replace')
                with self.assertRaises(ValueError):dota2(raw)
        self.assertFalse(self.store.ledger_path.exists())

    def test_duplicate_identity_batch_never_partially_commits(self):
        store=Store(Path(self.tmp.name)/'new')
        one,_=dota(0);bad,_=dota(1);bad['players'][1]['account_id']=bad['players'][0]['account_id']
        with self.assertRaises(AppError):import_history(store,dict(matches=[dict(mode='dota2',raw=one),dict(mode='dota2',raw=bad)]))
        self.assertEqual(store.corpus('dota2'),[])
        self.assertFalse(store.ledger_path.exists())

    def test_anonymous_sentinels_remain_repeatable_and_numeric_strings_canonicalize(self):
        for sentinel in (None,0,4294967295,'0','4294967295'):
            with self.subTest(sentinel=sentinel):
                raw,_=dota(30,True)
                for p in raw['players']:p['account_id']=sentinel
                self.assertTrue(check_data(self.store,dict(mode='dota2',raw=raw))['valid'])
                self.assertTrue(all(p['identity'] is None for p in dota2(raw)['players'].values()))
                self.assertEqual(dispatch(Miner(self.store),dict(mode='dota2',raw=raw))['status'],'ok')
        raw,_=dota(0);raw['players'][0]['account_id']='0000001000'
        self.assertEqual(dota2(raw)['players']['0']['identity'],'1000')

    def test_invalid_account_id_types_are_reported(self):
        for invalid in (True,1.5,-1,4294967296,{},'not-an-id'):
            with self.subTest(value=invalid):
                raw,_=dota(0);raw['players'][0]['account_id']=invalid
                report=check_data(self.store,dict(mode='dota2',raw=raw))
                self.assertFalse(report['valid']);self.assertIn('account_id',report['errors'][0]['path'])

    def test_replay_survives_external_target_withdrawal_and_history_correction(self):
        for action in ('withdraw_target','replace_history'):
            with self.subTest(action=action):
                root=Path(self.tmp.name)/action;make_demo(root);store=Store(root)
                expected=Miner(store).replay('dota2','dota-demo',5)
                frame_ready=threading.Event();changed=threading.Event();errors=[]
                def writer():
                    try:
                        if not frame_ready.wait(10):raise RuntimeError('Replay did not reach first frame')
                        external=Store(root)
                        if action=='withdraw_target':external.change('dota2','dota-demo',0,'Concurrent withdrawal')
                        else:
                            raw,_=dota(0)
                            raw['players'][0]['kills_log']=[{'time':1210+i*12} for i in range(8)]
                            raw['players'][0]['kills']=8
                            history_operation(external,dict(mode='dota2',match_id='dota-history-00',raw=raw,expected_revision=0,reason='Concurrent correction'),'replace')
                    except Exception as exc:errors.append(exc)
                    finally:changed.set()
                thread=threading.Thread(target=writer,daemon=True);thread.start()
                original=Miner.analyze
                def analyzed(miner,*args,**kwargs):
                    result=original(miner,*args,**kwargs)
                    if not frame_ready.is_set():
                        frame_ready.set()
                        if not changed.wait(10):raise RuntimeError('Writer did not complete')
                    return result
                try:
                    with patch.object(Miner,'analyze',analyzed):actual=Miner(store).replay('dota2','dota-demo',5)
                finally:thread.join(10)
                self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
                self.assertEqual(actual,expected)
                if action=='withdraw_target':
                    with self.assertRaises(AppError):Miner(store).replay('dota2','dota-demo',5)
                else:self.assertNotEqual(Miner(store).replay('dota2','dota-demo',5),expected)

    def test_snapshot_isolated_from_later_mutation_and_validates_mode(self):
        rows=self.store.corpus('dota2');snapshot=SnapshotStore(rows,'dota2')
        expected=snapshot.match('dota2','dota-demo')['title']
        next(m for m in rows if m['match_id']=='dota-demo')['title']='Changed outside snapshot'
        self.assertEqual(snapshot.match('dota2','dota-demo')['title'],expected)
        with self.assertRaises(ValueError):snapshot.corpus('epl')
        with self.assertRaises(AppError):snapshot.match('dota2','missing')

    def test_console_reprompts_out_of_range_replay_and_accepts_boundaries(self):
        for valid in ('5','30'):
            console=Console('/unused');console.mode='dota2';out=io.StringIO()
            with patch('builtins.input',side_effect=['1','60',valid]),patch.object(console,'call',return_value={'snapshots':[]}) as call,contextlib.redirect_stdout(out):
                console.handle('/replay 123')
            self.assertEqual(out.getvalue().count('请输入 5～30 的整数'),2)
            self.assertEqual(call.call_args.args,('replay','--mode','dota2','--match-id','123','--step',int(valid),'--llm','off'))

    def test_http_raw_invalid_input_returns_client_error_and_version_is_1(self):
        server=make_server(self.store,port=0,api_key='');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            raw,meta=football(30,True);raw[0].pop('shot')
            request=Request(f'http://127.0.0.1:{server.server_port}/v1/analyze',json.dumps(dict(mode='epl',raw=raw,metadata=meta)).encode(),{'Content-Type':'application/json'})
            with self.assertRaises(HTTPError) as exc:urlopen(request)
            self.assertEqual(exc.exception.code,400)
            self.assertEqual(json.load(exc.exception)['error']['code'],'invalid_source')
            health=json.load(urlopen(f'http://127.0.0.1:{server.server_port}/health'))
            self.assertEqual(health['version'],'1.0.0')
        finally:server.shutdown();server.server_close();thread.join()
        self.assertEqual(__version__,'1.0.0');self.assertEqual(openapi()['info']['version'],'1.0.0')


if __name__=='__main__':unittest.main()
