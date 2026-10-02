"""Deterministic editorial policy; not a learned or human interest score."""
from collections import Counter
import math


def event_keys(card):
    return {(e.get('match_id','current'),e.get('source',{}).get('event_id',e['id'])) for e in card['evidence']}


def apply_quality(cards,m,phase,end):
    accepted=[];rejected=[]
    for c in cards:
        f=c['fact'];reason=None
        current=[e for e in c['evidence'] if e.get('match_id',m['match_id'])==m['match_id'] and e['phase']==phase]
        if f['phase']!=phase or f['end']<end-5:reason='stale_window'
        elif current and max(e['time_ms'] for e in current)<(end-10)*60000:reason='no_recent_trigger'
        elif c['family']=='match_streak' and f.get('rule',{}).get('id')=='shot_activity':
            h=c['history'];frequency=sum(r['holds'] is True for r in h['observations'])/max(1,h['comparable_matches'])
            if f['value']<5 or frequency>.65:reason='routine_shot_activity'
        if not reason and c['family']=='historical_occurrence':
            h=c['history'];frequency=h['previous_occurrences']/h['comparable_matches']
            if frequency>.2:reason='routine_repeated_condition'
        if reason:
            rejected.append(dict(id=c['id'],family=c['family'],reason=reason));continue
        reasons=['可回查事件和历史样本','当前阶段相关']
        if c['family'] in ('personal_record','metric_record'):reasons.append('超过已收录同主体的阶段最高值')
        elif c['family']=='cross_match_events':reasons.append('跨场连续表现由本场事件延续')
        elif c.get('robust_tail') is not None:reasons.append('同条件历史频率较低')
        else:reasons.append('已收录历史中的连续或重复表现')
        # Keep the complete auditable text, plus a short usable lead.
        c['summary']=c['text'] if c['family'] in ('personal_record','metric_record') else c['text'].split('。')[0]+'。'
        supporting=(f['metric']=='ObserverWard' or c['family']=='match_streak' and
                    (f.get('rule',{}).get('id')=='shot_activity' or f.get('rule',{}).get('id')=='zero_deaths' and f['value']<5))
        c['presentation_priority']='supporting' if supporting else 'primary'
        c['mining_score']=c['rank_score']
        calibrated=c['mining_score'];basis='original_reference_score'
        if c['family']=='historical_occurrence':
            # Replicating all appearances does not make the event rarer.
            h=c['history'];frequency=h['previous_occurrences']/h['comparable_matches']
            calibrated=-math.log(frequency)+.5;basis='historical_occurrence_rate'
        elif c['family']=='cross_match_events':
            # Sequence length is not a calibrated historical rarity measure.
            # Keep these useful facts, without letting huge totals dominate.
            calibrated=1.5+.5*math.log1p(min(f['value'],30));basis='bounded_sequence_length'
        adjustment=-1.0 if supporting else (-.75 if c['family']=='cross_match_events' and m['mode']=='epl' else 0)
        c['ranking_adjustment']=adjustment
        c['rank_score']=round(calibrated+adjustment,6)
        c['selection_score_basis']=basis
        c['ranking_adjustment']=round(c['rank_score']-c['mining_score'],6)
        if supporting:reasons.append('背景补充；不推断战术效果')
        elif adjustment:reasons.append('跨场次数未作历史稀有度校准，降低排序权重')
        c['selection_reasons']=reasons
        c['quality_policy']='editorial_v2'
        accepted.append(c)
    return accepted,dict(policy='editorial_v2',generated=len(cards),eligible=len(accepted),
                         rejected_by_reason=dict(Counter(x['reason'] for x in rejected)),rejected=rejected)


def pick(cards,limit):
    chosen=[];families=Counter();subjects=Counter()
    for c in sorted(cards,key=lambda c:(-c['rank_score'],c['id'])):
        subject=(c['subject']['kind'],c['subject']['id'])
        if families[c['family']] >= (1 if c['family']=='cross_match_events' else 2) or subjects[subject]>=2:continue
        ids=event_keys(c)
        if any(ids and (other:=event_keys(old)) and len(ids&other)/min(len(ids),len(other))>=.7 for old in chosen):continue
        chosen.append(c);families[c['family']]+=1;subjects[subject]+=1
        if len(chosen)>=limit:break
    return chosen
