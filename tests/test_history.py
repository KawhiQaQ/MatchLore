import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from highlights.store import Store
from highlights.history import mine_history,TimelineIndex
from highlights.verify_history import verify_card
from highlights.verify import verify_response
from highlights.engine import Miner
from highlights.api import make_server,ingest


def sample(mid,start,mode='dota2',role='historical'):
    metric='Kill' if mode=='dota2' else 'Shot'
    return dict(schema_version=2,mode=mode,match_id=str(mid),title='A vs B',date='synthetic',role=role,
                start_order=start,end_order=start+2400,partition='p1',teams={'0':'A','1':'B'},team_identities={'0':'stable-A','1':'stable-B'},
                players={'0':dict(name='Player',identity='person1',team_id='0',seen_by={'1':0},death_log_complete=True)},
                phases={'1':40},events=[],source={'raw_sha256':str(mid)},event_coverage={metric:True},coverage='test')


def event(m,second,kind='Kill',goal=False):
    e=dict(id=f'{m["match_id"]}:{kind}:{second}',phase=1,time_ms=second*1000,team_id='0',player_id='0',kind=kind,source={})
    if kind=='Shot':e['is_goal']=goal
    m['events'].append(e)


class HistoryTests(unittest.TestCase):
    def data(self):
        old=[sample(i,i*5000) for i in range(5)]
        cur=sample('current',30000,role='development')
        return old,cur

    def test_match_streak_and_ordinal_recount(self):
        history,current=self.data()
        for m in history+[current]:
            for s in (650,660,670):event(m,s)
        cards=mine_history(current,history,1,20)
        records=[c for c in cards if c['family']=='historical_occurrence' and c['subject']['kind']=='player']
        self.assertEqual(records[0]['fact']['value'],6)
        for c in cards:verify_card(current,history,c)

    def test_false_result_breaks_streak_but_not_total_occurrences(self):
        history,current=self.data()
        for m in history+[current]:
            for s in (650,660,670):event(m,s)
        event(history[-2],300,'Death')
        cards=mine_history(current,history,1,20)
        zero=[c for c in cards if c['family']=='match_streak' and c['fact']['rule']['id']=='zero_deaths']
        self.assertEqual(zero,[])
        total=[c for c in cards if c['family']=='historical_occurrence' and c['subject']['kind']=='player']
        self.assertEqual(total[0]['fact']['value'],6)

    def test_missing_deaths_break_zero_and_cross_event_runs(self):
        history,current=self.data()
        for m in history+[current]:event(m,300)
        history[-1]['players']['0']['death_log_complete']=False
        cards=mine_history(current,history,1,20)
        self.assertFalse(any(c['family']=='cross_match_events' for c in cards))
        self.assertFalse(any(c['family']=='match_streak' and c['fact']['rule']['id']=='zero_deaths' for c in cards))

    def test_cross_match_events_and_death_boundary(self):
        history,current=self.data()
        event(history[-2],100,'Death')
        for m in history[-2:]+[current]:
            event(m,300);event(m,310)
        cards=[c for c in mine_history(current,history,1,20) if c['family']=='cross_match_events']
        self.assertEqual(cards[0]['fact']['value'],6)
        self.assertEqual(len(cards[0]['history']['event_match_ids']),3)
        verify_card(current,history,cards[0])
        event(current,310,'Death')
        self.assertFalse(any(c['family']=='cross_match_events' for c in mine_history(current,history,1,20)))

    def test_epl_goal_breaks_cross_match_drought(self):
        history=[sample(i,i*5000,'epl') for i in range(3)];current=sample('cur',20000,'epl')
        for m in history+[current]:
            for s in (100,200,300):event(m,s,'Shot')
        c=next(c for c in mine_history(current,history,1,30) if c['family']=='cross_match_events')
        self.assertEqual(c['fact']['value'],12);verify_card(current,history,c)
        current['events'][0]['is_goal']=True
        self.assertFalse(any(c['family']=='cross_match_events' for c in mine_history(current,history,1,30)))

    def test_zero_event_appearance_counts_in_span(self):
        history=[sample(i,i*5000,'epl') for i in range(3)];current=sample('cur',20000,'epl')
        for m in (history[0],history[2],current):
            for s in (100,200,300):event(m,s,'Shot')
        c=next(c for c in mine_history(current,history,1,30) if c['family']=='cross_match_events')
        self.assertEqual(len(c['history']['event_match_ids']),3)
        self.assertEqual(len(c['history']['span_match_ids']),4)
        verify_card(current,history,c)

    def test_future_events_do_not_change_historical_cards(self):
        history,current=self.data()
        for m in history+[current]:
            event(m,200);event(m,210)
        before=mine_history(current,history,1,10)
        event(current,1800,'Death')
        after=mine_history(current,history,1,10)
        self.assertEqual(before,after)

    def test_team_side_is_not_global_team_identity(self):
        history,current=self.data()
        for m in history:m['team_identities']['0']='other-team'
        for m in history+[current]:
            for s in range(100,110):event(m,s)
        cards=mine_history(current,history,1,20)
        self.assertFalse(any(c['subject']['kind']=='team' for c in cards))

    def test_unknown_short_match_breaks_streak(self):
        history,current=self.data();history[-1]['phases']['1']=5
        self.assertFalse(any(c['family']=='match_streak' for c in mine_history(current,history,1,20)))

    def test_overlapping_matches_break_streak(self):
        history,current=self.data()
        history[-2]['end_order']=history[-1]['start_order']+10
        self.assertFalse(any(c['family']=='match_streak' for c in mine_history(current,history,1,20)))

    def test_tampered_occurrence_is_rejected(self):
        history,current=self.data()
        for m in history+[current]:
            for s in (650,660,670):event(m,s)
        c=next(c for c in mine_history(current,history,1,20) if c['family']=='historical_occurrence')
        c['fact']['value']+=1
        with self.assertRaises(AssertionError):verify_card(current,history,c)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root)

    def test_restart_idempotency_and_conflict(self):
        m=sample('one',0,role='development')
        self.assertEqual(self.store.ingest(m)['status'],'committed')
        other=Store(self.root)
        self.assertEqual(other.ingest(m)['status'],'already_present')
        self.assertEqual(len(other.corpus('dota2')),1)
        bad=copy.deepcopy(m);event(bad,20)
        with self.assertRaises(ValueError):other.ingest(bad)
        self.assertEqual(len(other.corpus('dota2')),1)

    def test_future_overlap_partition_and_current_excluded(self):
        cur=sample('current',10000)
        for m in [sample('past',0),sample('overlap',9000),sample('future',20000),cur]:self.store.ingest(m)
        other=sample('wrong',1);other['partition']='p2';self.store.ingest(other)
        self.assertEqual([m['match_id'] for m in self.store.history(cur)],['past'])

    def test_late_arrival_visible_to_existing_store_instance(self):
        cur=sample('current',10000);reader=Store(self.root)
        self.assertEqual(reader.history(cur),[])
        self.store.ingest(sample('past',0))
        self.assertEqual(len(reader.history(cur)),1)

    def test_existing_miner_refreshes_after_late_ingest(self):
        current=sample('cur',30000);self.store.ingest(current);miner=Miner(self.store)
        self.assertEqual(miner.analyze('dota2','cur',10)['cards'],[])
        for i in range(3):self.store.ingest(sample(i,i*5000))
        result=miner.analyze('dota2','cur',10)
        self.assertTrue(any(c['family']=='match_streak' and c['fact']['value']==4 for c in result['cards']))
        verify_response(self.store,result)

    def test_ingest_rejects_unfinished_dota(self):
        with self.assertRaises(ValueError):ingest(self.store,{'mode':'dota2','raw':{'radiant_win':None}})

    def test_concurrent_duplicate_commits_exactly_once(self):
        m=sample('one',0)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:Store(self.root).ingest(m),range(4)))
        self.assertEqual(sum(r['status']=='committed' for r in results),1)
        self.assertEqual(len(self.store.corpus('dota2')),1)
