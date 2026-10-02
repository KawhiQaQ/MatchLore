"""Machine-readable v1 HTTP contract, served by the API and CLI."""
from . import __version__

def openapi():
    ref=lambda name:{'$ref':'#/components/schemas/'+name}
    mode=dict(type='string',enum=['epl','dota2'])
    source=dict(type='object',additionalProperties=False,properties=dict(mode=mode,match_id=dict(type='string'),raw={},metadata=dict(type='object')),
                required=['mode'])
    source['oneOf']=[{'required':['match_id'],'not':{'required':['raw']}},{'required':['raw'],'not':{'required':['match_id']}}]
    analyze={**source,'properties':{**source['properties'],
        'phase':dict(type='integer',enum=[1,2],default=1),'as_of_minute':dict(type='integer',minimum=5,maximum=60,default=30),
        'max_cards':dict(type='integer',minimum=1,maximum=10,default=5),'llm':dict(type='string',enum=['off','deepseek'],default='off'),
        'include_history':dict(type='boolean',default=True)}}
    schemas=dict(Source=source,AnalyzeRequest=analyze,
        ReplayRequest=dict(type='object',additionalProperties=False,required=['mode','match_id'],properties=dict(mode=mode,match_id=dict(type='string'),step=dict(type='integer',minimum=5,maximum=30,default=5),max_cards=dict(type='integer',minimum=1,maximum=10,default=3),llm=dict(type='string',enum=['off','deepseek'],default='off'))),
        ImportRequest=dict(type='object',additionalProperties=False,required=['matches'],properties=dict(matches=dict(type='array',minItems=1,maxItems=1000,items=ref('Source')))),
        Error=dict(type='object',required=['error','request_id'],properties=dict(error=dict(type='object',required=['code','message'],properties=dict(code=dict(type='string'),message=dict(type='string'))),request_id=dict(type='string'))),
        BroadcastReference=dict(type='object',required=['text','status','review_required'],properties=dict(text=dict(type=['string','null']),status=dict(type='string',enum=['ready','disabled','omitted','rejected','unavailable']),reason=dict(type=['string','null']),provider=dict(type='string'),review_required=dict(type='boolean',const=True))),
        Card=dict(type='object',required=['id','family','original_text','text','broadcast_reference','fact','evidence','references'],properties=dict(id=dict(type='string'),family=dict(type='string'),original_text=dict(type='string'),text=dict(type='string',description='原始统计，永不由LLM覆盖'),summary=dict(type='string'),broadcast_reference=ref('BroadcastReference'),fact=dict(type='object'),evidence=dict(type='array',items=dict(type='object')),references=dict(type='array',items=dict(type='object')))),
        AnalyzeResponse=dict(type='object',required=['schema_version','mode','match_id','status','cards','context','diagnostics','provenance'],properties=dict(schema_version=dict(type='integer',const=1),mode=mode,match_id=dict(type='string'),status=dict(type='string',enum=['ok','no_highlights']),cards=dict(type='array',items=ref('Card')),context=dict(type='object'),diagnostics=dict(type='object'),provenance=dict(type='object'))))
    check_request=dict(type='object',additionalProperties=False,required=['mode','raw'],
        properties=dict(mode=mode,raw={},metadata=dict(type='object')))
    status_request=dict(type='object',additionalProperties=False,required=['mode','match_id'],
        properties=dict(mode=mode,match_id=dict(type='string',minLength=1)))
    change_properties={**status_request['properties'],'expected_revision':dict(type='integer',minimum=0),
                       'reason':dict(type='string',minLength=1,maxLength=500)}
    schemas.update(
        CheckRequest=check_request,
        CheckBatchRequest=dict(type='object',additionalProperties=False,required=['matches'],properties=dict(matches=dict(type='array',minItems=1,maxItems=1000,items=ref('CheckRequest')))),
        CheckResponse=dict(type='object',required=['valid','can_ingest'],properties=dict(valid=dict(type='boolean'),can_ingest=dict(type='boolean'),errors=dict(type='array',items=dict(type='object')),warnings=dict(type='array',items=dict(type='object')),coverage=dict(type='object'),results=dict(type='array',items=dict(type='object')))),
        HistoryStatusRequest=status_request,
        WithdrawRequest=dict(type='object',additionalProperties=False,required=list(change_properties),properties=change_properties),
        ReplaceRequest=dict(type='object',additionalProperties=False,required=[*change_properties,'raw'],properties={**change_properties,'raw':{},'metadata':dict(type='object')}),
        HistoryStatusResponse=dict(type='object',required=['mode','match_id','status','revision','changes'],properties=dict(mode=mode,match_id=dict(type='string'),status=dict(type='string',enum=['active','withdrawn']),revision=dict(type='integer',minimum=0),fingerprint=dict(type=['string','null']),changes=dict(type='array',items=dict(type='object')))),
        ChangeResponse=dict(type='object',required=['status','mode','match_id','revision','affected_matches'],properties=dict(status=dict(type='string',enum=['replaced','restored','withdrawn','unchanged','already_withdrawn']),mode=mode,match_id=dict(type='string'),revision=dict(type='integer',minimum=0),affected_matches=dict(type='array',items=dict(type='string')),reanalysis_required=dict(type='boolean'))))
    schemas['ReanalyzeRequest']=dict(type='object',additionalProperties=False,required=['mode','revision'],properties=dict(mode=mode,revision=dict(type='integer',minimum=1),phase=dict(type='integer',enum=[1,2],default=1),as_of_minute=dict(type='integer',minimum=5,maximum=60,default=30),max_cards=dict(type='integer',minimum=1,maximum=10,default=5)))
    schemas['ReanalyzeResponse']=dict(type='object',required=['mode','revision','results','skipped','status','llm'],properties=dict(mode=mode,revision=dict(type='integer'),results=dict(type='array',items=ref('AnalyzeResponse')),skipped=dict(type='array',items=dict(type='object')),status=dict(type='string',const='completed'),llm=dict(type='string',const='off')))
    errors={str(n):dict(description=label,content={'application/json':dict(schema=ref('Error'))}) for n,label in [(400,'Invalid request'),(401,'Unauthorized'),(404,'Not found'),(405,'Method not allowed'),(409,'Conflict'),(413,'Request too large'),(415,'JSON required'),(500,'Internal error'),(503,'Storage unavailable')]}
    def operation(description,request=None,response=None):
        out=dict(description=description,responses={'200':dict(description='Success',content={'application/json':dict(schema=ref(response) if response else dict(type='object'))}),**errors})
        if request:out['requestBody']=dict(required=True,content={'application/json':dict(schema=ref(request))})
        return out
    paths={'/health':{'get':operation('Service version and liveness')},'/v1/modes':{'get':operation('Available adapters')},
           '/v1/llm':{'get':operation('Provider configuration status without secrets')},
           '/v1/matches':{'get':operation('List matches by mode and role')},
           '/v1/analyze':{'post':operation('Read-only analysis; EPL minute is half-local; no automatic ingestion','AnalyzeRequest','AnalyzeResponse')},
           '/v1/check':{'post':operation('Read-only diagnostics; valid=false or can_ingest=false is reported with HTTP 200','CheckRequest','CheckResponse')},
           '/v1/history/check':{'post':operation('Read-only batch diagnostics including duplicate conflicts','CheckBatchRequest','CheckResponse')},
           '/v1/history/reanalyze':{'post':operation('Recompute stored affected matches at one phase/minute using a current corpus snapshot; no model calls','ReanalyzeRequest','ReanalyzeResponse')},
           '/v1/history/status':{'post':operation('Read current revision and change log, including withdrawn matches','HistoryStatusRequest','HistoryStatusResponse')},
           '/v1/history/replace':{'post':operation('Replace or restore the same match ID using its current revision; returns potential reanalysis targets','ReplaceRequest','ChangeResponse')},
           '/v1/history/withdraw':{'post':operation('Withdraw a match using its current revision; retain audit snapshots','WithdrawRequest','ChangeResponse')},
           '/v1/ingest':{'post':operation('Commit one completed match idempotently','Source')},
           '/v1/history/import':{'post':operation('Atomic batch import; every source must be a completed match','ImportRequest')},
           '/v1/replay':{'post':operation('Historical playback; no automatic ingestion','ReplayRequest')},
           '/openapi.json':{'get':operation('This OpenAPI document')}}
    paths['/v1/matches']['get']['parameters']=[{'name':'mode','in':'query','required':True,'schema':mode},{'name':'role','in':'query','schema':dict(type='string',enum=['development','historical','committed','all'],default='development')}]
    return dict(openapi='3.1.0',info=dict(title='MatchLore',version=__version__),paths=paths,
                security=[{},dict(BearerAuth=[])],components=dict(schemas=schemas,securitySchemes=dict(BearerAuth=dict(type='http',scheme='bearer'))))
