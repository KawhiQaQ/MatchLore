import copy
import math
import unittest
from unittest.mock import patch
from highlights.quality import apply_quality
from highlights.narrative import guard,editorial_brief
from highlights import llm


def occurrence(k,n):
    return dict(id='a',family='historical_occurrence',subject=dict(kind='team',id='0',name='Team'),
        fact=dict(phase=1,end=20,start=0,metric='Kill',value=k+1,rule=dict(id='quiet_then_burst',metric='Kill',phase=1,end=20,boundary=10,cap=2,minimum=5)),
        evidence=[dict(id='e',phase=1,time_ms=1100000,source={})],references=[],robust_tail=None,
        history=dict(previous_occurrences=k,comparable_matches=n),rank_score=3+math.log(k+1),text='Team再次出现此表现。')


class SelectionStability(unittest.TestCase):
    def evaluate(self,c):return apply_quality([c],dict(match_id='current',mode='dota2'),1,20)

    def test_replicating_appearances_preserves_occurrence_score(self):
        a=self.evaluate(occurrence(2,40))[0][0];b=self.evaluate(occurrence(200,4000))[0][0]
        self.assertEqual(a['rank_score'],b['rank_score'])
        self.assertNotEqual(a['mining_score'],b['mining_score'])
        self.assertEqual(b['fact']['value'],201)

    def test_frequent_conditions_not_promoted_by_large_counts(self):
        for k,n in ((6,27),(225,1011)):
            accepted,info=self.evaluate(occurrence(k,n))
            self.assertEqual(accepted,[])
            self.assertEqual(info['rejected_by_reason'],{'routine_repeated_condition':1})

    def test_long_sequences_have_bounded_selection_score_not_truncated_facts(self):
        scores=[]
        for length in (30,300,3000):
            c=occurrence(2,40);c.update(family='cross_match_events',rank_score=2+math.log(length));c['fact']['value']=length
            out=self.evaluate(c)[0][0];scores.append(out['rank_score']);self.assertEqual(out['fact']['value'],length)
        self.assertEqual(len(set(scores)),1)

    def test_llm_cannot_swap_to_an_unselected_lower_rank_card(self):
        a=occurrence(2,40);b=copy.deepcopy(a);b['id']='lower'
        result=dict(mode='dota2',as_of={},context={},cards=[a,b],diagnostics={})
        def client(config,payload):
            self.assertEqual([c['id'] for c in payload['cards']],['a'])
            return {'cards':[dict(id='lower',text='Team本场再次出现这一表现，这是第3次。')]},{},'test'
        with patch.object(llm,'settings',return_value=dict(key='test',model='test',timeout=1)):
            out=llm.enhance(result,1,client=client)
        self.assertEqual(out['cards'][0]['id'],'a')
        self.assertEqual(out['diagnostics']['llm']['reason'],'unknown_or_duplicate_card')


class NarrativeStability(unittest.TestCase):
    def record(self):
        return dict(family='personal_record',subject=dict(name='Player'),fact=dict(value=7,phase=1,start=0,end=25),
            references=[dict(n=10,observations=[dict(value=5)])],text='Player前25分钟7次击杀，此前10次同阶段最高5次。')

    def test_internal_wording_is_rejected_without_changing_canonical(self):
        c=self.record();original=copy.deepcopy(c)
        with self.assertRaisesRegex(ValueError,'internal_wording'):guard('Player前25分钟7次击杀，本地此前最高5次。',c)
        self.assertEqual(c,original)

    def test_omitting_prior_denominator_is_allowed_but_not_prior_maximum(self):
        c=self.record();guard('Player前25分钟完成7次击杀，此前同阶段最多5次。',c)
        with self.assertRaisesRegex(ValueError,'missing_previous_record'):guard('Player前25分钟完成7次击杀，这次刷新纪录。',c)
        self.assertTrue(editorial_brief(c,'dota2')['optional_prior_denominator'])

    def test_subject_digits_do_not_satisfy_core_number_check(self):
        c=dict(family='burst',subject=dict(name='1w'),fact=dict(value=1),text='1w完成1次击杀。')
        with self.assertRaisesRegex(ValueError,'missing_core_value'):guard('1w在这个阶段完成了击杀，场上出现了变化。',c)

    def test_whole_match_goalless_cannot_replace_shot_sequence(self):
        c=dict(family='cross_match_events',subject=dict(name='Player'),fact=dict(value=17,metric='Shot'),
               text='Player跨6场连续17次射门未进球。')
        guard('Player最近连续17次射门未进球，这段序列跨6场。',c)
        with self.assertRaises(ValueError):guard('Player连续17次射门未进球，跨过的6场没有进球。',c)

    def test_precondition_cap_is_not_a_historical_record(self):
        c=dict(family='phase_change',subject=dict(name='Team'),fact=dict(value=6),
            text='Team前5至10分钟0次射门，10至30分钟6次；参考条件至多1次。')
        with self.assertRaisesRegex(ValueError,'unsupported_record_comparison'):
            guard('Team第5至10分钟没有射门，随后至30分钟射门6次。此前同阶段最多1次。',c)

    def test_pre_window_cannot_be_expanded_to_beginning_of_half(self):
        c=dict(family='phase_change',subject=dict(name='Team'),fact=dict(value=6,pre_start=5),
            text='Team在下半场5至10分钟没有射门，10至30分钟完成6次射门。')
        with self.assertRaisesRegex(ValueError,'missing_pre_window_start'):
            guard('Team在下半场第10分钟前没有射门，第10至30分钟完成6次射门。',c)

    def test_structured_zero_is_allowed_when_canonical_uses_chinese(self):
        c=dict(family='match_streak',subject=dict(name='Player'),
            fact=dict(value=6,metric='Death',rule=dict(maximum=0,end=10)),text='Player连续6次出场前10分钟零死亡。')
        guard('Player最近连续6次出场的前10分钟均为0死亡。',c)

    def test_team_run_does_not_imply_personal_no_death(self):
        c=dict(family='streak',subject=dict(name='Team'),fact=dict(value=13,metric='Kill'),
               text='Team在前30分钟曾有一段连续13次击杀。')
        with self.assertRaisesRegex(ValueError,'unsupported_death_claim'):
            guard('Team前30分钟曾有一段连续13次击杀，其间没有本人死亡。',c)

    def test_writer_does_not_receive_audit_prose_or_unrelated_reference_counts(self):
        card=dict(id='a',subject='Player',editorial_brief=dict(value=7,previous_max=5),derived_values={},
            verified_text='本地的534个观测',fact={'cap':3},references=[dict(n=534,tail_count=1)])
        wire=llm.wire_payload(dict(stage='write',mode='dota2',max_cards=1,cards=[card]))
        self.assertNotIn('verified_text',wire['cards'][0]);self.assertNotIn('references',wire['cards'][0])
        self.assertNotIn('534',wire['cards'][0]['allowed_numbers'])
        review=dict(stage='review',cards=[card]);self.assertIs(llm.wire_payload(review),review)


if __name__=='__main__':unittest.main()
