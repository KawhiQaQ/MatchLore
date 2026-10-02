"""Small localhost JSON HTTP API, no hosted services or credentials required."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import hmac
import os
import sqlite3
import uuid
from urllib.parse import urlsplit, parse_qs
from .errors import AppError,error_document
from .contracts import openapi
from . import __version__
from .adapters import ADAPTERS
from .preflight import check_data,check_batch,normalize_source
from .engine import Miner
from .store import Store
from .llm import enhance, status as llm_status


def dispatch(miner, body):
    if not isinstance(body, dict):raise ValueError('Expected JSON object')
    allowed={'mode','match_id','as_of_minute','phase','max_cards','raw','metadata','llm','include_history'}
    if set(body)-allowed:raise ValueError('Unknown request field')
    mode=body.get('mode')
    if mode not in ADAPTERS:raise ValueError('mode must be epl or dota2')
    raw=body.get('raw')
    if raw is not None and body.get('match_id') is not None:
        raise ValueError('Supply raw OR match_id, not both')
    if raw is None and body.get('match_id') is None:raise ValueError('Supply raw or match_id')
    llm=body.get('llm','off');limit=body.get('max_cards',5)
    if llm not in ('off','deepseek'):raise ValueError('llm must be off or deepseek')
    if type(limit) is not int or not 1<=limit<=10:raise ValueError('max_cards must be integer in 1..10')
    current=ADAPTERS[mode](raw,body.get('metadata')) if raw is not None else None
    if type(body.get('include_history',True)) is not bool:raise ValueError('include_history must be boolean')
    result=miner.analyze(mode,body.get('match_id'),body.get('as_of_minute',30),
                         body.get('phase',1),10 if llm=='deepseek' else limit,current=current,include_history=body.get('include_history',True))
    return enhance(result,limit) if llm=='deepseek' else result


def normalize_ingest(store,body):
    if not isinstance(body,dict) or set(body)-{'mode','match_id','raw','metadata'}:raise ValueError('Invalid ingest fields')
    mode=body.get('mode')
    if mode not in ADAPTERS:raise ValueError('mode must be epl or dota2')
    if ('raw' in body)==('match_id' in body):raise ValueError('Supply exactly one of raw or match_id')
    if 'raw' in body and mode=='dota2':
        if not isinstance(body['raw'],dict) or type(body['raw'].get('radiant_win')) is not bool:
            raise ValueError('Ingest requires a completed Dota match with final radiant_win result')
    m=normalize_source(mode,body['raw'],body.get('metadata')) if 'raw' in body else store.match(mode,body['match_id'])
    return m


def ingest(store,body):
    return store.ingest(normalize_ingest(store,body))


def import_history(store,body):
    if not isinstance(body,dict) or set(body)!={'matches'} or not isinstance(body['matches'],list) or not 1<=len(body['matches'])<=1000:
        raise AppError('invalid_batch','Supply matches array with 1..1000 completed matches')
    matches=[normalize_ingest(store,item) for item in body['matches']]
    rows=store.ingest_many(matches)
    return dict(status='committed',results=rows,inserted=sum(r['status']=='committed' for r in rows),already_present=sum(r['status']=='already_present' for r in rows))


def history_operation(store, body, action):
    fields={'mode','match_id'} if action=='status' else {'mode','match_id','expected_revision','reason'}
    if action=='replace':fields |= {'raw','metadata'}
    required=fields-{'metadata'}
    if not isinstance(body,dict) or set(body)-fields or required-set(body):
        raise AppError('invalid_request','Invalid history operation fields')
    if body['mode'] not in ADAPTERS or not isinstance(body['match_id'],str) or not body['match_id'].strip():
        raise AppError('invalid_request','Supply a valid mode and nonempty string match_id')
    if action=='status':return store.history_status(body['mode'],body['match_id'])
    replacement=normalize_source(body['mode'],body['raw'],body.get('metadata')) if action=='replace' else None
    return store.change(body['mode'],body['match_id'],body['expected_revision'],body['reason'],replacement)


def reanalyze_history(store, body):
    allowed={'mode','revision','as_of_minute','phase','max_cards'}
    if not isinstance(body,dict) or set(body)-allowed or not {'mode','revision'}<=set(body):
        raise AppError('invalid_request','Supply mode, revision and optional phase/as_of_minute/max_cards')
    minute=body.get('as_of_minute',30);phase=body.get('phase',1);limit=body.get('max_cards',5)
    if type(minute) is not int or not 5<=minute<=60 or type(phase) is not int or phase not in (1,2) or type(limit) is not int or not 1<=limit<=10:
        raise AppError('invalid_request','Invalid phase, as_of_minute or max_cards')
    targets,corpus=store.affected_by(body['mode'],body['revision'])
    # Analyze a single captured corpus, even if another process changes the live ledger.
    class Snapshot:
        def corpus(self,mode):return corpus
        def match(self,mode,mid):return next(m for m in corpus if m['match_id']==mid)
        def history(self,current):return Store.history(self,current)
    miner=Miner(Snapshot());results=[];skipped=[]
    for mid in targets:
        match=miner.store.match(body['mode'],mid)
        if match['phases'].get(str(phase),0)<minute:
            skipped.append(dict(match_id=mid,reason='phase_or_minute_unavailable'));continue
        results.append(miner.analyze(body['mode'],mid,minute,phase,limit))
    return dict(mode=body['mode'],revision=body['revision'],results=results,skipped=skipped,
                status='completed',llm='off')


def make_server(store, host='127.0.0.1', port=8765, api_key=None):
    token=api_key if api_key is not None else os.environ.get('MATCHLORE_API_KEY','')
    if host not in ('127.0.0.1','localhost','::1') and not token:
        raise AppError('authentication_required','Set MATCHLORE_API_KEY before binding a non-loopback address')
    miner=Miner(store);lock=threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup();self.connection.settimeout(30)

        def reply(self,status,value):
            data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('X-Request-ID',self.request_id)
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

        def authorize(self):
            self.request_id=str(uuid.uuid4())
            if token and not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                raise AppError('unauthorized','Valid bearer token required',401)

        def handle_error(self,error):
            status=error.status if isinstance(error,AppError) else 404 if isinstance(error,FileNotFoundError) else 400 if isinstance(error,(ValueError,KeyError,TypeError)) else 503 if isinstance(error,sqlite3.OperationalError) else 500
            if isinstance(error,sqlite3.OperationalError):error=AppError('storage_unavailable','Storage temporarily unavailable',503)
            self.reply(status,error_document(error,self.request_id))

        def do_GET(self):
            try:
                self.authorize();path=urlsplit(self.path)
                if path.path=='/health':return self.reply(200,dict(status='ok',version=__version__))
                if path.path=='/openapi.json':return self.reply(200,openapi())
                if path.path=='/v1/llm':return self.reply(200,llm_status())
                if path.path=='/v1/modes':return self.reply(200,dict(modes=['epl','dota2']))
                if path.path=='/v1/matches':
                    query=parse_qs(path.query);mode=query.get('mode',[''])[0];role=query.get('role',['development'])[0]
                    if set(query)-{'mode','role'} or any(len(v)!=1 for v in query.values()):raise AppError('invalid_request','Invalid query fields')
                    if role not in ('development','historical','committed','all'):raise AppError('invalid_request','Invalid role')
                    with lock:matches=store.list_matches(mode,role)
                    return self.reply(200,dict(mode=mode,matches=matches))
                raise AppError('not_found','Route not found',404)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as error:self.handle_error(error)

        def do_POST(self):
            try:
                self.authorize()
                if self.headers.get('Transfer-Encoding'):raise AppError('invalid_request','Transfer-Encoding is not supported')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=16*1024*1024:raise AppError('request_too_large','Body must be 1..16 MiB',413)
                if self.headers.get('Content-Type','').split(';')[0].strip().lower()!='application/json':raise AppError('unsupported_media_type','Use application/json',415)
                def invalid_constant(value):raise ValueError('Nonfinite JSON numbers are not supported')
                body=json.loads(self.rfile.read(length),parse_constant=invalid_constant)
                if self.path=='/v1/analyze':
                    with lock:result=dispatch(miner,body)
                elif self.path=='/v1/ingest':
                    with lock:result=ingest(store,body)
                elif self.path=='/v1/check':
                    with lock:result=check_data(store,body)
                elif self.path in ('/v1/history/status','/v1/history/replace','/v1/history/withdraw'):
                    with lock:result=history_operation(store,body,self.path.rsplit('/',1)[-1])
                elif self.path=='/v1/history/reanalyze':
                    with lock:result=reanalyze_history(store,body)
                elif self.path=='/v1/history/check':
                    with lock:result=check_batch(store,body)
                elif self.path=='/v1/history/import':
                    with lock:result=import_history(store,body)
                elif self.path=='/v1/replay':
                    if not isinstance(body,dict) or set(body)-{'mode','match_id','step','max_cards','llm'}:raise AppError('invalid_request','Invalid replay fields')
                    with lock:result=miner.replay(body['mode'],body['match_id'],body.get('step',5),body.get('max_cards',3),llm=body.get('llm','off'))
                else:raise AppError('not_found','Route not found',404)
                self.reply(200,result)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as error:self.handle_error(error)

        def unsupported_method(self):
            try:
                self.authorize()
                raise AppError('method_not_allowed','Use GET or POST for this API',405)
            except Exception as error:self.handle_error(error)

        do_PUT=unsupported_method
        do_DELETE=unsupported_method
        do_PATCH=unsupported_method
        do_OPTIONS=unsupported_method

    return ThreadingHTTPServer((host,port),Handler)


def serve(store, host='127.0.0.1', port=8765):
    server=make_server(store,host,port)
    print(f'MatchLore API listening on http://{host}:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
