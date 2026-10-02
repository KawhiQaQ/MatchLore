import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from highlights import llm
from highlights.api import dispatch
from highlights.engine import Miner
from highlights.store import Store
from highlights.verify import verify_response


class DeepSeekTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.make_demo import make_demo
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        data=Path(cls.tmp.name)/'demo';make_demo(data)
        cls.store=Store(data);cls.miner=Miner(cls.store)
        cls.samples=[cls.miner.analyze('epl','epl-demo',30,max_cards=10),
                     cls.miner.analyze('dota2','dota-demo',30,max_cards=10)]

    def setUp(self):
        llm._CACHE.clear()
        self.settings=patch.object(llm,'settings',return_value=dict(key='test-only-secret',model='deepseek-flash',timeout=3))
        self.settings.start();self.addCleanup(self.settings.stop)

    def decision(self,payload):
        c=payload['cards'][-1]
        text=c['verified_text']
        for term in ('本地收录的','本地历史中','已收录','可比','同主体','球队观测','观测','样本','数据库','收录','本地'):text=text.replace(term,'')
        return {'cards':[{'id':c['id'],'text':text}]}

    def review(self,payload):
        return {'checks':[dict(id=c['id'],ok=True,reason='ok') for c in payload['cards']]}

    def fake(self,config,payload):return (self.review(payload) if payload.get('stage')=='review' else self.decision(payload)),{'total_tokens':100},config['model']

    def test_both_modes_use_preselected_subset_and_keep_all_evidence(self):
        for r in self.samples:
            original=copy.deepcopy(r);out=llm.enhance(r,3,client=self.fake)
            self.assertEqual(out['diagnostics']['llm']['status'],'partial' if len(r['cards'][:3])>1 else 'applied')
            self.assertFalse(out['diagnostics']['llm']['provider_request_attempted'])
            self.assertEqual([c['id'] for c in out['cards']],[c['id'] for c in r['cards'][:3]])
            self.assertEqual(out['cards'][-1]['broadcast_reference']['status'],'ready')
            if len(r['cards'][:3])>1:self.assertEqual(out['cards'][0]['broadcast_reference']['status'],'omitted')
            for key in ('fact','evidence','references','robust_tail'):
                self.assertEqual(out['cards'][-1][key],r['cards'][:3][-1][key])
            self.assertEqual(r,original);verify_response(self.store,out)

    def test_missing_key_explicit_fallback_no_request(self):
        llm.settings.return_value['key']=''
        with patch.object(llm,'call_deepseek') as transport:
            out=llm.enhance(self.samples[0],2);transport.assert_not_called()
        self.assertEqual(out['diagnostics']['llm']['reason'],'missing_api_key')
        self.assertEqual([c['text'] for c in out['cards']],[c['text'] for c in self.samples[0]['cards'][:2]])
        self.assertTrue(all(c['broadcast_reference']['text'] is None for c in out['cards']))

    def test_no_candidates_no_paid_call(self):
        r=copy.deepcopy(self.samples[0]);r['cards']=[]
        with patch.object(llm,'call_deepseek') as transport:
            out=llm.enhance(r,2);transport.assert_not_called()
        self.assertEqual(out['diagnostics']['llm']['status'],'skipped_empty')

    def test_injected_or_hallucinated_fields_rejected(self):
        payload=llm.compact(self.samples[0],3);good=self.decision(payload)
        variants=[]
        d=copy.deepcopy(good);d['cards'][0]['id']='invented';variants.append(d)
        d=copy.deepcopy(good);d['cards'][0]['extra']='生涯首次100次击杀';variants.append(d)
        d=copy.deepcopy(good);d['cards'][0]['headline']='世界纪录';variants.append(d)
        d=copy.deepcopy(good);d['cards']*=2;variants.append(d)
        d=copy.deepcopy(good);d['cards'][0]['id']=[];variants.append(d)
        for d in variants:
            out=llm.enhance(self.samples[0],3,client=lambda c,p:(d,{},c['model']))
            self.assertEqual(out['diagnostics']['llm']['status'],'fallback')
            self.assertEqual([c['text'] for c in out['cards']],[c['text'] for c in self.samples[0]['cards'][:3]])
            self.assertTrue(all(c['broadcast_reference']['text'] is None for c in out['cards']))

    def test_network_failure_keeps_original_results(self):
        def fail(*_):raise llm.LLMFailure('network_or_timeout')
        out=llm.enhance(self.samples[0],3,client=fail)
        self.assertEqual(out['diagnostics']['llm']['reason'],'network_or_timeout')
        self.assertNotIn('test-only-secret',json.dumps(out))

    def test_cache_avoids_second_call_and_does_not_double_count_tokens(self):
        with patch.object(llm,'call_deepseek',side_effect=self.fake) as transport:
            first=llm.enhance(self.samples[0],3);second=llm.enhance(self.samples[0],3)
            self.assertEqual(transport.call_count,2)
        self.assertTrue(second['diagnostics']['llm']['cache_hit'])
        self.assertEqual(second['diagnostics']['llm']['usage'],{})
        self.assertEqual(first['cards'],second['cards'])

    def test_compact_payload_excludes_raw_logs_accounts_and_paths(self):
        payload=llm.compact(self.samples[0],3)
        for c in payload['cards']:
            self.assertNotIn('evidence',c)
            self.assertTrue(all('observations' not in r for r in c['references']))
        serialized=json.dumps(payload)
        self.assertNotIn('local_raw_path',serialized)
        self.assertNotIn('raw_sha256',serialized)

    def test_http_wire_format_json_and_non_thinking(self):
        payload=llm.compact(self.samples[0],3)
        reply=dict(model='deepseek-flash',choices=[dict(finish_reason='stop',message={'content':json.dumps(self.decision(payload))})],usage={'total_tokens':100})
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(reply).encode()
        opener=MagicMock();opener.open.return_value=response
        with patch.object(llm,'build_opener',return_value=opener):llm.call_deepseek(llm.settings(),payload)
        request=opener.open.call_args.args[0];sent=json.loads(request.data)
        self.assertEqual(sent['thinking'],{'type':'disabled'})
        self.assertEqual(sent['response_format'],{'type':'json_object'})
        self.assertLessEqual(sent['max_tokens'],4000)
        self.assertEqual(request.full_url,'https://api.deepseek.com/chat/completions')

    def test_provider_truncation_and_empty_content_rejected(self):
        for finish,content in [('length','{}'),('stop','')]:
            response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(dict(choices=[dict(finish_reason=finish,message={'content':content})])).encode()
            opener=MagicMock();opener.open.return_value=response
            with patch.object(llm,'build_opener',return_value=opener):
                with self.assertRaises(llm.LLMFailure):llm.call_deepseek(llm.settings(),{})

    def test_dispatch_opt_in_and_input_limit(self):
        with patch.object(llm,'call_deepseek',side_effect=self.fake) as transport:
            result=dispatch(self.miner,dict(mode='epl',match_id='epl-demo',as_of_minute=30,llm='deepseek',max_cards=2))
            self.assertEqual(transport.call_count,2)
        self.assertEqual(result['diagnostics']['llm']['status'],'partial')
        with self.assertRaises(ValueError):dispatch(self.miner,dict(mode='epl',match_id='epl-demo',llm='deepseek',max_cards=100))

    def test_status_never_exposes_key(self):
        self.assertTrue(llm.status()['key_configured'])
        self.assertNotIn('test-only-secret',json.dumps(llm.status()))

    def test_empty_writer_selection_never_deletes_originals(self):
        out=llm.enhance(self.samples[0],3,client=lambda c,p:({'cards':[]},{},c['model']))
        self.assertEqual(len(out['cards']),min(3,len(self.samples[0]['cards'])));self.assertEqual(out['status'],'ok')
        self.assertTrue(all(c['broadcast_reference']['status']=='omitted' for c in out['cards']))


if __name__=='__main__':unittest.main()
