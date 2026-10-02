"""Slow independent historical-card audit (no history miner/index imports)."""
from itertools import groupby


def subjects(m,kind,identity):
    if kind=='team':
        return [(t,t) for t,i in m.get('team_identities',{}).items() if i==identity]
    return [(p,meta['team_id']) for p,meta in m['players'].items() if meta.get('identity')==identity]


def measure(m,local,team,kind,r):
    complete=m['phases'].get(str(r['phase']),0)>=r['end']
    if kind=='player':
        complete=complete and m['players'][local].get('seen_by',{}).get(str(r['phase']),float('inf'))<r['end']*60000
        if r['metric']=='Death':complete=complete and m['players'][local].get('death_log_complete',False)
    if r['metric']!='Death':complete=complete and m.get('event_coverage',{}).get(r['metric'],False)
    value=pre=None;holds=None;ids=[]
    if complete:
        events=[e for e in m['events'] if e['phase']==r['phase'] and e['kind']==r['metric']
                and e['team_id']==team and (kind=='team' or e['player_id']==local)
                and 0<=e['time_ms']<r['end']*60000]
        boundary=r.get('boundary',0)*60000
        pre=sum(e['time_ms']<boundary for e in events);value=len(events)-pre
        holds=value>=r['minimum'] if 'minimum' in r else value<=r['maximum']
        if boundary:holds=holds and pre<=r['cap']
        ids=[e['id'] for e in sorted(events,key=lambda e:(e['time_ms'],e['id']))]
    return dict(match_id=m['match_id'],local_id=local,team_id=team,complete=bool(complete),
                start_order=m['start_order'],end_order=m['end_order'],holds=holds,value=value,pre_count=pre,evidence_ids=ids)


def verify_card(current,history,c):
    s=c['subject'];r=c['fact']['rule'];kind=s['kind'];identity=s['identity']
    pairs=[]
    for m in sorted(history,key=lambda m:(m['start_order'],m['match_id'])):
        for local,team in subjects(m,kind,identity):pairs.append((m,local,team))
    assert (s['id'],s['team_id']) in subjects(current,kind,identity)
    if c['family']=='cross_match_events':
        chain=[];previous_end=None
        for m,local,team in pairs+[(current,s['id'],s['team_id'])]:
            if previous_end is not None and previous_end>=m['start_order']:chain=[]
            previous_end=m['end_order']
            if m['mode']=='dota2' and not m['players'][local].get('death_log_complete',False):chain=[];continue
            ev=[e for e in m['events'] if e['player_id']==local and e['team_id']==team and e['time_ms']>=0
                and e['kind'] in (('Shot',) if m['mode']=='epl' else ('Kill','Death'))]
            if m is current:ev=[e for e in ev if e['phase']<r['phase'] or e['phase']==r['phase'] and e['time_ms']<r['end']*60000]
            ev.sort(key=lambda e:(e['phase'],e['time_ms'],e['id']))
            for _,group in groupby(ev,key=lambda e:(e['phase'],e['time_ms'])):
                group=list(group)
                if m['mode']=='dota2' and any(e['kind']=='Death' for e in group):chain=[];continue
                for e in group:
                    if m['mode']=='epl' and e.get('is_goal') is not False:chain=[]
                    else:chain.append(dict(e,match_id=m['match_id']))
        assert chain==c['evidence'] and len(chain)==c['fact']['value']
        assert list(dict.fromkeys(e['match_id'] for e in chain))==c['history']['event_match_ids']
        all_ids=[m['match_id'] for m,_,_ in pairs]+[current['match_id']]
        start=all_ids.index(chain[0]['match_id'])
        assert all_ids[start:]==c['history']['span_match_ids']
        assert len(c['history']['event_match_ids'])>=2
        return len(pairs)
    rows=[measure(m,local,team,kind,r) for m,local,team in pairs]
    now=measure(current,s['id'],s['team_id'],kind,r)
    assert now==c['history']['current_observation'] and now['holds'] is True
    assert rows==c['history']['observations']
    assert c['evidence']==[dict(e) for e in current['events'] if e['id'] in now['evidence_ids']]
    assert c['history']['comparable_matches']==sum(x['complete'] for x in rows)
    assert c['history']['unknown_matches']==sum(not x['complete'] for x in rows)
    if c['family']=='historical_occurrence':
        ids=[row['match_id'] for row in rows if row['holds'] is True]+[current['match_id']]
        assert ids==c['history']['occurrence_match_ids']
        assert c['history']['previous_occurrences']==len(ids)-1
        assert c['fact']['value']==len(ids)
    else:
        # Forward scan with reset is deliberately different from reverse mining.
        chain=[];last_end=None
        for row in rows+[now]:
            if row['holds'] is not True:chain=[]
            else:
                if last_end is not None and last_end>=row['start_order']:chain=[]
                chain.append(row['match_id'])
            last_end=row['end_order']
        assert chain==c['history']['streak_match_ids'] and len(chain)==c['fact']['value']
    return len(rows)
