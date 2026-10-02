"""Slow independent reconstruction of expanded metric cards and coverage."""

def verify_metric(m,history,c,phase,end):
    f=c['fact'];metric=f['metric'];pid=c['subject']['id'] if c['subject']['kind']=='player' else None
    def available(x,team,player):
        if metric in ('Buyback','ObserverWard'):
            ps=[p for k,p in x['players'].items() if p['team_id']==team and (player is None or k==player)]
            return bool(ps) and (player is not None or len(ps)==5) and all(p.get('metric_coverage',{}).get(metric) is True for p in ps)
        return x.get('event_coverage',{}).get(metric) is True
    def scan(x,team,player):
        return sorted([e for e in x['events'] if e['kind']==metric and e['phase']==phase and e['team_id']==team
            and (player is None or e['player_id']==player) and f['start']*60000<=e['time_ms']<end*60000],key=lambda e:(e['time_ms'],e['id']))
    assert f['phase']==phase and f['end']==end
    team=c['subject']['team_id'];assert available(m,team,pid)
    ev=scan(m,team,pid);assert c['evidence']==ev and f['value']==len(ev)
    rows=[]
    for hm in history:
        if hm['phases'].get(str(phase),0)<end:continue
        if c['family']=='metric_burst':subjects=[(t,None) for t in hm['teams']]
        elif pid is None:
            identity=m['team_identities'][team];assert f['entity_identity']==identity
            subjects=[(t,None) for t,i in hm.get('team_identities',{}).items() if i==identity]
        else:
            identity=m['players'][pid]['identity'];assert f['entity_identity']==identity
            subjects=[(p['team_id'],k) for k,p in hm['players'].items() if p.get('identity')==identity
                and p.get('seen_by',{}).get(str(phase),float('inf'))<end*60000]
        for ht,hp in subjects:
            if not available(hm,ht,hp):continue
            row=dict(match_id=hm['match_id'],team_id=ht,value=len(scan(hm,ht,hp)))
            if hp is not None:row['player_id']=hp
            rows.append(row)
    assert len(c['references'])==1
    ref=c['references'][0];assert ref['observations']==rows
    assert ref['n']==len(rows) and ref['tail_count']==sum(r['value']>=len(ev) for r in rows)
    assert abs(ref['smoothed_tail']-(ref['tail_count']+1)/(len(rows)+1))<1e-12
    assert c['robust_tail']==ref['smoothed_tail']
    if c['family']=='metric_record':assert len(rows)>=5 and len(ev)>f['previous_max']==max(r['value'] for r in rows)
    else:assert len(rows)>=30 and c['robust_tail']<=.15
    return len(rows)
