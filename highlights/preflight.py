"""Read-only source diagnostics, shared with writes so checks cannot drift."""
import math
import re
from collections import Counter
from .adapters import ADAPTERS
from .errors import AppError


def source_errors(mode, raw, metadata=None):
    errors = []
    def issue(path, message):
        errors.append(dict(code='invalid_field', path=path, message=message))
    def required(obj, fields, path):
        if not isinstance(obj, dict):
            issue(path, 'Expected an object'); return False
        for field in fields:
            if obj.get(field) is None or obj.get(field) == '':
                issue(path+'.'+field, 'Required field is missing or empty')
        return True
    def number(value, path, integer=False, minimum=None):
        if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or (minimum is not None and value < minimum):
            issue(path, 'Expected a finite '+('integer' if integer else 'number')+(f' >= {minimum}' if minimum is not None else ''))
    def identity(value, path):
        if type(value) not in (int, str) or not str(value).strip():
            issue(path, 'Expected a nonempty string or integer ID')
    if mode == 'epl':
        if not required(metadata, ['match_id','competition','season','home_team','away_team','home_score','away_score','match_date','kick_off'], 'metadata'):
            metadata = {}
        identity(metadata.get('match_id'), 'metadata.match_id')
        for name, fields in [('competition',['competition_id']),('season',['season_id']),('home_team',['home_team_id','home_team_name']),('away_team',['away_team_id','away_team_name'])]:
            required(metadata.get(name), fields, 'metadata.'+name)
        for side in ('home','away'):number(metadata.get(side+'_score'), 'metadata.'+side+'_score', True, 0)
        if not isinstance(raw, list):
            issue('raw', 'Expected a StatsBomb event array'); return errors
        ids = set()
        for i, row in enumerate(raw):
            path = f'raw[{i}]'
            if not required(row, ['id','period','timestamp','type','team'], path):continue
            identity(row.get('id'), path+'.id')
            eid = str(row.get('id'))
            if eid in ids:issue(path+'.id', 'Duplicate event ID')
            ids.add(eid)
            number(row.get('period'), path+'.period', True, 1)
            stamp = row.get('timestamp')
            if not isinstance(stamp,str) or not re.fullmatch(r'\d{2}:([0-5]\d):([0-5]\d)(\.\d+)?',stamp):issue(path+'.timestamp','Expected HH:MM:SS[.fraction] with valid minute/second ranges')
            required(row.get('type'), ['name'], path+'.type')
            required(row.get('team'), ['id'], path+'.team')
            if row.get('player') is not None:required(row['player'], ['id','name'], path+'.player')
            kind = row.get('type',{}).get('name') if isinstance(row.get('type'),dict) else None
            if kind in ('Shot','Own Goal Against','Pass','Dribble'):number(row.get('index'),path+'.index',True,0)
            if kind=='Shot':
                shot=row.get('shot')
                if required(shot,['outcome'],path+'.shot'):required(shot.get('outcome'),['name'],path+'.shot.outcome')
    elif mode == 'dota2':
        if not required(raw, ['match_id','start_time','duration','patch','game_mode','players','radiant_win'], 'raw'):return errors
        identity(raw.get('match_id'),'raw.match_id')
        number(raw.get('duration'),'raw.duration',True,300)
        number(raw.get('start_time'),'raw.start_time',False,0)
        if type(raw.get('radiant_win')) is not bool:issue('raw.radiant_win','Completed match requires a boolean final result')
        players=raw.get('players')
        if not isinstance(players,list) or len(players)!=10:
            issue('raw.players','Expected exactly 10 players'); return errors
        slots=set()
        for i,p in enumerate(players):
            path=f'raw.players[{i}]'
            if not required(p,['player_slot','hero_id','kills','kills_log'],path):continue
            slot=p.get('player_slot')
            if type(slot) is not int or slot not in (0,1,2,3,4,128,129,130,131,132):issue(path+'.player_slot','Invalid player slot')
            elif slot in slots:issue(path+'.player_slot','Duplicate player slot')
            else:slots.add(slot)
            number(p.get('kills'),path+'.kills',True,0)
            logs=p.get('kills_log')
            if not isinstance(logs,list):issue(path+'.kills_log','Required parsed kill log must be an array');continue
            if len(logs)!=p.get('kills'):issue(path+'.kills_log','Log count differs from final kills')
            for j,e in enumerate(logs):
                ep=f'{path}.kills_log[{j}]'
                if not required(e,['time'],ep):continue
                t=e.get('time');number(t,ep+'.time')
                if type(t) in (int,float) and type(raw.get('duration')) in (int,float) and t>raw['duration']:issue(ep+'.time','Event occurs after match end')
    else:
        issue('mode','mode must be epl or dota2')
    return errors


