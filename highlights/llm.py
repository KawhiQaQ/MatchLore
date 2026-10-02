"""Optional DeepSeek selection + natural wording, with gated semantic review."""
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .config import env_file
ENV_FILE=env_file()
ENDPOINT='https://api.deepseek.com/chat/completions'
PROMPT_VERSION='semantic_contract_v10'
VALIDATION_VERSION='typed_roles_v2'
_CACHE={}
_LOCK=threading.Lock()
from .narrative import WRITER_SYSTEM, REVIEW_SYSTEM, guard, validate_review, allowed_numbers, derived_values, editorial_brief, numbers, semantic_contract
SYSTEM=WRITER_SYSTEM


class LLMFailure(Exception):
    pass


def settings():
    allowed={'DEEPSEEK_API_KEY','DEEPSEEK_MODEL','DEEPSEEK_TIMEOUT'}
    values={}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):continue
            key,sep,value=line.partition('=')
            if sep and key.strip() in allowed:values[key.strip()]=value.strip().strip('\"\'')
    for key in allowed:
        if key in os.environ:values[key]=os.environ[key]
    timeout=float(values.get('DEEPSEEK_TIMEOUT','20'))
    if not 1<=timeout<=60:raise ValueError('DEEPSEEK_TIMEOUT must be in 1..60 seconds')
    return dict(key=values.get('DEEPSEEK_API_KEY','').strip(),model=values.get('DEEPSEEK_MODEL','deepseek-flash'),timeout=timeout)


def status():
    config=settings()
    return dict(provider='deepseek',model=config['model'],key_configured=bool(config['key']),
                default='off',thinking='disabled',output='原始统计与DeepSeek转播参考稿双输出；原文始终保留，参考稿需编辑确认')


def headline_options(card):
    choices={
        'metric_burst':{'concentration':'短时事件集中出现','reference':'与同阶段历史对照'},
        'metric_record':{'record':'超过已收录同阶段最高值','history':'同主体历史对照'},
        'phase_change':{'contrast':'前后阶段反差','acceleration':'后段明显提速'},
        'burst':{'burst':'短时间集中爆发','rare_window':'少见的短时表现'},
        'streak':{'streak':'连续表现值得关注','one_sided':'一段单方连续事件'},
        'personal_record':{'record':'刷新本地同阶段纪录','personal_best':'超过已收录的个人同阶段最高值'},
        'match_streak':{'streak':'连续多场做到','consistency':'多场稳定表现'},
        'historical_occurrence':{'again':'同类表现再次出现','history':'回看同一主体的历史'},
        'cross_match_events':{'cross_match':'纪录延续到本场','sequence':'跨场连续事件'},
    }
    return choices[card['family']]


def compact(result,max_cards):
    return dict(mode=result['mode'],as_of=result['as_of'],max_cards=max_cards,
                reference_scope=result['context'],cards=[dict(
                    id=c['id'],family=c['family'],subject=c['subject']['name'],
                    fact={k:v for k,v in c['fact'].items() if k not in ('player_identity','entity_identity')},verified_text=c['text'],
                    editorial_brief=editorial_brief(c,result['mode']),
                    semantic_contract=semantic_contract(c),
                    history_scope=c.get('history',{}).get('coverage'),
                    references=[{k:r[k] for k in ('name','n','tail_count','unit')} for r in c['references']],
                    allowed_numbers=sorted(allowed_numbers(c)),derived_values=derived_values(c),presentation_priority=c.get('presentation_priority','primary')) for c in result['cards']])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def call_deepseek(config,payload):
    stage=payload.get('stage','write')
    system=REVIEW_SYSTEM if stage=='review' else SYSTEM
    wire=wire_payload(payload)
    budget=min(4000,400+400*payload.get('max_cards',5)) if stage=='write' else 1000
    body=dict(model=config['model'],messages=[dict(role='system',content=system),
              dict(role='user',content=json.dumps(wire,ensure_ascii=False))],
              thinking={'type':'disabled'},temperature=.2 if stage=='write' else 0,max_tokens=budget,
              response_format={'type':'json_object'},stream=False)
    request=Request(ENDPOINT,json.dumps(body,ensure_ascii=False).encode(),
                    {'Authorization':'Bearer '+config['key'],'Content-Type':'application/json'})
    try:
        # No redirects, no automatic retries or paid self-repair loops.
        with build_opener(NoRedirect).open(request,timeout=config['timeout']) as response:
            raw=response.read(256*1024+1)
        if len(raw)>256*1024:raise LLMFailure('response_too_large')
        result=json.loads(raw)
        choice=result['choices'][0]
        if choice.get('finish_reason')!='stop':raise LLMFailure('incomplete_response')
        decision=json.loads(choice['message']['content'])
        usage={k:v for k,v in result.get('usage',{}).items()
               if k in ('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens') and type(v) is int and v>=0}
        return decision,usage,result.get('model',config['model'])
    except HTTPError as error:
        # Do not surface provider response bodies: they may echo request data.
        raise LLMFailure(f'http_{error.code}') from None
    except (URLError,TimeoutError,OSError):raise LLMFailure('network_or_timeout') from None
    except (ValueError,KeyError,IndexError,TypeError,AttributeError):raise LLMFailure('invalid_response') from None


