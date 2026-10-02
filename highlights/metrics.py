"""Domain metric expansion. Counts have explicit coverage and source semantics."""
import hashlib
import json
import math
from collections import defaultdict
from bisect import bisect_left

METRICS={
 'epl':{
  'ShotOnTarget':dict(label='射正',minimum=3,record_minimum=3,meaning='射门结果为Goal、Saved或Saved to Post；不含Saved Off Target'),
  'Corner':dict(label='角球开出',minimum=3,record_minimum=4,meaning='Pass.type=Corner；统计已开出的角球，不是判罚次数'),
  'DribbleComplete':dict(label='成功过人',minimum=4,record_minimum=4,meaning='Dribble.outcome=Complete；不是带球推进次数')},
 'dota2':{
  'Buyback':dict(label='买活',minimum=2,record_minimum=2,meaning='玩家buyback_log；不推断买活收益或比赛胜负'),
  'ObserverWard':dict(label='侦查守卫放置',minimum=4,record_minimum=5,meaning='obs_log；不含岗哨守卫，不代表有效视野面积'),
  'TowerLoss':dict(label='防御塔损失',minimum=3,record_minimum=4,meaning='building_kill中的本方防御塔；可能包含反补，不等同对手推塔击杀')},
}


def covered(m,metric,team,player=None):
    if m.get('event_coverage',{}).get(metric,False):return True
    if metric not in ('Buyback','ObserverWard'):return False
    ps=[p for pid,p in m['players'].items() if p['team_id']==team and (player is None or pid==player)]
    return bool(ps) and (player is not None or len(ps)==5) and all(p.get('metric_coverage',{}).get(metric,False) for p in ps)


class MetricIndex:
    def __init__(self,matches,phase):
        self.times=defaultdict(list);self.events=defaultdict(list)
        for m in matches:
            for e in m['events']:
                if e['phase']!=phase:continue
                for player in (None,e['player_id']) if e['player_id'] is not None else (None,):
                    key=(m['match_id'],e['kind'],e['team_id'],player)
                    self.events[key].append(e)
        for key,es in self.events.items():
            es.sort(key=lambda e:(e['time_ms'],e['id']));self.times[key]=[e['time_ms'] for e in es]

    def window(self,m,metric,team,player,start,end):
        key=(m['match_id'],metric,team,player);ts=self.times[key]
        return self.events[key][bisect_left(ts,start*60000):bisect_left(ts,end*60000)]


def mine_metrics(m,history,phase,end):
    index=MetricIndex([m]+history,phase);cards=[]
    for metric,config in METRICS[m['mode']].items():
        for team in m['teams']:
            if not covered(m,metric,team):continue
            for width in (5,10):
                if width>end:continue
                ev=index.window(m,metric,team,None,end-width,end)
                if len(ev)<config['minimum']:continue
                rows=[]
                for hm in history:
                    if hm['phases'].get(str(phase),0)<end:continue
                    for ht in hm['teams']:
                        if covered(hm,metric,ht):rows.append(dict(match_id=hm['match_id'],team_id=ht,
                            value=len(index.window(hm,metric,ht,None,end-width,end))))
                if len(rows)>=30 and (1+sum(r['value']>=len(ev) for r in rows))/(1+len(rows))<=.15:
                    cards.append(make(m,metric,'metric_burst',team,None,phase,end-width,end,ev,rows,config))
        # Same-entity records include valid zero counts and do not pool all players.
        subjects=[(t,None,m.get('team_identities',{}).get(t)) for t in m['teams']]
        if metric!='TowerLoss':subjects += [(p['team_id'],pid,p.get('identity')) for pid,p in m['players'].items()]
        for team,pid,identity in subjects:
            if not identity or not covered(m,metric,team,pid):continue
            ev=index.window(m,metric,team,pid,0,end)
            if len(ev)<config['record_minimum']:continue
            rows=[]
            for hm in history:
                if hm['phases'].get(str(phase),0)<end:continue
                if pid is None:
                    past=[(ht,None) for ht,hi in hm.get('team_identities',{}).items() if hi==identity]
                else:
                    past=[(p['team_id'],hp) for hp,p in hm['players'].items() if p.get('identity')==identity
                          and p.get('seen_by',{}).get(str(phase),float('inf'))<end*60000]
                for ht,hp in past:
                    if not covered(hm,metric,ht,hp):continue
                    row=dict(match_id=hm['match_id'],team_id=ht,value=len(index.window(hm,metric,ht,hp,0,end)))
                    if hp is not None:row['player_id']=hp
                    rows.append(row)
            if len(rows)>=5 and len(ev)>max(r['value'] for r in rows):
                cards.append(make(m,metric,'metric_record',team,pid,phase,0,end,ev,rows,config,identity))
    return cards


def make(m,metric,family,team,pid,phase,start,end,ev,rows,config,identity=None):
    subject=dict(kind='team' if pid is None else 'player',id=team if pid is None else pid,
                 team_id=team,name=m['teams'][team] if pid is None else m['players'][pid]['name'])
    n=len(rows);k=sum(r['value']>=len(ev) for r in rows);tail=(k+1)/(n+1)
    fact=dict(metric=metric,phase=phase,start=start,end=end,value=len(ev),measure='count',metric_definition=config['meaning'])
    if identity is not None:fact['entity_identity']=identity
    scope=('上半场' if phase==1 else '下半场') if m['mode']=='epl' else '比赛'
    actions={'ShotOnTarget':'完成{n}次射正','Corner':'开出{n}个角球','DribbleComplete':'完成{n}次成功过人',
             'Buyback':'使用{n}次买活','ObserverWard':'放置{n}个侦查守卫','TowerLoss':'损失{n}座防御塔'}
    unit={'Corner':'个','ObserverWard':'个','TowerLoss':'座'}.get(metric,'次')
    text=f'{subject["name"]}在{scope}{start}—{end}分钟内'+actions[metric].format(n=len(ev))+'。'
    if family=='metric_record':
        fact['previous_max']=max(r['value'] for r in rows)
        text+=f'此前已收录的{n}次同主体可比出场，同阶段最高为{fact["previous_max"]}{unit}。'
    else:text+=f'已收录历史的{n}个同时间窗球队观测中，{k}个达到或超过这一数量。'
    cid=hashlib.sha256(json.dumps([m['match_id'],family,subject,fact],sort_keys=True).encode()).hexdigest()[:18]
    return dict(id=cid,family=family,subject=subject,fact=fact,evidence=ev,
        references=[dict(name='same_entity_window' if identity else 'same_window',n=n,tail_count=k,
            smoothed_tail=tail,unit='同主体出场' if identity else '球队-比赛观测',observations=rows)],
        robust_tail=tail,rank_score=round(-math.log(tail)+(.5 if identity else 0),6),text=text,
        interpretation='描述性历史频率；范围为已收录同赛季/版本数据，不是完整生涯纪录或显著性检验',algorithm='covered_metric_index_v1')