def normalize_source(mode, raw, metadata=None):
    errors=source_errors(mode,raw,metadata)
    if errors:
        raise AppError('invalid_source', '; '.join(x['path']+': '+x['message'] for x in errors[:12]))
    try:
        return ADAPTERS[mode](raw,metadata)
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as exc:
        raise AppError('invalid_source', 'Source reconciliation failed: '+str(exc)) from exc


def coverage_report(m):
    warnings=[]
    def warn(code,message):warnings.append(dict(code=code,message=message))
    if m['mode']=='epl':
        coverage={k:dict(available=v) for k,v in m.get('event_coverage',{}).items()}
        warn('provider_coverage','EPL event coverage follows the StatsBomb source contract; an absent event type alone cannot distinguish zero events from an incomplete export.')
    else:
        players=list(m['players'].values())
        coverage={k:dict(available=v) for k,v in m['event_coverage'].items()}
        for metric in ('Buyback','ObserverWard','Death'):
            n=sum(p.get('death_log_complete',False) if metric=='Death' else p.get('metric_coverage',{}).get(metric,False) for p in players)
            coverage[metric]=dict(available=n==len(players),covered_players=n,total_players=len(players))
            if n<len(players):warn('incomplete_'+metric,f'{metric}: complete logs for {n}/{len(players)} players; affected player/team statistics are excluded, not counted as zero.')
        if not m['event_coverage']['TowerLoss']:warn('incomplete_TowerLoss','Missing or inconsistent objectives; tower-loss statistics are excluded.')
    anonymous=sum(not p.get('identity') for p in m['players'].values())
    if anonymous:warn('missing_player_identity',f'{anonymous} players lack stable identities; cross-match player records are unavailable for them.')
    if any(not x for x in m.get('team_identities',{}).values()):warn('missing_team_identity','Missing stable team IDs; affected cross-match team records are unavailable.')
    return coverage,warnings


def check_data(store, body):
    if not isinstance(body,dict) or set(body)-{'mode','raw','metadata'} or 'raw' not in body:
        raise AppError('invalid_request','Supply mode, raw and optional metadata')
    mode=body.get('mode');errors=source_errors(mode,body['raw'],body.get('metadata'))
    report=dict(valid=False,can_ingest=False,errors=errors,warnings=[],coverage={})
    if errors:return report
    try:m=normalize_source(mode,body['raw'],body.get('metadata'))
    except AppError as exc:
        report['errors'].append(dict(code=exc.code,path='source',message=str(exc)));return report
    report['valid']=True;report['can_ingest']=True
    report['match']=dict(mode=mode,match_id=m['match_id'],title=m['title'],partition=m['partition'],phases=m['phases'],events=dict(Counter(e['kind'] for e in m['events'])))
    report['coverage'],report['warnings']=coverage_report(m)
    history=store.history(m)
    report['eligible_history_matches']=len(history)
    if not history:report['warnings'].append(dict(code='no_history',message='No eligible earlier history in this season/patch; reference-based highlights may be unavailable.'))
    try:
        state=store.history_status(mode,m['match_id']);report['existing']=state
        conflict=state['status']=='withdrawn' or state['fingerprint']!=store.fingerprint(m)
        report['can_ingest']=not conflict
        if conflict:report['warnings'].append(dict(code='explicit_replace_required',message='This ID is withdrawn or has different content; use replace with its current revision.'))
    except AppError as exc:
        if exc.status!=404:raise
    return report


def check_batch(store, body):
    if not isinstance(body,dict) or set(body)!={'matches'} or not isinstance(body['matches'],list) or not 1<=len(body['matches'])<=1000:
        raise AppError('invalid_batch','Supply matches array with 1..1000 raw sources')
    reports=[];seen={}
    for index,item in enumerate(body['matches']):
        report=check_data(store,item);report['index']=index
        if report['valid']:
            mode=item['mode'];m=normalize_source(mode,item['raw'],item.get('metadata'))
            key=(mode,m['match_id']);fp=store.fingerprint(m)
            if key in seen and seen[key][0]!=fp:
                for r in (report,reports[seen[key][1]]):
                    r['can_ingest']=False
                    r['errors'].append(dict(code='batch_conflict',path='match_id',message='Same match ID has conflicting content within this batch'))
            else:seen[key]=(fp,index)
        reports.append(report)
    return dict(valid=all(r['valid'] for r in reports),can_ingest=all(r['can_ingest'] for r in reports),results=reports)
