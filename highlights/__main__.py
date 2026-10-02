"""Installable CLI; stdout is data, stderr is structured failures."""
import argparse
import json
from pathlib import Path
import sys
import zipfile
from . import __version__
from .store import Store,DEFAULT_DATA,read_json
from .api import dispatch,serve,ingest,import_history,history_operation,reanalyze_history
from .preflight import check_data,check_batch
from .engine import Miner
from . import llm
from .contracts import openapi
from .datasets import initialize,make_demo_bundle
from .errors import AppError,error_document


class Parser(argparse.ArgumentParser):
    def error(self,message):raise AppError('invalid_arguments',message)


def common(p,root=False):
    p.add_argument('--data',type=Path,default=DEFAULT_DATA if root else argparse.SUPPRESS,help='Data directory (or MATCHLORE_DATA)')
    p.add_argument('--env-file',type=Path,default=None if root else argparse.SUPPRESS,help='Explicit DeepSeek .env file; never put keys in arguments')
    p.add_argument('--output',type=Path,default=None if root else argparse.SUPPRESS,help='Write output file')
    p.add_argument('--format',choices=['json','text'],default='json' if root else argparse.SUPPRESS,help='JSON for integration, text for paired original/reference reading')


def parser():
    p=Parser(prog='matchlore',description='Mine evidence-backed EPL/Dota highlights; original facts plus optional broadcast references')
    p.add_argument('--version',action='version',version=__version__);common(p,True)
    commands=p.add_subparsers(dest='command')
    for name in ('console','init','demo-export','doctor','schema','llm-status','list','analyze','replay','ingest','import-history','serve','check','history-status','replace','withdraw','reanalyze'):
        q=commands.add_parser(name);common(q)
        if name=='init':q.add_argument('--demo',type=Path,help='Portable demo-data.zip; destination must not exist')
        if name in ('list','analyze','replay','ingest','import-history','check','history-status','replace','withdraw','reanalyze'):q.add_argument('--mode',choices=['epl','dota2'],required=True)
        if name=='list':q.add_argument('--role',choices=['development','historical','committed','all'],default='development')
        if name in ('analyze','ingest'):
            source=q.add_mutually_exclusive_group(required=True);source.add_argument('--match-id');source.add_argument('--raw',type=Path)
            q.add_argument('--metadata',type=Path,help='EPL single match metadata JSON for --raw')
        if name=='check':
            source=q.add_mutually_exclusive_group(required=True)
            source.add_argument('--raw',type=Path);source.add_argument('--manifest',type=Path)
            q.add_argument('--metadata',type=Path)
        if name=='replace':
            q.add_argument('--raw',type=Path,required=True);q.add_argument('--metadata',type=Path)
        if name in ('history-status','replace','withdraw'):q.add_argument('--match-id',required=True)
        if name in ('replace','withdraw'):
            q.add_argument('--expected-revision',type=int,required=True);q.add_argument('--reason',required=True)
        if name=='reanalyze':
            q.add_argument('--revision',type=int,required=True);q.add_argument('--minute',type=int,default=30)
            q.add_argument('--phase',type=int,default=1);q.add_argument('--max-cards',type=int,default=5)
        if name=='analyze':
            q.add_argument('--minute',type=int,default=30);q.add_argument('--phase',type=int,default=1);q.add_argument('--no-history',action='store_true')
        if name in ('analyze','replay'):
            q.add_argument('--llm',choices=['off','deepseek'],default='off');q.add_argument('--max-cards',type=int,default=5 if name=='analyze' else 3)
        if name=='replay':q.add_argument('--match-id',required=True);q.add_argument('--step',type=int,default=5)
        if name=='import-history':q.add_argument('--manifest',type=Path,required=True,help='JSON array of raw/metadata paths relative to this manifest')
        if name=='serve':q.add_argument('--host',default='127.0.0.1');q.add_argument('--port',type=int,default=8765)
    return p


def display(result):
    if not isinstance(result,dict):return json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if 'cards' in result:
        lines=[f'{result.get("title","")} ({result.get("mode","")})']
        for i,c in enumerate(result['cards'],1):
            ref=c['broadcast_reference'];lines.extend([f'\n{i}. 原始统计：{c["original_text"]}',f'   转播参考：{ref["text"] or "（无可用参考稿）"}',f'   参考状态：{ref["status"]}'+(f' / {ref["reason"]}' if ref.get('reason') else '')])
        if not result['cards']:lines.append('无合格亮点')
        return '\n'.join(lines)+'\n'
    if 'snapshots' in result:
        return '\n'.join(f'阶段{s["as_of"]["phase"]} 第{s["as_of"]["minute"]}分钟\n'+display(dict(s,title=result['title'],mode=result['mode'])) for s in result['snapshots'])
    return json.dumps(result,ensure_ascii=False,indent=2)+'\n'


