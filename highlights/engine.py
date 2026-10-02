"""Shared mining, evidence, ranking and rendering; adapters own domain semantics."""
from . import __version__
from collections import defaultdict
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import time
import numpy as np

# Minimal solver implementation; no research workspace dependencies.
from ._solver.core import Cube
from ._solver.witness import solve as phase_change_search

PROFILES = {
    'epl':dict(metric='Shot', label='射门', minimum_value=3, streak_minimum=4, cutoff=3, caps=[0,1,2,3]),
    'dota2':dict(metric='Kill', label='归属击杀', minimum_value=4, streak_minimum=5, cutoff=5, caps=[0,1,2,3]),
}


def events_between(match, phase, start, end, kind=None, team=None, player=None):
    return [e for e in match['events'] if e['phase']==phase and start*60000<=e['time_ms']<end*60000
            and (kind is None or e['kind']==kind) and (team is None or e['team_id']==team)
            and (player is None or e['player_id']==player)]


def longest_run(match, phase, end, metric, team):
    # Never infer order between opposing kills with equal source timestamps.
    groups = defaultdict(list)
    for e in events_between(match,phase,0,end,metric):
        groups[e['time_ms']].append(e)
    best, run = [], []
    for _, events in sorted(groups.items()):
        if {e['team_id'] for e in events} == {team}:
            run += events
            if len(run)>len(best):
                best=run[:]
        else:
            run=[]
    return best


def state_at(match, team, phase, minute):
    kind = 'Goal' if match['mode']=='epl' else 'Kill'
    score = 0
    for e in match['events']:
        if e['kind']!=kind:
            continue
        if e['phase']<phase or (e['phase']==phase and 0<=e['time_ms']<minute*60000):
            score += 1 if e['team_id']==team else -1
    return int(np.sign(score))


def observations(history, phase, end):
    return [(m,t) for m in history if m['phases'].get(str(phase),0)>=end for t in m['teams']]


def cube(obs, phase, end, metric):
    counts=np.zeros((len(obs),end+1,1),np.int32)
    scores=np.zeros((len(obs),end+1),np.int32)
    pressure=np.zeros_like(scores)
    for i,(m,t) in enumerate(obs):
        own=np.array(sorted(e['time_ms'] for e in events_between(m,phase,0,end,metric,team=t)),dtype=np.int64)
        opp=np.array(sorted(e['time_ms'] for e in events_between(m,phase,0,end,metric) if e['team_id']!=t),dtype=np.int64)
        boundaries=np.arange(end+1)*60000
        counts[i,:,0]=np.searchsorted(own,boundaries,side='left')
        pressure[i]=np.searchsorted(opp,boundaries,side='left')
        for b in range(end+1):scores[i,b]=state_at(m,t,phase,b)
    return Cube(counts,scores,pressure,[dict(match_id=m['match_id'],team_id=t) for m,t in obs],[metric],60000)


def reference(name, items, value):
    n=len(items); tail=sum(r['value']>=value for r in items)
    return dict(name=name,n=n,tail_count=tail,smoothed_tail=(tail+1)/(n+1),
                unit='球队-比赛观测' if not items or 'player_id' not in items[0] else '球员出场',observations=items)


def ref_item(match, team, value, **extra):
    return dict(match_id=match['match_id'],team_id=team,value=int(value),**extra)


def fmt(value):
    return f'{int(value):02d}:00'


