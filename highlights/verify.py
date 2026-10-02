"""Independent, slow recount of published cards. No mining helper imports."""
from fractions import Fraction
from itertools import groupby


def records(m,phase,start,end,kind,team=None,player=None):
    out=[]
    for e in m['events']:
        if e['phase']!=phase or e['kind']!=kind:continue
        if not start*60000<=e['time_ms']<end*60000:continue
        if team is not None and e['team_id']!=team:continue
        if player is not None and e['player_id']!=player:continue
        out.append(e)
    return out


def run(m,phase,end,metric,team):
    sequence=sorted(records(m,phase,0,end,metric),key=lambda e:(e['time_ms'],e['id']))
    best=[];chain=[]
    for _,g in groupby(sequence,key=lambda e:e['time_ms']):
        g=list(g)
        if all(e['team_id']==team for e in g):chain.extend(g)
        else:chain=[]
        if len(chain)>len(best):best=list(chain)
    return best


def sign(m,team,phase,end):
    own=other=0
    for e in m['events']:
        if e['kind']!=('Goal' if m['mode']=='epl' else 'Kill'):continue
        if e['phase']<phase or e['phase']==phase and 0<=e['time_ms']<end*60000:
            if e['team_id']==team:own+=1
            else:other+=1
    return (own>other)-(own<other)


def verify_response(store,result,current=None):
    m=current or store.match(result['mode'],result['match_id'])
    history=[x for x in store.corpus(result['mode']) if x['role'] in ('historical','committed')
             and x['match_id']!=m['match_id'] and x['end_order']<m['start_order'] and x['partition']==m['partition']]
    checks=0
    for c in result['cards']:
        if c['family'] in ('metric_burst','metric_record'):
            from .verify_metrics import verify_metric
            checks+=verify_metric(m,history,c,result['as_of']['phase'],result['as_of']['minute'])
            continue
        if c['family'] in ('match_streak','historical_occurrence','cross_match_events'):
            from .verify_history import verify_card
            assert c['fact']['phase']<=result['as_of']['phase']
            assert c['fact']['end']<=result['as_of']['minute']
            checks+=verify_card(m,history,c)
            continue
        f=c['fact'];phase=f['phase'];end=f['end'];team=c['subject']['team_id'];metric=f['metric']
        if c['family']=='streak':expected=run(m,phase,end,metric,team)
        else:expected=records(m,phase,f['start'],end,metric,team=team,
                              player=c['subject']['id'] if c['subject']['kind']=='player' else None)
        assert [e['id'] for e in c['evidence']]==[e['id'] for e in expected],c['id']
        assert c['evidence']==expected
        assert len(expected)==f['value']
        assert f['end']==result['as_of']['minute'] and phase==result['as_of']['phase']
        if c['family']=='phase_change':
            pre=records(m,phase,f['pre_start'],f['start'],metric,team)
            assert len(pre)==f['pre_count']<=f['cap'] and pre==c['precondition_evidence']
        groups=[[],[],[]] if c['family']=='phase_change' else [[]]
        for hm in history:
            if hm['phases'].get(str(phase),0)<end:continue
            if c['family']=='personal_record':
                for hp,meta in hm['players'].items():
                    if meta['identity']!=f['player_identity'] or meta.get('seen_by',{}).get(str(phase),float('inf'))>=end*60000:continue
                    value=len(records(hm,phase,0,end,metric,player=hp))
                    groups[0].append(dict(match_id=hm['match_id'],team_id=meta['team_id'],player_id=hp,value=value))
                continue
            for ht in hm['teams']:
                if c['family']=='phase_change' and len(records(hm,phase,f['pre_start'],f['start'],metric,ht))>f['cap']:continue
                value=len(run(hm,phase,end,metric,ht)) if c['family']=='streak' else len(records(hm,phase,f['start'],end,metric,ht))
                item=dict(match_id=hm['match_id'],team_id=ht,value=value)
                groups[0].append(item)
                if c['family']=='phase_change':
                    if sign(hm,ht,phase,f['start'])==sign(m,team,phase,f['start']):groups[1].append(item)
                    h_opp=len([e for e in records(hm,phase,0,f['start'],metric) if e['team_id']!=ht])
                    x_opp=len([e for e in records(m,phase,0,f['start'],metric) if e['team_id']!=team])
                    if (h_opp<=f['context_cutoff'])==(x_opp<=f['context_cutoff']):groups[2].append(item)
        ratios=[]
        assert len(groups)==len(c['references'])
        for group,ref in zip(groups,c['references']):
            assert group==ref['observations'],c['id']
            n=len(group);k=sum(x['value']>=f['value'] for x in group)
            assert n==ref['n'] and k==ref['tail_count']
            assert abs(ref['smoothed_tail']-float(Fraction(k+1,n+1)))<1e-12
            ratios.append(Fraction(k+1,n+1));checks+=n
        assert abs(c['robust_tail']-float(max(ratios)))<1e-12
        if c['family']=='personal_record':assert f['value']>max(r['value'] for r in groups[0])
    return dict(cards_verified=len(result['cards']),reference_observations_recounted=checks)