def execute(args):
    if args.env_file is not None:
        args.env_file=args.env_file.expanduser()
        if not args.env_file.is_file():raise AppError('config_not_found','Explicit --env-file does not exist',404)
        llm.ENV_FILE=args.env_file.expanduser()
    store=Store(args.data.expanduser())
    if args.command=='console':
        from .console import Console
        Console(args.data.expanduser(),args.env_file).run();return None
    if args.command=='serve':serve(store,args.host,args.port);return None
    if args.command=='init':return initialize(args.data,args.demo)
    if args.command=='demo-export':
        if not args.output:raise AppError('invalid_arguments','demo-export requires --output archive.zip')
        return make_demo_bundle(args.data,args.output)
    if args.command=='schema':return openapi()
    if args.command=='llm-status':return llm.status()
    if args.command=='doctor':
        from ._solver import witness
        return dict(version=__version__,data=str(store.root.resolve()),data_exists=store.root.exists(),
                    solver='witness_successor_search',modes={mode:dict(total=len(store.corpus(mode)),historical=len([m for m in store.corpus(mode) if m['role'] in ('historical','committed')])) for mode in ('epl','dota2')},llm=llm.status())
    if args.command=='reanalyze':return reanalyze_history(store,dict(mode=args.mode,revision=args.revision,as_of_minute=args.minute,phase=args.phase,max_cards=args.max_cards))
    if args.command=='list':return store.list_matches(args.mode,args.role)
    if args.command=='replay':return Miner(store).replay(args.mode,args.match_id,args.step,args.max_cards,llm=args.llm)
    if args.command=='import-history' or (args.command=='check' and args.manifest):
        if args.command=='check' and args.metadata:raise AppError('invalid_arguments','--metadata requires --raw')
        items=read_json(args.manifest)
        if not isinstance(items,list) or not 1<=len(items)<=1000:raise AppError('invalid_manifest','Manifest must be array of 1..1000 entries')
        bodies=[]
        for item in items:
            if not isinstance(item,dict) or set(item)-{'raw','metadata'} or not isinstance(item.get('raw'),str):raise AppError('invalid_manifest','Each entry needs a raw path and optional metadata path')
            body=dict(mode=args.mode,raw=read_json(args.manifest.parent/item['raw']))
            if 'metadata' in item:body['metadata']=read_json(args.manifest.parent/item['metadata'])
            bodies.append(body)
        return check_batch(store,dict(matches=bodies)) if args.command=='check' else import_history(store,dict(matches=bodies))
    if args.command in ('history-status','withdraw'):
        body=dict(mode=args.mode,match_id=args.match_id)
        if args.command=='withdraw':body.update(expected_revision=args.expected_revision,reason=args.reason)
        return history_operation(store,body,'status' if args.command=='history-status' else 'withdraw')
    body=dict(mode=args.mode)
    if args.raw:
        body['raw']=read_json(args.raw)
        if args.metadata:body['metadata']=read_json(args.metadata)
    else:
        if args.metadata:raise AppError('invalid_arguments','--metadata requires --raw')
        body['match_id']=args.match_id
    if args.command=='check':return check_data(store,body)
    if args.command=='replace':
        body.update(match_id=args.match_id,expected_revision=args.expected_revision,reason=args.reason)
        return history_operation(store,body,'replace')
    if args.command=='ingest':return ingest(store,body)
    body.update(as_of_minute=args.minute,phase=args.phase,max_cards=args.max_cards,llm=args.llm,include_history=not args.no_history)
    return dispatch(Miner(store),body)


def main(argv=None):
    try:
        p=parser();args=p.parse_args(argv)
        if args.command is None:
            if sys.stdin.isatty() and sys.stdout.isatty():args.command='console'
            else:p.print_help();return
        if args.command=='console' and not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise AppError('interactive_terminal_required','console requires a terminal; use analyze/list/etc. in scripts')
        if args.command=='console' and args.output:
            raise AppError('invalid_arguments','Use /save inside console instead of --output')
        result=execute(args)
        if result is None:return
        text=display(result) if args.format=='text' else json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        if args.output and args.command!='demo-export':
            args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text)
            print(str(args.output.resolve()))
        else:print(text,end='')
        if args.command=='check' and not result['can_ingest']:raise SystemExit(2)
    except (AppError,ValueError,KeyError,TypeError,OSError,zipfile.BadZipFile) as error:
        print(json.dumps(error_document(error),ensure_ascii=False),file=sys.stderr)
        raise SystemExit(3 if isinstance(error,AppError) and error.status==404 else 4 if isinstance(error,AppError) and error.status==409 else 2)
    except Exception as error:
        print(json.dumps(error_document(error),ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)


if __name__=='__main__':main()