def render(card, match):
    f=card['fact'];profile=PROFILES[match['mode']];label=profile['label'];name=card['subject']['name']
    phase_name=('上半场' if f['phase']==1 else '下半场') if match['mode']=='epl' else '比赛'
    scope=f'{phase_name}{fmt(f["start"])}—{fmt(f["end"])}'
    if card['family']=='phase_change':
        pre=f['pre_count'];prelabel='没有'+label if pre==0 else f'仅{pre}次{label}'
        text=f'{name}在{phase_name}{fmt(f["pre_start"])}—{fmt(f["start"])}{prelabel}，随后至{fmt(f["end"])}完成{f["value"]}次{label}。'
    elif card['family']=='burst':
        text=f'{name}在{scope}完成{f["value"]}次{label}。'
    elif card['family']=='streak':
        text=f'截至{phase_name}{fmt(f["end"])}，{name}曾包揽双方事件流中连续{f["value"]}次{label}。'
    else:
        previous=max(r['value'] for r in card['references'][0]['observations'])
        text=f'{name}在{phase_name}前{f["end"]}分钟完成{f["value"]}次{label}；本地收录的此前{card["references"][0]["n"]}次可比出场中，同阶段最高为{previous}次。'
    if card['family']!='personal_record':
        r=card['references'][0]
        condition=f'满足前段≤{f["cap"]}次条件的' if card['family']=='phase_change' else ''
        comparison='没有观测达到这一数量' if r['tail_count']==0 else f'仅{r["tail_count"]}个达到或超过这一数量'
        text+=f'本地历史中，{condition}{r["n"]}个可比球队观测里，{comparison}。'
    return text


def make_card(match, team, family, fact, evidence, refs, player=None):
    if not evidence or not refs:
        raise ValueError('A card requires events and historical evidence')
    subject=dict(kind='player' if player is not None else 'team', id=player if player is not None else team,
                 team_id=team,name=match['players'][player]['name'] if player is not None else match['teams'][team])
    payload=dict(match_id=match['match_id'],family=family,subject=subject,fact=fact)
    cid=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:18]
    tail=max(r['smoothed_tail'] for r in refs)
    # Product salience ordering, not a hypothesis-test p-value or calibrated probability.
    score=-math.log(tail)+(0.5 if family in ('phase_change','personal_record') else 0)
    card=dict(id=cid,family=family,subject=subject,fact=fact,rank_score=round(score,6),
              robust_tail=tail,references=refs,evidence=[dict(e) for e in evidence],
              interpretation='描述性历史频率；经过模式搜索，不是校正后的显著性概率')
    card['text']=render(card,match)
    return card


def select(cards, limit):
    from .quality import pick
    return pick(cards,limit)


