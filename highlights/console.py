"""Resident terminal workspace; calls the same CLI actions, never a separate miner."""
import json
import os
from pathlib import Path
import shlex
import sys
from . import __version__,llm
from .store import Store

COMMANDS={
    '/analyze':'分析比赛，按提示选比赛和时间点',
    '/replay':'按时间回放，去除重复亮点',
    '/matches':'浏览比赛，支持名称搜索和分页',
    '/mode':'切换英超 / Dota 2',
    '/llm':'开关 DeepSeek 转播参考稿',
    '/save':'保存最近结果为 JSON / 文本',
    '/check':'检查原始比赛或历史清单，不写入数据库',
    '/history':'查看单场版本与维护记录',
    '/replace':'更正或恢复比赛，保留版本记录',
    '/withdraw':'撤回比赛，停止参与分析与历史参考',
    '/reanalyze':'按变更版本重新分析受影响比赛',
    '/import':'批量导入历史 manifest',
    '/ingest':'将完整比赛纳入历史',
    '/data':'切换数据目录',
    '/init':'初始化空目录或导入演示数据包',
    '/status':'查看数据和模型配置',
    '/api':'在本机启动 HTTP API',
    '/help':'显示命令',
    '/exit':'退出',
}


class Back(Exception):
    pass


class Console:
    def __init__(self,data,env_file=None):
        self.data=Path(data);self.env_file=env_file;self.mode='epl';self.provider='off'
        self.last_result=None
        self.color=sys.stdout.isatty() and 'NO_COLOR' not in os.environ and os.environ.get('TERM')!='dumb'

    def paint(self,text,code='36'):
        return f'\033[{code}m{text}\033[0m' if self.color else text

    @property
    def mode_name(self):return '英超' if self.mode=='epl' else 'Dota 2'

    def banner(self):
        print(self.paint(f'\n  ◆ MatchLore  v{__version__}','1;36'))
        print('  赛事统计工作台 · 原始事实 + 转播参考\n')
        self.status()
        print('\n  /analyze 开始分析   /mode 切换场景   / 显示全部命令')
        print('  Tab 补全命令 · ↑↓ 输入历史 · Ctrl+C 取消当前操作 · Ctrl+D 退出')
        print('  使用 / 命令操作；操作中的 /back 返回主输入框。\n')

    def status(self):
        store=Store(self.data)
        counts=[]
        for mode,name in [('epl','英超'),('dota2','Dota 2')]:
            rows=store.corpus(mode)
            history=sum(m['role'] in ('historical','committed') for m in rows)
            counts.append(f'{name} {history} 历史 / {len(rows)} 总计')
        print(f'  数据  {self.data.resolve()}')
        print('  '+'  ·  '.join(counts))
        key='已配置' if llm.status()['key_configured'] else '未配置'
        print(f'  场景  {self.mode_name}  ·  DS 密钥{key}  ·  参考稿{"开启" if self.provider=="deepseek" else "关闭"}')
        if not any(store.corpus(m) for m in ('epl','dota2')):
            print('  当前没有比赛：用 /init 初始化演示库，或 /import 导入历史。')

    def ask(self,prompt,default=None):
        hint=f' [{default}]' if default is not None else ''
        value=input(f'  {prompt}{hint} › ').strip()
        if value=='/back':raise Back()
        return value if value else str(default) if default is not None else ''

    def number(self,prompt,default,low,high):
        while True:
            value=self.ask(prompt,default)
            if value.isdigit() and low<=int(value)<=high:return int(value)
            print(f'  请输入 {low}～{high} 的整数。')

    def path(self,prompt):
        while True:
            value=self.ask(prompt)
            if value:return Path(value).expanduser()
            print('  路径不能为空；输入 /back 返回。')

    def call(self,command,*args):
        from .__main__ import parser,execute
        argv=[command,'--data',str(self.data)]
        if self.env_file:argv+=['--env-file',str(self.env_file)]
        argv.extend(str(x) for x in args)
        return execute(parser().parse_args(argv))

    def show(self,result):
        from .__main__ import display
        self.last_result=result
        print('\n'+display(result))
        print(self.paint('  /save 保存完整结果；继续输入命令。','2'))

    def choose_match(self,role='development'):
        rows=Store(self.data).list_matches(self.mode,role)
        if not rows:
            print('  该范围没有比赛。可以导入原始文件，或浏览全部比赛。');raise Back()
        filtered=rows;page=0;size=8
        while True:
            visible=filtered[page*size:(page+1)*size]
            print(f'\n  {self.mode_name} · {len(filtered)} 场 · 第 {page+1}/{max(1,(len(filtered)+size-1)//size)} 页')
            for i,row in enumerate(visible,1):
                print(f'  {i}. {row["title"]}  |  {row["date"]}  |  ID {row["match_id"]}')
            value=self.ask('序号 / 比赛ID / 搜索词；n下一页 p上一页 *全部')
            if value=='n':page=min(page+1,max(0,(len(filtered)-1)//size));continue
            if value=='p':page=max(0,page-1);continue
            if value=='*':filtered=rows;page=0;continue
            if value.isdigit() and 1<=int(value)<=len(visible):return visible[int(value)-1]['match_id']
            for row in rows:
                if row['match_id']==value:return value
            if not value:continue
            filtered=[r for r in rows if value.casefold() in r['title'].casefold() or value in r['match_id']];page=0

    def source(self,match_id=None):
        if match_id:
            Store(self.data).match(self.mode,match_id)
            return ['--match-id',match_id]
        print('  1 演示/待分析比赛   2 原始 JSON 文件   3 全部比赛')
        choice=self.number('数据来源',1,1,3)
        if choice in (1,3):return ['--match-id',self.choose_match('development' if choice==1 else 'all')]
        args=['--raw',self.path('原始比赛 JSON 路径')]
        if self.mode=='epl':args+=['--metadata',self.path('英超单场元数据 JSON 路径')]
        return args

    def analyze(self,match_id=None):
        source=self.source(match_id)
        phase=self.number('半场：1 上半场 / 2 下半场',1,1,2) if self.mode=='epl' else 1
        maximum=45 if self.mode=='epl' else 60
        if source[0]=='--match-id':
            maximum=min(maximum,Store(self.data).match(self.mode,source[1])['phases'].get(str(phase),0))
        if maximum<5:raise ValueError('该阶段不足5分钟，无法分析')
        minute=self.number('分析至第几分钟（英超按半场计）',min(30,maximum),5,maximum)
        print(self.paint('  正在挖掘统计'+('并生成 DS 参考稿…' if self.provider=='deepseek' else '…')))
        self.show(self.call('analyze','--mode',self.mode,*source,'--phase',phase,'--minute',minute,'--llm',self.provider))

    def replay(self,match_id=None):
        mid=match_id or self.choose_match('all');step=self.number('回放间隔（分钟）',10,5,30)
        # Replay is offline by default: do not silently fan out paid DS requests.
        print('  正在回放，展示原始统计；回放不自动逐帧调用 DS。')
        self.show(self.call('replay','--mode',self.mode,'--match-id',mid,'--step',step,'--llm','off'))

    def save(self):
        if self.last_result is None:print('  还没有结果，请先 /analyze 或 /replay。');return
        from .__main__ import display
        path=self.path('保存路径（.json 完整证据，.txt 可读双输出）')
        if path.suffix.lower() not in ('.json','.txt'):raise ValueError('请使用 .json 或 .txt 文件后缀')
        if path.exists() and self.ask(f'{path} 已存在，覆盖？y/N','n').lower()!='y':return
        text=display(self.last_result) if path.suffix.lower()=='.txt' else json.dumps(self.last_result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
        print(f'  已保存：{path.resolve()}')

    def check(self):
        choice=self.number('1 单场原始文件 / 2 历史 manifest',1,1,2)
        args=['--mode',self.mode]
        if choice==2:args+=['--manifest',self.path('历史 manifest 路径')]
        else:
            args+=['--raw',self.path('原始比赛 JSON 路径')]
            if self.mode=='epl':args+=['--metadata',self.path('英超单场元数据 JSON 路径')]
        print(json.dumps(self.call('check',*args),ensure_ascii=False,indent=2))

    def maintain(self,command):
        mid=self.ask('比赛 ID（保持原 ID）')
        state=self.call('history-status','--mode',self.mode,'--match-id',mid)
        print(json.dumps(state,ensure_ascii=False,indent=2))
        if command=='history-status':return
        args=['--mode',self.mode,'--match-id',mid,'--expected-revision',state['revision']]
        if command=='replace':
            args+=['--raw',self.path('修正后的完整比赛 JSON 路径')]
            if self.mode=='epl':args+=['--metadata',self.path('英超单场元数据 JSON 路径')]
        args+=['--reason',self.ask('操作原因')]
        if self.ask('确认提交本次变更？y/N','n').lower()!='y':return
        print(json.dumps(self.call(command,*args),ensure_ascii=False,indent=2))

    def handle(self,line):
        parts=shlex.split(line);cmd=parts[0];rest=parts[1:]
        if cmd in ('/exit','/quit','exit','quit'):return False
        if cmd in ('/','/help','help','?'):
            for name,help_text in COMMANDS.items():print(f'  {name:12} {help_text}')
            return True
        if len(rest)>1 or (rest and cmd not in ('/mode','/analyze','/replay')):
            raise ValueError('该命令按提示输入即可；/analyze 和 /replay 可附比赛ID，/mode 可附 epl/dota2')
        if cmd=='/mode':
            mode=rest[0] if rest else ('epl' if self.number('1 英超 / 2 Dota 2',1 if self.mode=='epl' else 2,1,2)==1 else 'dota2')
            if mode not in ('epl','dota2'):raise ValueError('场景必须为 epl 或 dota2')
            self.mode=mode;print(f'  已切换：{self.mode_name}')
        elif cmd=='/llm':
            if self.provider=='deepseek':self.provider='off';print('  DS 参考稿已关闭。')
            elif not llm.status()['key_configured']:print('  未配置 DS 密钥，请在 .env 配置；也可退出后通过 --env-file 指定。不要在这里输入密钥。')
            elif self.ask('启用 DS 参考稿？每次分析最多两次模型调用，按供应商计费。y/N','n').lower()=='y':
                self.provider='deepseek';print('  DS 参考稿已开启，原始统计始终保留。')
        elif cmd=='/matches':
            mid=self.choose_match('all');m=Store(self.data).match(self.mode,mid)
            print(f'  已查看：{m["title"]}；输入 /analyze {mid} 开始分析。')
        elif cmd=='/analyze':self.analyze(rest[0] if rest else None)
        elif cmd=='/replay':self.replay(rest[0] if rest else None)
        elif cmd=='/save':self.save()
        elif cmd=='/status':self.status()
        elif cmd=='/data':
            path=self.path('数据目录')
            if not path.is_dir():raise ValueError('目录不存在；请使用 /init 初始化')
            self.data=path;self.status()
        elif cmd=='/init':
            path=self.path('新数据目录（须不存在）');demo=self.ask('演示数据 zip 路径，留空建立空库')
            from .datasets import initialize
            initialize(path,Path(demo).expanduser() if demo else None);self.data=path;self.status()
        elif cmd=='/check':self.check()
        elif cmd=='/reanalyze':
            revision=self.number('变更 revision',1,1,2147483647)
            phase=self.number('半场：1 上半场 / 2 下半场',1,1,2) if self.mode=='epl' else 1
            minute=self.number('分析至第几分钟',30,5,45 if self.mode=='epl' else 60)
            self.last_result=self.call('reanalyze','--mode',self.mode,'--revision',revision,'--phase',phase,'--minute',minute)
            print(f'  完成 {len(self.last_result["results"])} 场，跳过 {len(self.last_result["skipped"])} 场；使用 /save 保存。')
        elif cmd in ('/history','/replace','/withdraw'):self.maintain('history-status' if cmd=='/history' else cmd[1:])
        elif cmd=='/import':
            path=self.path('历史 manifest 路径')
            print(f'  将导入 {self.mode_name} 历史：{path} → {self.data}')
            if self.ask('确认提交整批？y/N','n').lower()=='y':
                result=self.call('import-history','--mode',self.mode,'--manifest',path)
                print(f'  完成：新增 {result["inserted"]} 场，已存在 {result["already_present"]} 场。')
        elif cmd=='/ingest':
            source=self.source();print(f'  将完整比赛提交至 {self.mode_name} 历史库：{source[1]}')
            if self.ask('确认比赛已结束并入库？y/N','n').lower()=='y':
                print(json.dumps(self.call('ingest','--mode',self.mode,*source),ensure_ascii=False))
        elif cmd=='/api':
            port=self.number('本地 API 端口',8765,1024,65535)
            print('  服务运行期间此终端用于 API；Ctrl+C 停止服务并回到工作台。')
            self.call('serve','--port',port)
        else:print('  未识别的命令。输入 / 查看功能；当前工作台使用命令操作，不是自然语言对话。')
        return True

    def run(self):
        readline=None;old_completer=None;old_delims=None
        try:
            try:
                import readline
                old_completer=readline.get_completer();old_delims=readline.get_completer_delims()
                readline.set_completer_delims(' \t\n')
                def complete(text,state):
                    matches=[c for c in COMMANDS if c.startswith(text)]
                    return matches[state] if state<len(matches) else None
                readline.set_completer(complete)
                readline.parse_and_bind('bind ^I rl_complete' if 'libedit' in (readline.__doc__ or '') else 'tab: complete')
            except ImportError:pass
            self.banner()
            while True:
                try:
                    provider='DS开' if self.provider=='deepseek' else '原始统计'
                    line=input(f'\n  {self.mode_name} · {provider} › ').strip()
                    if line and not self.handle(line):break
                except (Back,KeyboardInterrupt):print('\n  已取消，返回主输入框。')
                except EOFError:break
                except Exception as error:
                    from .errors import error_document
                    detail=error_document(error)['error'];print(self.paint(f'  {detail["code"]}：{detail["message"]}','33'))
            print('\n  已退出 MatchLore。')
        finally:
            if readline:
                readline.set_completer(old_completer);readline.set_completer_delims(old_delims)
