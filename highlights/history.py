"""Exact per-entity temporal queries over an ordered, bounded-coverage history.

Identity index + bisected event time indexes avoid repeatedly scanning whole
matches. Missing observations are barriers for streaks, not zero-valued games.
All patterns are explicit predicates; LLMs do not compute any record counts.
"""
from bisect import bisect_left
from collections import defaultdict
import hashlib
import json
import math


def entities(m):
    for local,name in m['teams'].items():
        identity=m.get('team_identities',{}).get(local)
        if identity:yield dict(kind='team',id=local,identity=identity,team_id=local,name=name)
    for local,p in m['players'].items():
        if p.get('identity'):
            yield dict(kind='player',id=local,identity=p['identity'],team_id=p['team_id'],name=p['name'])


def rules_for(mode,kind,phase,end):
    if mode=='epl':
        rules=[dict(id='shot_activity',metric='Shot',phase=phase,end=end,minimum=1 if kind=='player' else 5)]
        if end>=10:
            rules.append(dict(id='quiet_then_burst',metric='Shot',phase=phase,end=end,boundary=(end//10)*5,
                              cap=0 if kind=='player' else 1,minimum=2 if kind=='player' else 4))
    else:
        rules=[dict(id='early_kills',metric='Kill',phase=1,end=min(end,10),minimum=3 if kind=='player' else 5)]
        if kind=='player':rules.append(dict(id='zero_deaths',metric='Death',phase=1,end=min(end,10),maximum=0))
        if end>=20:rules.append(dict(id='quiet_then_burst',metric='Kill',phase=1,end=20,boundary=10,
                                     cap=0 if kind=='player' else 2,minimum=3 if kind=='player' else 5))
    return rules


class TimelineIndex:
    def __init__(self,history):
        self.timelines=defaultdict(list);self.cache={}
        for m in sorted(history,key=lambda m:(m['start_order'],m['match_id'])):
            for subject in entities(m):
                self.timelines[(subject['kind'],subject['identity'])].append((m,subject))

    def for_subject(self,subject):return self.timelines.get((subject['kind'],subject['identity']),[])

    def window(self,m,subject,metric,phase,start,end):
        key=(m['match_id'],m.get('source',{}).get('raw_sha256',id(m)),subject['kind'],subject['id'],metric,phase)
        if key not in self.cache:
            es=[e for e in m['events'] if e['kind']==metric and e['phase']==phase and e['team_id']==subject['team_id']
                and (subject['kind']=='team' or e['player_id']==subject['id'])]
            es.sort(key=lambda e:(e['time_ms'],e['id']))
            self.cache[key]=([e['time_ms'] for e in es],es)
        ts,es=self.cache[key]
        return es[bisect_left(ts,start*60000):bisect_left(ts,end*60000)]

    def observe(self,m,s,rule):
        p=rule['phase'];end=rule['end'];metric=rule['metric']
        complete=m['phases'].get(str(p),0)>=end
        if s['kind']=='player':
            player=m['players'][s['id']]
            complete=complete and player.get('seen_by',{}).get(str(p),float('inf'))<end*60000
            if metric=='Death':complete=complete and player.get('death_log_complete',False)
        if metric!='Death':complete=complete and m.get('event_coverage',{}).get(metric,False)
        row=dict(match_id=m['match_id'],local_id=s['id'],team_id=s['team_id'],complete=bool(complete),
                 start_order=m['start_order'],end_order=m['end_order'],holds=None,value=None,pre_count=None,evidence_ids=[])
        if not complete:return row
        b=rule.get('boundary',0)
        later=self.window(m,s,metric,p,b,end)
        prior=self.window(m,s,metric,p,0,b) if b else []
        value=len(later)
        holds=value>=rule['minimum'] if 'minimum' in rule else value<=rule['maximum']
        if b:holds=holds and len(prior)<=rule['cap']
        row.update(holds=holds,value=value,pre_count=len(prior),evidence_ids=[e['id'] for e in prior+later])
        return row


def describe_rule(rule,mode):
    metric={'Shot':'射门','Kill':'归属击杀','Death':'死亡'}[rule['metric']]
    prefix=('上半场' if rule['phase']==1 else '下半场') if mode=='epl' else '比赛'
    if rule['id']=='quiet_then_burst':
        before=f'没有{metric}' if rule['cap']==0 else f'至多{rule["cap"]}次{metric}'
        return f'{prefix}前{rule["boundary"]}分钟{before}，随后至第{rule["end"]}分钟至少{rule["minimum"]}次{metric}'
    if rule['id']=='zero_deaths':return f'{prefix}前{rule["end"]}分钟零死亡'
    return f'{prefix}前{rule["end"]}分钟至少{rule["minimum"]}次{metric}'


def card(m,s,family,rule,value,evidence,history,text,score):
    fact=dict(phase=rule['phase'],start=0,end=rule['end'],metric=rule['metric'],value=value,
              measure=family,rule=rule)
    key=json.dumps([m['match_id'],s['kind'],s['identity'],family,rule,value],sort_keys=True)
    return dict(id=hashlib.sha256(key.encode()).hexdigest()[:18],family=family,subject=s,fact=fact,
                evidence=evidence,references=[],history=history,rank_score=round(score,6),robust_tail=None,
                text=text,interpretation='已收录同赛季/版本比赛中的精确计数；不宣称完整生涯或完整赛事覆盖',
                algorithm='ordered_entity_prefix_index_v1')


def mine_history(current,history,phase,end,index=None):
    index=index or TimelineIndex(history);cards=[]
    for subject in entities(current):
        past=index.for_subject(subject)
        if not past:continue
        for rule in rules_for(current['mode'],subject['kind'],phase,end):
            now=index.observe(current,subject,rule)
            if now['holds'] is not True:continue
            rows=[index.observe(m,s,rule) for m,s in past]
            # The latest result is only continuous if the previous match ended
            # before it began; overlapping schedules break the suffix.
            streak=[];next_start=current['start_order']
            for row in reversed(rows):
                if row['holds'] is not True or row['end_order']>=next_start:break
                streak.append(row);next_start=row['start_order']
            matched=[r for r in rows if r['holds'] is True]
            complete=sum(r['complete'] for r in rows)
            if complete<3:continue
            evidence=[]
            for e in current['events']:
                if e['id'] in now['evidence_ids']:evidence.append(dict(e))
            scope=dict(observations=rows,current_observation=now,previous_matches=len(rows),
                       comparable_matches=complete,unknown_matches=len(rows)-complete,partition=current['partition'],
                       coverage='仅已收录同实体出场；未收录比赛是否打断纪录未知')
            description=describe_rule(rule,current['mode']);name=subject['name']
            if len(streak)>=2:
                value=len(streak)+1
                h=dict(scope,streak_match_ids=[r['match_id'] for r in reversed(streak)]+[current['match_id']])
                text=f'{name}在最近连续{value}次已收录出场中均做到：{description}。'
                cards.append(card(current,subject,'match_streak',rule,value,evidence,h,text,2+math.log(value)))
            if matched and rule['id']=='quiet_then_burst':
                value=len(matched)+1
                h=dict(scope,previous_occurrences=len(matched),occurrence_match_ids=[r['match_id'] for r in matched]+[current['match_id']])
                text=f'{name}本场再次做到“{description}”；在已收录的{complete}次历史可比出场加上本场中，这是第{value}次。'
                if scope['unknown_matches']:text+=f'另有{scope["unknown_matches"]}次已收录出场缺少可比覆盖，未计入次数。'
                cards.append(card(current,subject,'historical_occurrence',rule,value,evidence,h,text,3+math.log(value)))
        if subject['kind']=='player':
            c=event_suffix(current,subject,past,phase,end)
            if c:cards.append(c)
    return cards


def event_suffix(current,subject,past,phase,end):
    mode=current['mode'];chain=[];boundary='observed_history_start';used=[]
    ordered=past+[(current,subject)]
    next_start=float('inf')
    for m,s in reversed(ordered):
        if m is not current and m['end_order']>=next_start:
            boundary='overlapping_matches';break
        next_start=m['start_order']
        if mode=='dota2' and not m['players'][s['id']].get('death_log_complete',False):
            boundary='missing_death_log';break
        own=[e for e in m['events'] if e['player_id']==s['id'] and e['team_id']==s['team_id']
             and e['kind'] in (('Shot',) if mode=='epl' else ('Kill','Death')) and e['time_ms']>=0]
        if m is current:own=[e for e in own if e['phase']<phase or e['phase']==phase and e['time_ms']<end*60000]
        own.sort(key=lambda e:(e['phase'],e['time_ms'],e['id']))
        # A death at a tied kill time is a barrier, regardless of source ordering.
        death_times={(e['phase'],e['time_ms']) for e in own if e['kind']=='Death'}
        stopped=False
        for e in reversed(own):
            good=e.get('is_goal') is False if mode=='epl' else e['kind']=='Kill' and (e['phase'],e['time_ms']) not in death_times
            if not good:
                boundary='goal_or_death_or_unknown_outcome';stopped=True;break
            chain.append(dict(e,match_id=m['match_id']))
        used.append(m['match_id'])
        if stopped:break
    chain.reverse();matches=list(dict.fromkeys(e['match_id'] for e in chain))
    minimum=8 if mode=='epl' else 5
    if len(chain)<minimum or len(matches)<2 or current['match_id'] not in matches:return None
    first=next(i for i,(m,_) in enumerate(ordered) if m['match_id']==matches[0])
    span=[m['match_id'] for m,_ in ordered[first:]]
    rule=dict(id='shot_without_goal' if mode=='epl' else 'kill_without_death',metric='Shot' if mode=='epl' else 'Kill',phase=phase,end=end)
    history=dict(event_match_ids=matches,span_match_ids=span,scanned_match_ids=used,boundary=boundary,partition=current['partition'],
                 coverage='已收录出场序列；缺失日志中断，不代表完整生涯')
    phrase='次射门未进球' if mode=='epl' else '次归属击杀，其间未记录本人死亡'
    text=f'{subject["name"]}跨{len(span)}场已收录比赛，最近连续{len(chain)}{phrase}。'
    return card(current,subject,'cross_match_events',rule,len(chain),chain,history,text,2+math.log(len(chain)))
