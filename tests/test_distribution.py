import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import zipfile
from highlights.__main__ import main,parser
from highlights.api import make_server
from highlights.contracts import openapi
from highlights.datasets import initialize,make_demo_bundle
from highlights.errors import AppError
from highlights.store import Store,DEFAULT_DATA


class Distribution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.make_demo import make_demo
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        cls.source=Path(cls.tmp.name)/'demo';make_demo(cls.source)
        cls.matches=[copy.deepcopy(m) for m in Store(cls.source).corpus('dota2')[:2]]

    def test_global_flags_before_or_after_command(self):
        for args in (['--data','/tmp/example','analyze','--mode','epl','--match-id','1'],['analyze','--data','/tmp/example','--mode','epl','--match-id','1']):
            self.assertEqual(parser().parse_args(args).data,Path('/tmp/example'))

    def test_cli_errors_are_json_and_do_not_pollute_stdout(self):
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as x:main(['analyze','--mode','epl'])
        self.assertEqual(x.exception.code,2);self.assertEqual(out.getvalue(),'')
        self.assertEqual(json.loads(err.getvalue())['error']['code'],'invalid_arguments')

    def test_atomic_import_conflict_does_not_partially_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);a,b=copy.deepcopy(self.matches);store.ingest(a)
            changed=copy.deepcopy(a);changed['title']='changed content'
            with self.assertRaises(AppError):store.ingest_many([b,changed])
            self.assertEqual(len(store.corpus('dota2')),1)
            result=store.ingest_many([a,b,b]);self.assertEqual([r['status'] for r in result],['already_present','committed','already_present'])
            self.assertEqual(len(store.corpus('dota2')),2)

    def test_conflicting_duplicates_within_batch_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);a=copy.deepcopy(self.matches[0]);b=copy.deepcopy(a);b['title']='different'
            with self.assertRaises(AppError):store.ingest_many([a,b])
            self.assertEqual(store.corpus('dota2'),[])

    def test_reader_does_not_create_schema_during_first_writer_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);store.ledger_path.touch()
            self.assertEqual(store.corpus('dota2'),[])
            self.assertEqual(store.ledger_path.stat().st_size,0)
            self.assertEqual(store.ingest(self.matches[0])['status'],'committed')

    def test_demo_bundle_roundtrip_preserves_counts_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);bundle=p/'demo.zip';data=p/'data'
            make_demo_bundle(self.source,bundle);initialize(data,bundle)
            for mode in ('epl','dota2'):
                self.assertEqual(len(Store(data).corpus(mode)),len(Store(self.source).corpus(mode)))
            with self.assertRaises(AppError):initialize(data,bundle)
            self.assertFalse((data/'rolling.sqlite3').exists())

    def test_bundle_path_escape_and_integrity_failure_are_atomic(self):
        import hashlib
        for name,digest in [('epl/../../escape',hashlib.sha256(b'x').hexdigest()),('epl/catalog.json','wrong')]:
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);bundle=p/'bad.zip';data=p/'data'
                with zipfile.ZipFile(bundle,'w') as z:
                    z.writestr('bundle.json',json.dumps(dict(format='highlights-demo-v1',files={name:digest})))
                    z.writestr(name,b'x')
                with self.assertRaises(AppError):initialize(data,bundle)
                self.assertFalse(data.exists());self.assertFalse((p/'escape').exists())

    def test_remote_bind_requires_key(self):
        with self.assertRaisesRegex(AppError,'MATCHLORE_API_KEY'):make_server(Store(),host='0.0.0.0',port=0,api_key='')

    def test_http_auth_errors_ids_contract_and_media_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            server=make_server(Store(tmp),port=0,api_key='local-test-token');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            def req(path,body=None,method=None,auth=True,media='application/json'):
                headers={'Content-Type':media}
                if auth:headers['Authorization']='Bearer local-test-token'
                return urlopen(Request(base+path,data=body,headers=headers,method=method))
            try:
                with self.assertRaises(HTTPError) as e:req('/health',auth=False)
                self.assertEqual(e.exception.code,401)
                payload=json.load(e.exception);self.assertEqual(payload['request_id'],e.exception.headers['X-Request-ID'])
                self.assertNotIn('local-test-token',json.dumps(payload))
                spec=json.load(req('/openapi.json'));self.assertEqual(spec['openapi'],'3.1.0')
                self.assertIn('/v1/history/import',spec['paths'])
                for path,body,method,media,status,code in [
                    ('/v1/analyze',b'{','POST','application/json',400,'invalid_request'),
                    ('/v1/analyze',b'{"a":NaN}','POST','application/json',400,'invalid_request'),
                    ('/v1/analyze',b'{}','POST','text/plain',415,'unsupported_media_type'),
                    ('/v1/analyze',b'{"mode":"epl","match_id":"missing"}','POST','application/json',404,'match_not_found'),
                    ('/health',b'{}','PUT','application/json',405,'method_not_allowed'),
                    ('/wrong',None,'GET','application/json',404,'not_found')]:
                    with self.assertRaises(HTTPError) as e:req(path,body,method,media=media)
                    self.assertEqual(e.exception.code,status);self.assertEqual(json.load(e.exception)['error']['code'],code)
            finally:server.shutdown();server.server_close();thread.join()

    def test_contract_has_both_outputs_and_batch_bounds(self):
        spec=openapi();schemas=spec['components']['schemas']
        self.assertIn('original_text',schemas['Card']['required'])
        self.assertIn('broadcast_reference',schemas['Card']['required'])
        self.assertEqual(schemas['ImportRequest']['properties']['matches']['maxItems'],1000)
        self.assertEqual(schemas['AnalyzeRequest']['properties']['max_cards']['maximum'],10)


if __name__=='__main__':unittest.main()
