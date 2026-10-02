import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from highlights.api import make_server, dispatch
from highlights.adapters import dota2, epl
from highlights.engine import Miner, events_between, longest_run, select, state_at
from highlights.store import Store
from highlights.verify import verify_response


def match(mid='current',role='development',start=100000):
    return dict(mode='dota2',match_id=mid,title='A vs B',date='test',role=role,start_order=start,end_order=start+3000,
                partition='patch=1;mode=2',teams={'0':'A','1':'B'},players={},phases={'1':45},events=[],source={},coverage='test')


def event(mid,sec,team='0'):
    return dict(id=f'{mid}:{sec}:{team}',phase=1,time_ms=sec*1000,team_id=team,player_id=None,kind='Kill',source={})


class MemoryStore:
    def __init__(self,ms):self.ms=ms
    def corpus(self,mode):return self.ms
    def match(self,mode,mid):return next(m for m in self.ms if m['match_id']==str(mid))
    def history(self,current):return Store.history(self,current)
    def list_matches(self,mode):return []


class Correctness(unittest.TestCase):
    def test_half_open_boundaries(self):
        m=match();m['events']=[event('x',0),event('x',299),event('x',300)]
        self.assertEqual(len(events_between(m,1,0,5,'Kill')),2)

    def test_opposing_simultaneous_events_break_streak(self):
        m=match();m['events']=[event('x',1),event('x',2),event('x',2,'1'),event('x',3)]
        self.assertEqual(len(longest_run(m,1,5,'Kill','0')),1)

    def test_reference_leakage_and_partition(self):
        cur=match();good=match('good','historical',0);future=match('future','historical',200000)
        overlap=match('overlap','historical',99000);other=match('other','historical',0);other['partition']='patch=2'
        self.assertEqual([m['match_id'] for m in MemoryStore([cur,good,future,overlap,other]).history(cur)],['good'])

    def test_empty_history_does_not_invent_highlights(self):
        m=match();m['events']=[event('x',s) for s in range(1,9)]
        result=Miner(MemoryStore([m])).analyze('dota2','current',10)
        self.assertEqual(result['cards'],[])
        self.assertEqual(result['status'],'no_highlights')

    def test_future_events_do_not_change_facts(self):
        cur=match();cur['events']=[event('x',s) for s in range(301,308)]+[event('x',1200,'1')]
        history=[match(str(i),'historical',i*100) for i in range(20)]
        store=MemoryStore([cur]+history)
        one=Miner(store).analyze('dota2','current',10)
        self.assertTrue(one['cards']);verify_response(store,one)
        cur['events'][-1]['team_id']='0'
        cur['events']+=[event('future',s) for s in range(1300,1320)]
        two=Miner(store).analyze('dota2','current',10)
        self.assertEqual(one['cards'],two['cards'])

    def test_evidence_tampering_detected(self):
        cur=match();cur['events']=[event('x',s) for s in range(301,308)]
        store=MemoryStore([cur]+[match(str(i),'historical',i*100) for i in range(20)])
        r=Miner(store).analyze('dota2','current',10);r['cards'][0]['fact']['value']+=1
        with self.assertRaises(AssertionError):verify_response(store,r)

    def test_reference_denominator_tampering_detected(self):
        cur=match();cur['events']=[event('x',s) for s in range(301,308)]
        store=MemoryStore([cur]+[match(str(i),'historical',i*100) for i in range(20)])
        r=Miner(store).analyze('dota2','current',10);r['cards'][0]['references'][0]['n']+=1
        with self.assertRaises(AssertionError):verify_response(store,r)

    def test_invalid_requests(self):
        miner=Miner(MemoryStore([match()]))
        for body in [{'mode':'nba','match_id':'current'},{'mode':'dota2'},
                     {'mode':'dota2','match_id':'current','raw':{}},
                     {'mode':'dota2','match_id':'current','as_of_minute':True},
                     {'mode':'dota2','match_id':'current','as_of_minute':100},
                     {'mode':'dota2','match_id':'current','phase':2}]:
            with self.assertRaises(ValueError):dispatch(miner,body)

    def test_missing_logs_not_zero(self):
        players=[dict(player_slot=i if i<5 else i+123,kills=0,kills_log=None) for i in range(10)]
        with self.assertRaises(ValueError):dota2({'match_id':1,'patch':1,'game_mode':2,'players':players})

    def test_anonymous_player_not_a_cross_match_identity(self):
        players=[dict(player_slot=i if i<5 else i+123,kills=0,kills_log=[],hero_id=i,account_id=4294967295) for i in range(10)]
        m=dota2(dict(match_id=1,patch=1,game_mode=2,players=players,duration=1200,start_time=10000))
        self.assertTrue(all(p['identity'] is None for p in m['players'].values()))

    def test_epl_own_goal_and_second_half_clock(self):
        meta=dict(match_id=1,competition={'competition_id':2},season={'season_id':27},
                  match_date='2016-01-01',kick_off='15:00:00.000',home_score=0,away_score=1,
                  home_team={'home_team_id':1,'home_team_name':'Home'},away_team={'away_team_id':2,'away_team_name':'Away'})
        def row(eid,phase,stamp,kind,team):
            return dict(id=eid,index=int(eid),period=phase,timestamp=stamp,type={'name':kind},team={'id':team})
        raw=[row('1',1,'00:46:00.000','Own Goal Against',1),row('2',1,'00:46:00.000','Own Goal For',2),
             row('3',1,'00:47:00.000','Half End',1),row('4',2,'00:45:00.000','Half End',1),
             dict(row('5',2,'00:00:01.000','Shot',2),shot={'outcome':{'name':'Saved'}})]
        m=epl(raw,meta)
        self.assertEqual(sum(e['kind']=='Goal' for e in m['events']),1)
        self.assertEqual(state_at(m,'2',2,0),1)
        self.assertEqual(len(events_between(m,2,0,5,'Shot')),1)
        self.assertEqual(len(events_between(m,1,0,45,'Goal')),0)

    def test_replay_deduplicates(self):
        cur=match();cur['phases']={'1':20};cur['events']=[event('x',s) for s in range(301,308)]
        store=MemoryStore([cur]+[match(str(i),'historical',i*100) for i in range(20)])
        replay=Miner(store).replay('dota2','current')
        cards=[c for s in replay['snapshots'] for c in s['cards']]
        self.assertEqual(len(cards),1)