def wire_payload(payload):
    """Give the writer only typed facts, not audit prose or irrelevant tail counts.

    Reviewer retains the original evidence summary and the same typed contract.
    This changes model input, not any stored fact, evidence or fallback text.
    """
    if payload.get('stage','write')=='review':return payload
    cards=[]
    for c in payload.get('cards',[]):
        brief={k:c[k] for k in ('subject','editorial_brief','derived_values')}
        if 'semantic_contract' in c:
            brief['semantic_contract']=c['semantic_contract']
            brief['editorial_brief']=dict(c['editorial_brief'])
            # Auxiliary audit denominators/deltas invite redundant prose; the
            # review stage still receives them. Required comparisons remain.
            brief['editorial_brief'].pop('prior_appearances',None)
            brief['derived_values']={k:v for k,v in c['derived_values'].items() if k!='record_improvement'}
        cards.append(dict(id=c['id'],**brief,allowed_numbers=sorted(numbers(json.dumps(brief,ensure_ascii=False)))))
    return dict(stage='write',mode=payload.get('mode'),max_cards=payload.get('max_cards',5),cards=cards)


def validate_decision(decision,cards,limit):
    if not isinstance(decision,dict) or set(decision)!={'cards'} or not isinstance(decision['cards'],list):
        raise LLMFailure('invalid_selection')
    if len(decision['cards'])>limit:raise LLMFailure('too_many_cards')
    ids={c['id'] for c in cards};seen=set()
    for item in decision['cards']:
        if not isinstance(item,dict) or set(item)!={'id','text'}:raise LLMFailure('invalid_selection')
        if not all(isinstance(v,str) for v in item.values()):raise LLMFailure('invalid_selection')
        if item['id'] not in ids or item['id'] in seen:raise LLMFailure('unknown_or_duplicate_card')
        seen.add(item['id'])