class Miner:
    def __init__(self, store):
        self.store=store
        self._cubes={}
        self._history_indexes={}

    def analyze(self, mode, match_id=None, as_of_minute=30, phase=1, max_cards=5, current=None, include_history=True):
        if mode not in PROFILES:
            raise ValueError('mode must be epl or dota2')
        if type(as_of_minute) is not int or type(phase) is not int or type(max_cards) is not int:
            raise ValueError('minute, phase and max_cards must be integers')
        if not 1<=max_cards<=10:
            raise ValueError('max_cards must be in 1..10')
        m=current if current is not None else self.store.match(mode,match_id)
        if m['mode']!=mode or not 5<=as_of_minute<=m['phases'].get(str(phase),0):
            raise ValueError('Invalid phase/minute; epl half-local 5..45, dota2 5..min(duration,60)')
        end=as_of_minute;p=PROFILES[mode];metric=p['metric']
        started=time.perf_counter();history=self.store.history(m)
        obs=observations(history,phase,end)
        cards=[];solver_stats=[]
        key=(mode,m['partition'],tuple(x['match_id'] for x in history),phase,end)
        if key not in self._cubes:
            if len(self._cubes)>=32:self._cubes.pop(next(iter(self._cubes)))
            self._cubes[key]=cube(obs,phase,end,metric)
        h=self._cubes[key]
        for team in m['teams']:
            # 1) Exact witness-driven search discovers the condition and split.
            if len(obs)>=15 and end>=10:
                x=cube([(m,team)],phase,end,metric)
                grid=5 if end%5==0 else 1
                hs=Cube(h.counts[:,::grid],h.score[:,::grid],h.opponent_shots[:,::grid],h.rows,h.events,grid*60000)
                xs=Cube(x.counts[:,::grid],x.score[:,::grid],x.opponent_shots[:,::grid],x.rows,x.events,grid*60000)
                config=dict(events=[metric],window_grid_minutes=grid,minimum_window_minutes=5,
                            thresholds={metric:p['caps']},minimum_reference_size=15,K=40,
                            opponent_shots_cutoff=p['cutoff'])
                result=phase_change_search(hs,xs,0,end//grid,config)
                solver_stats.append(dict(team_id=team,**result['stats']))
                for item in result['top_k']:
                    a,b,_,_,_,cap=item['pattern']
                    evidence=events_between(m,phase,b,end,metric,team=team)
                    pre=events_between(m,phase,a,b,metric,team=team)
                    value=len(evidence)
                    if value<p['minimum_value'] or item['robust_tail']>0.2:
                        continue
                    if value/(end-b)<2*len(pre)/(b-a):
                        continue
                    groups=[[],[],[]]
                    for hm,ht in obs:
                        if len(events_between(hm,phase,a,b,metric,team=ht))>cap:continue
                        v=len(events_between(hm,phase,b,end,metric,team=ht))
                        row=ref_item(hm,ht,v)
                        groups[0].append(row)
                        if state_at(hm,ht,phase,b)==state_at(m,team,phase,b):groups[1].append(row)
                        hi=sum(e['team_id']!=ht for e in events_between(hm,phase,0,b,metric))
                        xi=sum(e['team_id']!=team for e in events_between(m,phase,0,b,metric))
                        if (hi<=p['cutoff'])==(xi<=p['cutoff']):groups[2].append(row)
                    refs=[reference(n,g,value) for n,g in zip(['same_precondition','same_boundary_state','same_opponent_activity'],groups)]
                    if [(r['n'],r['tail_count']) for r in refs] != [(r['n'],r['tail_count']) for r in item['reference_counts']]:
                        raise AssertionError('Candidate17 failed independent event recount')
                    f=dict(phase=phase,start=b,end=end,pre_start=a,pre_count=len(pre),cap=cap,
                           metric=metric,value=value,measure='count',context_cutoff=p['cutoff'])
                    c=make_card(m,team,'phase_change',f,evidence,refs)
                    c['precondition_evidence']=[dict(e) for e in pre]
                    c['algorithm']='witness_successor';cards.append(c)
            # 2) Short bursts with fixed, understandable windows.
            for width in (5,10):
                if width>end or len(obs)<15:continue
                evidence=events_between(m,phase,end-width,end,metric,team=team)
                value=len(evidence)
                if value<p['minimum_value']:continue
                rows=[ref_item(hm,ht,len(events_between(hm,phase,end-width,end,metric,team=ht))) for hm,ht in obs]
                r=reference('same_phase_and_window',rows,value)
                if r['smoothed_tail']<=0.15:
                    f=dict(phase=phase,start=end-width,end=end,metric=metric,value=value,measure='count')
                    cards.append(make_card(m,team,'burst',f,evidence,[r]))
            # 3) Longest uninterrupted sequence; equal-time opposing events break runs.
            evidence=longest_run(m,phase,end,metric,team)
            if len(evidence)>=p['streak_minimum'] and len(obs)>=15:
                rows=[ref_item(hm,ht,len(longest_run(hm,phase,end,metric,ht))) for hm,ht in obs]
                r=reference('same_phase_prefix_max_run',rows,len(evidence))
                if r['smoothed_tail']<=0.2:
                    f=dict(phase=phase,start=0,end=end,metric=metric,value=len(evidence),measure='max_run')
                    cards.append(make_card(m,team,'streak',f,evidence,[r]))
        # 4) Identity-aware personal phase record, with explicit local coverage.
        for pid,player in m['players'].items():
            evidence=events_between(m,phase,0,end,metric,player=pid)
            if not player['identity'] or len(evidence)<3:continue
            rows=[]
            for hm in history:
                if hm['phases'].get(str(phase),0)<end:continue
                for hp,meta in hm['players'].items():
                    if meta['identity']!=player['identity']:continue
                    # Only include appearances observable by this phase cutoff,
                    # including zero-shot/zero-kill appearances.
                    if meta.get('seen_by',{}).get(str(phase),float('inf'))>=end*60000:continue
                    ev=events_between(hm,phase,0,end,metric,player=hp)
                    rows.append(ref_item(hm,meta['team_id'],len(ev),player_id=hp))
            if len(rows)>=3 and len(evidence)>max(r['value'] for r in rows):
                f=dict(phase=phase,start=0,end=end,metric=metric,value=len(evidence),measure='count',
                       player_identity=player['identity'],reference_condition='同一球员、同赛季/版本；截至该时刻已有出场事件或首发记录')
                c=make_card(m,player['team_id'],'personal_record',f,evidence,[reference('same_player_observed_appearance',rows,len(evidence))],player=pid)
                cards.append(c)
        history_candidates=0
        if include_history:
            from .history import TimelineIndex,mine_history
            hk=(mode,m['partition'],tuple((hm['match_id'],hm.get('source',{}).get('raw_sha256',''),hm['end_order']) for hm in history))
            if hk not in self._history_indexes:
                if len(self._history_indexes)>=4:self._history_indexes.pop(next(iter(self._history_indexes)))
                self._history_indexes[hk]=TimelineIndex(history)
            hc=mine_history(m,history,phase,end,self._history_indexes[hk]);history_candidates=len(hc)
            cards.extend(hc)
        from .metrics import mine_metrics
        from .quality import apply_quality
        extra=mine_metrics(m,history,phase,end)
        cards.extend(extra)
        cards,quality=apply_quality(cards,m,phase,end)
        selected=select(cards,max_cards)
        from .llm import original_output
        for card in selected:original_output(card)
        result=dict(schema_version=1,mode=mode,match_id=m['match_id'],title=m['title'],
                    as_of=dict(phase=phase,minute=end,interval='[start,end)'),
                    cards=selected,status='ok' if selected else 'no_highlights',
                    context=dict(history_matches=len({hm['match_id'] for hm,ht in obs}),history_team_observations=len(obs),
                                 partition=m['partition'],reference_policy='目标比赛开始前已结束的historical/committed比赛；同赛季或同版本/模式',
                                 coverage=m['coverage']),
                    diagnostics=dict(quality=quality,metric_candidate_cards=len(extra),candidate_cards=len(cards),history_candidate_cards=history_candidates,solver=solver_stats,
                                     elapsed_seconds=time.perf_counter()-started,renderer='deterministic_zh_v1',
                                     empty_reason=None if selected else '无模式通过支持度、数量、历史频率和去重规则'),
                    provenance=dict(current_source=m['source'],engine_version=__version__,
                                    solver_sha256=hashlib.sha256((Path(__file__).parent/'_solver/witness.py').read_bytes()).hexdigest()))
        return result

    def replay(self, mode, match_id, step=5, max_cards=3, llm='off'):
        if type(step) is not int or not 5<=step<=30:raise ValueError('step must be integer in 5..30')
        if type(max_cards) is not int or not 1<=max_cards<=10:raise ValueError('max_cards must be integer in 1..10')
        if llm not in ('off','deepseek'):raise ValueError('llm must be off or deepseek')
        m=self.store.match(mode,match_id);snapshots=[];seen=[];seen_cards=set()
        for phase,length in sorted(m['phases'].items()):
            for minute in range(step,length+1,step):
                r=self.analyze(mode,match_id,minute,int(phase),10)
                fresh=[]
                for c in r['cards']:
                    if c['id'] in seen_cards:continue
                    from .quality import event_keys
                    ids=event_keys(c)
                    # A repeat needs substantially new events, not merely a new window.
                    if ids and any(old and len(ids&old)/min(len(ids),len(old))>=0.7 for old in seen):continue
                    fresh.append(c)
                r['cards']=fresh
                if llm=='deepseek':
                    from .llm import enhance
                    r=enhance(r,max_cards)
                else:r['cards']=fresh[:max_cards]
                fresh=r['cards']
                for c in fresh:
                    seen.append(event_keys(c));seen_cards.add(c['id'])
                snapshot=dict(as_of=r['as_of'],cards=fresh,status='ok' if fresh else 'no_new_highlights')
                if llm=='deepseek':snapshot['llm']=r['diagnostics']['llm']
                snapshots.append(snapshot)
        return dict(mode=mode,match_id=str(match_id),title=m['title'],snapshots=snapshots,
                    playback='历史事件回放，非实时数据接入；每个快照仅使用当时事件')
