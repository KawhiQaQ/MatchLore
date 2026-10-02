import copy
import unittest
from highlights.narrative import semantic_contract,guard,editorial_brief
from highlights.llm import wire_payload


class SemanticContract(unittest.TestCase):
    def sequence(self,n,span,active,metric='Shot'):
        label='射门未进球' if metric=='Shot' else '击杀，其间未记录本人死亡'
        return dict(subject=dict(name='TestEntity'),family='cross_match_events',
                    fact=dict(value=n,metric=metric),history=dict(span_match_ids=list(range(span)),event_match_ids=list(range(active))),
                    text=f'TestEntity跨{span}场最近连续{n}次{label}。')

    def test_distinct_roles_across_counts_entities_and_event_types(self):
        for n,span,active in [(11,7,3),(4,12,2),(41,5,5),(9,9,1)]:
            for metric in ('Shot','Kill'):
                c=self.sequence(n,span,active,metric);before=copy.deepcopy(c)
                m=semantic_contract(c)['measurements']
                self.assertEqual([x['value'] for x in m],[n,span,active])
                self.assertEqual([x['required'] for x in m],[True,True,False])
                label='射门未进球' if metric=='Shot' else '击杀，其间未记录本人死亡'
                guard(f'TestEntity连续{n}次{label}，这段连续序列跨{span}场。',c)
                if span!=active:
                    with self.assertRaisesRegex(ValueError,'event_count_scope_mismatch'):
                        guard(f'TestEntity连续{n}次{label}，序列跨{span}场，事件分布在{span}场。',c)
                guard(f'TestEntity连续{n}次{label}，序列跨{span}场，事件分布在{active}场。',c)
                self.assertEqual(c,before)

    def test_number_role_swap_is_rejected_even_when_both_values_are_allowed(self):
        c=self.sequence(11,7,3)
        with self.assertRaisesRegex(ValueError,'missing_sequence_span'):
            guard('TestEntity连续11次射门未进球，连续序列跨3场，射门分布在7场。',c)

    def test_ambiguous_pronoun_is_distinct_from_explicit_sequence_span(self):
        c=self.sequence(11,7,3)
        with self.assertRaisesRegex(ValueError,'ambiguous_event_span'):
            guard('TestEntity连续11次射门未进球，这些射门跨7场。',c)
        guard('TestEntity连续11次射门未进球，这段连续序列跨7场。',c)

    def test_same_numeric_value_does_not_merge_distinct_roles(self):
        c=self.sequence(9,9,1);m=semantic_contract(c)['measurements']
        self.assertEqual(m[0]['value'],m[1]['value']);self.assertNotEqual(m[0]['role'],m[1]['role'])

    def test_optional_audit_counts_are_not_writer_prose(self):
        c=dict(family='personal_record',subject=dict(name='P'),fact=dict(value=8,phase=1,end=25),
               references=[dict(n=17,observations=[dict(value=6)])],text='P前25分钟8次。')
        compact=dict(id='x',subject='P',editorial_brief=editorial_brief(c,'dota2'),
                     semantic_contract=semantic_contract(c),derived_values=dict(record_improvement=2),references=[dict(n=17)])
        wire=wire_payload(dict(cards=[compact]))['cards'][0]
        self.assertNotIn('prior_appearances',wire['editorial_brief'])
        self.assertNotIn('record_improvement',wire['derived_values'])
        self.assertEqual(wire['semantic_contract']['measurements'][1]['value'],6)
        review=dict(stage='review',cards=[compact]);self.assertIs(wire_payload(review),review)

    def test_predicate_operators_keep_equality_upper_and_lower_bounds_distinct(self):
        for rule,expected in [({'maximum':0},[('=',0)]),({'minimum':3},[('>=',3)]),
                              ({'boundary':10,'cap':2,'minimum':5},[('<=',2),('>=',5)])]:
            c=dict(family='match_streak',fact=dict(value=4,metric='Death',rule=dict(rule,end=20)))
            plan=semantic_contract(c)
            self.assertEqual([(x['operator'],x['value']) for x in plan['conditions']],expected)
            self.assertEqual(plan['measurements'][0]['unit'],'次条件成立')

    def test_independent_window_counts_do_not_become_running_totals(self):
        for metric,label in [('Shot','射门'),('Kill','击杀')]:
            c=dict(family='phase_change',subject=dict(name='Team'),fact=dict(value=8,metric=metric,pre_start=5),
                   text=f'Team第5—10分钟1次{label}，第10—30分钟8次{label}。')
            guard(f'Team第5—10分钟取得1次{label}，第10—30分钟取得8次{label}。',c)
            for verb in ('增至','升至','增加到','提升到','累计'):
                with self.assertRaisesRegex(ValueError,'independent_windows_as_accumulation'):
                    guard(f'Team第5—10分钟1次{label}，第10—30分钟{label}{verb}8次。',c)


if __name__=='__main__':unittest.main()