def _rewrite(result,limit,client=None):
    """At most two requests per cache miss: drafting then separate semantic review."""
    output=copy.deepcopy(result);started=time.perf_counter();source=result['cards'][:limit]
    output['cards']=output['cards'][:limit]
    info=dict(provider='deepseek',prompt_version=PROMPT_VERSION,validation_version=VALIDATION_VERSION,status='fallback',cache_hit=False,
              output_contract='natural_text_with_canonical_evidence',provider_request_attempted=False,
              request_count=0,stages=[],usage={},semantic_review='not_run',selection_policy='deterministic_editorial_v2')
    def request(config,payload):
        info['provider_request_attempted']=client is None
        info['request_count']+=int(client is None)
        value,usage,model=(client or call_deepseek)(config,payload)
        info['stages'].append(dict(stage=payload.get('stage','write'),usage=usage,model=model))
        for k,v in usage.items():info['usage'][k]=info['usage'].get(k,0)+v
        return value,model
    try:
        config=settings();info['requested_model']=config['model']
        if not source:
            info.update(status='skipped_empty',reason='no_candidate_cards');return output
        if not config['key']:
            info.update(reason='missing_api_key');return output
        payload=compact(dict(result,cards=source),limit);payload['stage']='write'
        key=hashlib.sha256(json.dumps([PROMPT_VERSION,VALIDATION_VERSION,config['model'],SYSTEM,REVIEW_SYSTEM,payload],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        cached=None
        if client is None:
            with _LOCK:cached=_CACHE.get(key)
        if cached is not None:
            presentations,models=copy.deepcopy(cached);selected=[]
            current={c['id']:c for c in source}
            for item in presentations:
                c=copy.deepcopy(current[item['id']]);c['canonical_text']=c['text'];c['canonical_summary']=c.get('summary',c['text'])
                c.update(text=item['text'],summary=item['text'],editorial=item['editorial']);selected.append(c)
            info.update(cache_hit=True,semantic_review='cached_model_review',actual_models=models)
        else:
            decision,writer_model=request(config,payload)
            if isinstance(decision,dict) and isinstance(decision.get('cards'),list) and len(decision['cards'])>limit:
                info['over_limit_discarded']=len(decision['cards'])-limit
                decision=copy.deepcopy(decision);decision['cards']=decision['cards'][:limit]
            validate_decision(decision,source,limit)
            order={c['id']:i for i,c in enumerate(source)}
            decision['cards'].sort(key=lambda x:order[x['id']])
            by_id={c['id']:c for c in source};compact_ids={c['id']:c for c in payload['cards']}
            ready=[];failures={}
            for item in decision['cards']:
                try:guard(item['text'],by_id[item['id']]);ready.append(item)
                except ValueError as e:failures[item['id']]='guard:'+str(e)
            reviews={};models=[writer_model]
            if ready:
                review_payload=dict(stage='review',mode=result['mode'],as_of=result['as_of'],cards=[dict(
                    compact_ids[x['id']],draft_text=x['text']) for x in ready])
                try:
                    review,model=request(config,review_payload);models.append(model)
                    reviews=validate_review(review,ready);info['semantic_review']='model_reviewed'
                except (LLMFailure,ValueError):
                    failures.update({x['id']:'review_unavailable' for x in ready});info['semantic_review']='failed'
            selected=[]
            for item in decision['cards']:
                c=copy.deepcopy(by_id[item['id']]);c['canonical_text']=c['text'];c['canonical_summary']=c.get('summary',c['text'])
                checked=reviews.get(item['id']);reason=failures.get(item['id'])
                if checked and not checked['ok']:reason='review:'+checked['reason']
                if not reason and checked and checked['ok']:
                    c['text']=item['text'];c['summary']=item['text']
                    c['editorial']=dict(provider='deepseek',status='rewritten',validation='numeric_scope_guard_and_model_review',
                                        semantic_guarantee='model_review_not_formal_proof')
                else:c['editorial']=dict(provider='deepseek',status='fallback',reason=reason or 'review_missing')
                selected.append(c)
            info['actual_models']=models
            # Only cache accepted drafts, never transient failures or refusals.
            if client is None and all(c['editorial']['status']=='rewritten' for c in selected):
                with _LOCK:
                    if len(_CACHE)>=128:_CACHE.pop(next(iter(_CACHE)))
                    _CACHE[key]=([{k:copy.deepcopy(c[k]) for k in ('id','text','editorial')} for c in selected],models)
        output['cards']=selected;output['status']='ok' if selected else 'no_highlights'
        rewritten=sum(c['editorial']['status']=='rewritten' for c in selected)
        info.update(status='applied' if rewritten==len(selected) else 'partial' if rewritten else 'fallback',
                    selected_count=len(selected),candidate_count=len(source),rewritten_count=rewritten)
        if info['status']=='fallback':info['reason']='all_drafts_rejected'
        output['diagnostics']['renderer']='deepseek_natural_zh_v1' if rewritten else 'deterministic_zh_v1'
        if not selected:output['diagnostics']['empty_reason']='LLM未选择可播报亮点'
    except LLMFailure as error:info['reason']=str(error)
    except (ValueError,OSError):info['reason']='invalid_local_configuration'
    finally:
        info['elapsed_seconds']=time.perf_counter()-started;output['diagnostics']['llm']=info
    return output


def original_output(card):
    """Stable public contract, including when the optional writer is disabled."""
    card['original_text']=card['text']
    card['broadcast_reference']=dict(text=None,status='disabled',provider='deepseek',
                                     reason='llm_off',review_required=True)
    return card


def enhance(result,limit,client=None):
    """Keep every selected original; expose accepted drafts only as references.

    Writer omission, network failure and rejected drafts cannot remove facts or
    masquerade as a successful second output. Legacy text/summary fields always
    retain their deterministic meaning, with or without the LLM.
    """
    clean=copy.deepcopy(result)
    for c in clean['cards']:
        c.pop('editorial',None)
        original_output(c)
    rewritten=_rewrite(clean,limit,client=client)
    info=rewritten['diagnostics']['llm']
    drafts={c['id']:c for c in rewritten['cards']}
    cards=[]
    for source in result['cards'][:limit]:
        c=original_output(copy.deepcopy(source));draft=drafts.get(c['id'])
        editorial=draft.get('editorial',{}) if draft else {}
        accepted=editorial.get('status')=='rewritten'
        if accepted:
            reference=dict(text=draft['text'],status='ready',provider='deepseek',reason=None,
                           review_required=True,validation=editorial['validation'])
        else:
            reason=editorial.get('reason') or info.get('reason') or 'writer_omitted'
            state='rejected' if editorial.get('reason') else 'omitted' if not draft else 'unavailable'
            reference=dict(text=None,status=state,provider='deepseek',reason=reason,review_required=True)
        c['broadcast_reference']=reference
        c['canonical_text']=c['text'];c['canonical_summary']=c.get('summary',c['text'])
        c['editorial']=editorial or dict(provider='deepseek',status='fallback',reason=reference['reason'])
        cards.append(c)
    rewritten['cards']=cards;rewritten['status']='ok' if cards else 'no_highlights'
    ready=sum(c['broadcast_reference']['status']=='ready' for c in cards)
    info.update(output_contract='original_and_broadcast_reference_v1',selected_count=len(cards),
                candidate_count=min(limit,len(result['cards'])),rewritten_count=ready)
    if cards:
        info['status']='applied' if ready==len(cards) else 'partial' if ready else 'fallback'
        if ready and info.get('reason')=='all_drafts_rejected':info.pop('reason')
        rewritten['diagnostics']['empty_reason']=None
    rewritten['diagnostics']['renderer']='dual_output_zh_v1'
    return rewritten
