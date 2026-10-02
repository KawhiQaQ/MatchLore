import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from highlights.api import make_server,dispatch,ingest,import_history
from highlights.engine import Miner
from highlights.store import Store,read_json
from highlights.verify import verify_response
from scripts.make_demo import make_demo,football,dota


class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'data';make_demo(self.root)
        self.store=Store(self.root);self.miner=Miner(self.store)

    def test_both_adapters_raw_and_catalog_agree_and_recount(self):
        for mode,mid in [('epl','epl-demo'),('dota2','dota-demo')]:
            body=dict(mode=mode,raw=read_json(self.root/mode/'raw'/f'{mid}.json'),as_of_minute=30)
            if mode=='epl':body['metadata']=read_json(self.root/mode/'raw'/f'{mid}.metadata.json')
            raw=dispatch(self.miner,body);stored=self.miner.analyze(mode,mid,30)
            self.assertTrue(raw['cards']);self.assertEqual(raw['cards'],stored['cards'])
            verify_response(self.store,stored)

    def test_http_analyze_and_incremental_history_refresh(self):
        server=make_server(self.store,port=0,api_key='')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,body):
            return json.load(urlopen(Request(f'http://127.0.0.1:{server.server_port}'+path,
                json.dumps(body).encode(),{'Content-Type':'application/json'})))
        try:
            for mode,builder,mid in [('epl',football,'epl-demo'),('dota2',dota,'dota-demo')]:
                raw,meta=builder(31,True);raw=copy.deepcopy(raw);meta=copy.deepcopy(meta)
                if mode=='epl':meta['match_id']='later-epl'
                else:raw['match_id']='later-dota'
                body=dict(mode=mode,raw=raw,as_of_minute=30)
                if meta:body['metadata']=meta
                before=request('/v1/analyze',body)
                first=request('/v1/ingest',dict(mode=mode,match_id=mid))
                second=request('/v1/ingest',dict(mode=mode,match_id=mid))
                after=request('/v1/analyze',body)
                self.assertEqual(first['status'],'committed');self.assertEqual(second['status'],'already_present')
                self.assertEqual(after['context']['history_matches'],before['context']['history_matches']+1)
        finally:server.shutdown();server.server_close();thread.join()

    def test_raw_history_batch_preflight_and_duplicates(self):
        destination=Store(Path(self.tmp.name)/'empty')
        body={'matches':[dict(mode='dota2',raw=dota(i)[0]) for i in range(2)]}
        self.assertEqual(import_history(destination,body)['inserted'],2)
        self.assertEqual(import_history(destination,body)['already_present'],2)
        invalid={'matches':[dict(mode='dota2',raw=dota(3)[0]),dict(mode='epl',raw=[])]}
        with self.assertRaises(ValueError):import_history(destination,invalid)
        self.assertEqual(len(destination.corpus('dota2')),2)
