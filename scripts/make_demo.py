"""Generate fictional fixtures for an offline EPL/Dota smoke test.

No real match records, private data, API calls, or API keys are used.
"""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import tempfile

from highlights.adapters import ADAPTERS


def football(index, current=False):
    mid='epl-demo' if current else f'epl-history-{index:02d}'
    date=(datetime(2020,1,1)+timedelta(days=index)).date().isoformat()
    metadata=dict(match_id=mid,competition={'competition_id':2},season={'season_id':2020},
        match_date=date,kick_off='15:00:00.000',home_score=0,away_score=0,
        home_team={'home_team_id':1,'home_team_name':'Demo Home'},
        away_team={'away_team_id':2,'away_team_name':'Demo Away'})
    events=[]
    def row(phase,seconds,kind,player=False):
        m,s=divmod(seconds,60)
        r=dict(id=f'{mid}:{len(events)}',index=len(events),period=phase,
            timestamp=f'00:{m:02d}:{s:02d}.000',type={'name':kind},team={'id':1})
        if player:r['player']={'id':10,'name':'Demo Forward'}
        return r
    for sec in ([1210+i*12 for i in range(8)] if current else [300+index,1000+index]):
        r=row(1,sec,'Shot',True);r['shot']={'outcome':{'name':'Saved'}};events.append(r)
    events.append(row(1,2700,'Half End'));events.append(row(2,2700,'Half End'))
    return events,metadata


def dota(index, current=False):
    mid='dota-demo' if current else f'dota-history-{index:02d}'
    times=[1210+i*12 for i in range(8)] if current else [400+index,900+index]
    players=[]
    for i in range(10):
        slots=i if i<5 else i+123
        kills=[{'time':t} for t in times] if i==0 else []
        deaths=[{'time':t} for t in times] if i==5 else []
        buyback=[{'time':t} for t in (1510,1550,1590)] if current and i==5 else []
        players.append(dict(player_slot=slots,account_id=1000+i,hero_id=i+1,name=f'Demo Player {i+1}',
            kills=len(kills),kills_log=kills,deaths=len(deaths),deaths_log=deaths,
            buyback_count=len(buyback),buyback_log=buyback,obs_placed=0,obs_log=[]))
    return dict(match_id=mid,start_time=int(datetime(2020,1,1,tzinfo=timezone.utc).timestamp())+index*86400,
        duration=2400,patch=1,game_mode=2,radiant_win=True,radiant_team_id=100,dire_team_id=200,
        radiant_name='Demo Radiant',dire_name='Demo Dire',players=players,objectives=[]),None


def make_demo(destination):
    dest=Path(destination).expanduser().absolute()
    if dest.exists():raise ValueError('Destination already exists; choose a new directory')
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.matchlore-demo-',dir=dest.parent) as tmp:
        stage=Path(tmp)/'data';stage.mkdir()
        for mode,builder in [('epl',football),('dota2',dota)]:
            folder=stage/mode;(folder/'matches').mkdir(parents=True);(folder/'raw').mkdir()
            rows=[];manifest=[]
            for index in list(range(20))+[30]:
                current=index==30;raw,metadata=builder(index,current)
                match=ADAPTERS[mode](raw,metadata);mid=match['match_id']
                match['role']='development' if current else 'historical'
                match['source']['data_origin']='synthetic_demo'
                name=f'matches/{mid}.json.gz'
                with gzip.open(folder/name,'wt',encoding='utf-8') as f:json.dump(match,f,ensure_ascii=False)
                rows.append(dict(match_id=mid,role=match['role'],file=name))
                raw_path=f'raw/{mid}.json';(folder/raw_path).write_text(json.dumps(raw,ensure_ascii=False),encoding='utf-8')
                item={'raw':raw_path}
                if metadata:
                    meta_path=f'raw/{mid}.metadata.json';(folder/meta_path).write_text(json.dumps(metadata),encoding='utf-8');item['metadata']=meta_path
                if not current:manifest.append(item)
            (folder/'catalog.json').write_text(json.dumps({'matches':rows}),encoding='utf-8')
            (folder/'history.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        (stage/'workspace.json').write_text(json.dumps({'format':'highlights-workspace-v1','data_origin':'synthetic_demo'}),encoding='utf-8')
        stage.rename(dest)
    return dict(data=str(dest),origin='synthetic_demo',history_per_mode=20,current_ids={'epl':'epl-demo','dota2':'dota-demo'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('demo-data'))
    print(json.dumps(make_demo(parser.parse_args().output)),flush=True)
