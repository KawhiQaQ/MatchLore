"""Source-specific semantics. No model-generated facts enter this layer."""
from datetime import datetime, timezone
import hashlib
import json
import math
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def timestamp_ms(value):
    h, m, s = value.split(':')
    return round((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)


def epl(raw, metadata):
    if not isinstance(raw, list) or not metadata:
        raise ValueError('epl requires StatsBomb events plus match metadata')
    if metadata['competition']['competition_id'] != 2:
        raise ValueError('epl mode accepts Premier League competition_id=2 only')
    teams = {str(metadata[f'{side}_team'][f'{side}_team_id']):
             metadata[f'{side}_team'][f'{side}_team_name'] for side in ('home', 'away')}
    if len(teams) != 2:
        raise ValueError('Expected two distinct teams')
    mid = str(metadata['match_id'])
    events, players, seen, ends = [], {}, set(), {}
    for r in raw:
        eid = str(r['id'])
        if eid in seen:
            raise ValueError('Duplicate source event ID')
        seen.add(eid)
        phase = r['period']
        if phase not in (1, 2):
            continue
        t = timestamp_ms(r['timestamp'])
        team = str(r['team']['id'])
        if team not in teams or t < 0:
            raise ValueError('Invalid event team or timestamp')
        typ = r['type']['name']
        if typ == 'Half End':
            ends[phase] = max(ends.get(phase, 0), t)
        p = r.get('player')
        if p:
            player=players.setdefault(str(p['id']),dict(name=p['name'],team_id=team,identity=str(p['id']),seen_by={}))
            player['seen_by'][str(phase)]=min(player['seen_by'].get(str(phase),t),t)
        if typ == 'Starting XI':
            for entry in r.get('tactics',{}).get('lineup',[]):
                person=entry['player']
                player=players.setdefault(str(person['id']),dict(name=person['name'],team_id=team,identity=str(person['id']),seen_by={}))
                player['seen_by']['1']=0
        extra=[]
        if typ=='Pass' and r.get('pass',{}).get('type',{}).get('name')=='Corner':extra.append('Corner')
        if typ=='Dribble' and r.get('dribble',{}).get('outcome',{}).get('name')=='Complete':extra.append('DribbleComplete')
        if typ=='Shot' and r.get('shot',{}).get('outcome',{}).get('name') in ('Goal','Saved','Saved to Post'):extra.append('ShotOnTarget')
        for metric in extra:
            events.append(dict(id=eid+':'+metric,phase=phase,time_ms=t,team_id=team,
                               player_id=str(p['id']) if p else None,kind=metric,
                               source=dict(event_id=eid,index=r['index'])))
        if typ not in ('Shot', 'Own Goal Against'):
            continue
        goal = typ == 'Own Goal Against' or r.get('shot', {}).get('outcome', {}).get('name') == 'Goal'
        credit = next(x for x in teams if x != team) if typ == 'Own Goal Against' else team
        base = dict(id=eid, phase=phase, time_ms=t, team_id=team,
                    player_id=str(p['id']) if p else None, source=dict(event_id=eid, index=r['index']))
        if typ == 'Shot':
            events.append(dict(base, kind='Shot',is_goal=goal))
        if goal:
            events.append(dict(base, id=eid+':goal', kind='Goal', team_id=credit))
    if any(ends.get(p, 0) < 45*60000 for p in (1, 2)):
        raise ValueError('Both full regulation halves must be present')
    for side in ('home', 'away'):
        tid = str(metadata[f'{side}_team'][f'{side}_team_id'])
        if sum(e['kind']=='Goal' and e['team_id']==tid for e in events) != metadata[f'{side}_score']:
            raise ValueError('Source goals do not reconcile with final score')
    # Source local time, not a claim that the feed supplied UTC. A conservative
    # four-hour end excludes overlapping fixtures from the reference pool.
    start = datetime.fromisoformat(metadata['match_date']+'T'+metadata['kick_off']).replace(tzinfo=timezone.utc).timestamp()
    return dict(schema_version=3, mode='epl', match_id=mid, title=' vs '.join(teams.values()),
                date=metadata['match_date'], start_order=start, end_order=start+4*3600,
                partition=str(metadata['season']['season_id']), teams=teams, players=players,
                team_identities={t:t for t in teams},event_coverage={k:True for k in ('Shot','Goal','ShotOnTarget','Corner','DribbleComplete')},
                phases={'1':45, '2':45}, events=sorted(events, key=lambda e:(e['phase'],e['time_ms'],e['id'])),
                source=dict(provider='StatsBomb', raw_sha256=digest(raw), metadata_sha256=digest(metadata)),
                coverage='完整常规两半场；挖掘窗口不含各半场补时，第二半场比分包含上半场补时进球')


def dota2(raw, metadata=None):
    if not isinstance(raw, dict) or len(raw.get('players', [])) != 10:
        raise ValueError('dota2 requires a parsed OpenDota match with 10 players')
    if raw.get('patch') is None or raw.get('game_mode') is None:
        raise ValueError('Missing patch or game mode')
    mid = str(raw['match_id'])
    teams = {'0':raw.get('radiant_name') or '天辉', '1':raw.get('dire_name') or '夜魇'}
    events, players = [], {}
    for p in raw['players']:
        if not isinstance(p,dict) or not isinstance(p.get('player_slot'),int) or p['player_slot'] not in (0,1,2,3,4,128,129,130,131,132):
            raise ValueError('Invalid or missing player slot')
        slot = str(p['player_slot'])
        if slot in players or not isinstance(p.get('kills_log'), list):
            raise ValueError('Missing kill log or duplicate player slot')
        if len(p['kills_log']) != p.get('kills'):
            raise ValueError('Kill log does not reconcile with player kills')
        account = p.get('account_id')
        identity = str(account) if account not in (None, 0, 4294967295) else None
        players[slot] = dict(name=p.get('name') or p.get('personaname') or f'英雄{p["hero_id"]}',
                             team_id=str(int(p['player_slot']>=128)), identity=identity, hero_id=p['hero_id'],seen_by={'1':0})
        players[slot]['metric_coverage']={}
        for log,count,metric in [('buyback_log','buyback_count','Buyback'),('obs_log','obs_placed','ObserverWard')]:
            entries=p.get(log)
            valid=(isinstance(entries,list) and len(entries)==p.get(count) and all(
                isinstance(e,dict) and type(e.get('time')) in (int,float) and math.isfinite(e['time'])
                and e['time']<=raw['duration'] for e in entries))
            players[slot]['metric_coverage'][metric]=valid
            if valid:
                for i,r in enumerate(entries):
                    events.append(dict(id=f'{mid}:{slot}:{log}:{i}',phase=1,time_ms=round(r['time']*1000),
                        team_id=players[slot]['team_id'],player_id=slot,kind=metric,
                        source=dict(player_slot=int(slot),log=log,index=i)))
        deaths=p.get('deaths_log')
        valid_deaths=(isinstance(deaths,list) and len(deaths)==p.get('deaths')
                      and all(isinstance(e,dict) and type(e.get('time')) in (int,float)
                              and e['time']<=raw['duration'] for e in deaths))
        players[slot]['death_log_complete']=valid_deaths
        if valid_deaths:
            for i,r in enumerate(deaths):
                events.append(dict(id=f'{mid}:{slot}:deaths_log:{i}',phase=1,time_ms=round(r['time']*1000),
                                   team_id=players[slot]['team_id'],player_id=slot,kind='Death',
                                   source=dict(player_slot=int(slot),log='deaths_log',index=i)))
        for i, r in enumerate(p['kills_log']):
            if not isinstance(r.get('time'), (int, float)):
                raise ValueError('Invalid kill timestamp')
            if r['time'] > raw['duration']:
                raise ValueError('Kill occurs after match end')
            events.append(dict(id=f'{mid}:{slot}:kills_log:{i}', phase=1, time_ms=round(r['time']*1000),
                               team_id=players[slot]['team_id'], player_id=slot, kind='Kill',
                               source=dict(player_slot=int(slot), log='kills_log', index=i)))
    objectives=raw.get('objectives')
    tower_rows=[];tower_valid=isinstance(objectives,list)
    if tower_valid:
        for i,r in enumerate(objectives):
            if r.get('type')!='building_kill' or '_tower' not in str(r.get('key','')):continue
            key=r['key']
            if (not re.fullmatch(r'npc_dota_(goodguys|badguys)_(?:tower[1-3]_(?:top|mid|bot)|tower4)',key)
                or type(r.get('time')) not in (int,float) or not math.isfinite(r['time'])
                or not 0<=r['time']<=raw['duration']):tower_valid=False;break
            tower_rows.append(dict(id=f'{mid}:objectives:{i}',phase=1,time_ms=round(r['time']*1000),
                team_id='0' if '_goodguys_' in key else '1',player_id=None,kind='TowerLoss',
                source=dict(log='objectives',index=i),building=key))
        from collections import Counter
        if any(n>(2 if key.endswith('_tower4') else 1) for key,n in Counter(e['building'] for e in tower_rows).items()):tower_valid=False
    if tower_valid:events.extend(tower_rows)
    return dict(schema_version=3, mode='dota2', match_id=mid, title=' vs '.join(teams.values()),
                date=datetime.fromtimestamp(raw['start_time'], timezone.utc).isoformat(),
                start_order=raw['start_time'], end_order=raw['start_time']+raw['duration'],
                partition=f'patch={raw["patch"]};mode={raw["game_mode"]}', teams=teams, players=players,
                team_identities={str(i):str(raw[f'{side}_team_id']) if raw.get(f'{side}_team_id') else None
                                 for i,side in enumerate(('radiant','dire'))},
                event_coverage={'Kill':True,'TowerLoss':tower_valid},
                phases={'1':min(60, raw['duration']//60)},
                events=sorted(events, key=lambda e:(e['time_ms'],e['id'])),
                source=dict(provider='OpenDota', raw_sha256=digest(raw)),
                coverage='开局起最多60分钟；击杀指玩家归属击杀日志，不等同所有情况下的记分牌；不计开局前事件')


ADAPTERS = {'epl': epl, 'dota2': dota2}
