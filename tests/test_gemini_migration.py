"""Fixed-model migration contracts using native SDK raw JSON/SSE only."""
import json
from types import SimpleNamespace
import uuid

import httpx2
import pytest

from generation.providers.base import CompletionOptions, ProviderError, UsageInfo
from generation.providers.gemini import GeminiProvider
from tests.test_gemini_sdk_bridge import native_client
from users.models import UserProviderKey
from tests.factories import BaseCardFactory
from examples.models import Fragment
from logs.models import LlmCallLog
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from tests.test_safe_model_diagnostics import capture_logger


MODEL = 'gemini-3.5-flash-lite'
VERSION = MODEL+'-001'
PRIVATE = 'synthetic-private-model-or-key-marker'
OPTIONS = CompletionOptions(response_format='json',max_tokens=1000,reasoning_effort='low')


def response_payload(text='result', *, version=VERSION, finish='STOP', usage=None):
    result={'candidates':[{'content':{'parts':[] if text is None else [{'text':text}]},'finishReason':finish}]}
    if version is not None:result['modelVersion']=version
    if usage is not None:result['usageMetadata']=usage
    return result


def sse(request, chunks):
    raw=''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in chunks)
    return httpx2.Response(200,request=request,headers={'content-type':'text/event-stream'},content=raw.encode())


def test_fixed_model_and_low_thinking_wire_policy(monkeypatch):
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload('{}')))
    result=GeminiProvider('dummy-key').complete('system','user',options=OPTIONS)
    request=state.requests[0];config=json.loads(request.content)['generationConfig']
    assert request.url.path.endswith('/'+MODEL+':generateContent')
    thinking=config.get('thinkingConfig',config.get('thinking_config',{}))
    assert thinking.get('thinkingLevel',thinking.get('thinking_level'))=='MINIMAL'
    assert config['maxOutputTokens']==1000 and config['responseMimeType']=='application/json'
    assert all(name not in config for name in ['temperature','topP','topK','candidateCount'])
    assert 'thinkingBudget' not in thinking and 'thinking_budget' not in thinking
    assert result.model==MODEL
    assert len(state.requests)==1 and state.closes==state.clients


def test_native_reported_usage_keeps_missing_and_zero_distinct(monkeypatch):
    raw=response_payload('{}',usage={'candidatesTokenCount':0,'thoughtsTokenCount':7,'totalTokenCount':8})
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=raw))
    result=GeminiProvider('dummy-key').complete('system','user',options=OPTIONS)
    metadata=getattr(result,'metadata',None)
    assert metadata is not None
    assert metadata.model_version==VERSION
    assert metadata.prompt_tokens is None and metadata.candidate_tokens==0
    assert metadata.thoughts_tokens==7 and metadata.total_tokens==8
    assert result.model==MODEL and (result.prompt_tokens,result.completion_tokens)==(0,0)
    assert state.closes==state.clients


def test_reported_different_model_is_safe_failure(monkeypatch):
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload(version='gemini-2.5-flash')))
    with pytest.raises(ProviderError) as caught:
        GeminiProvider('dummy-key').complete('system','user',options=OPTIONS)
    assert caught.value.code=='provider_model_unavailable'
    assert len(state.requests)==1 and state.closes==state.clients


@pytest.mark.parametrize('finish',['MAX_TOKENS','SAFETY',PRIVATE])
def test_stream_non_success_finish_never_completes_or_replays(monkeypatch,finish):
    chunks=[response_payload('first',finish=None),response_payload('last',finish=finish)]
    state=native_client(monkeypatch,httpx2,lambda request:sse(request,chunks))
    iterator=GeminiProvider('dummy-key').stream('system','user')
    assert next(iterator)=='first'
    with pytest.raises(ProviderError) as caught:list(iterator)
    assert caught.value.code=='generation_failed' and PRIVATE not in str(caught.value)
    assert len(state.requests)==1 and state.closes==state.clients
    assert all(item.is_closed for item in state.responses)


@pytest.mark.django_db
def test_native_key_verification_records_reported_version(api_client,make_user_with_key,monkeypatch):
    user=make_user_with_key();api_client.force_authenticate(user=user)
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload('OK')))
    response=api_client.post('/api/auth/llm-config/verify/',{'provider':'gemini'},format='json')
    assert response.status_code==200
    key=UserProviderKey.objects.get(user=user,provider='gemini')
    assert key.verification_model==MODEL and key.verification_resolved_model==VERSION
    assert len(state.requests)==1 and state.closes==state.clients


@pytest.mark.parametrize('hint,level',[(None,'MINIMAL'),('low','MINIMAL'),('medium','MEDIUM'),('high','HIGH')])
@pytest.mark.parametrize('format',['json','text'])
def test_all_declared_hints_keep_explicit_native_configuration(monkeypatch,hint,level,format):
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload('{}')))
    GeminiProvider('dummy-key').complete('system','user',options=CompletionOptions(response_format=format,max_tokens=64,reasoning_effort=hint))
    config=json.loads(state.requests[0].content)['generationConfig']
    thinking=config.get('thinkingConfig',config.get('thinking_config'))
    assert thinking.get('thinkingLevel',thinking.get('thinking_level'))==level
    assert thinking.get('includeThoughts',thinking.get('include_thoughts')) is False
    assert config['maxOutputTokens']==64
    assert config.get('responseMimeType')==('application/json' if format=='json' else None)
    assert len(state.requests)==1 and state.closes==state.clients


@pytest.mark.parametrize('version',[None,'',MODEL,VERSION,MODEL+'-2026-07'])
def test_expected_or_unknown_model_versions_do_not_invent_resolution(monkeypatch,version):
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload('{}',version=version)))
    result=GeminiProvider('dummy-key').complete('system','user',options=OPTIONS)
    assert result.model==MODEL and result.metadata.model_version==(version or None)
    assert result.metadata.prompt_tokens is None and result.prompt_tokens==0
    assert state.closes==state.clients


@pytest.mark.parametrize('version',['gemini-3.6-flash','gemini-flash-latest',PRIVATE,MODEL+'-'+PRIVATE,MODEL+'\n'+PRIVATE,MODEL+'-'+'1'*70])
def test_untrusted_reported_version_never_enters_output_or_logs(monkeypatch,version):
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload('{}',version=version)))
    with capture_logger('generation.providers.gemini') as sink:
        with pytest.raises(ProviderError) as caught:GeminiProvider(PRIVATE).complete(PRIVATE,PRIVATE,options=OPTIONS)
    assert caught.value.code=='provider_model_unavailable'
    assert PRIVATE not in str(caught.value)+sink.getvalue()
    assert len(state.requests)==1 and state.closes==state.clients


@pytest.mark.parametrize('text',[None,'','  ','\n\t'])
def test_native_empty_complete_is_error_without_retry(monkeypatch,text):
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload(text)))
    with pytest.raises(ProviderError) as caught:GeminiProvider('dummy-key').complete('system','user',options=OPTIONS)
    assert caught.value.code=='generation_failed'
    assert len(state.requests)==1 and state.closes==state.clients


@pytest.mark.parametrize('finish',['MAX_TOKENS','SAFETY','RECITATION',PRIVATE])
def test_non_success_complete_never_passes_syntactically_valid_json(monkeypatch,finish):
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload('{}',finish=finish)))
    with capture_logger('generation.providers.gemini') as sink:
        with pytest.raises(ProviderError) as caught:GeminiProvider(PRIVATE).complete('system',PRIVATE,options=OPTIONS)
    assert caught.value.code=='generation_failed'
    assert PRIVATE not in str(caught.value)+sink.getvalue()
    assert len(state.requests)==1 and state.closes==state.clients


def test_prompt_block_feedback_refuses_even_if_body_was_present(monkeypatch):
    raw=response_payload('{}',finish=None);raw['promptFeedback']={'blockReason':'SAFETY'}
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=raw))
    with pytest.raises(ProviderError) as caught:GeminiProvider('dummy-key').complete('system','user',options=OPTIONS)
    assert caught.value.code=='generation_failed' and len(state.requests)==1


def test_stream_merges_partial_cumulative_counts_and_retains_zero(monkeypatch):
    chunks=[response_payload('first',finish=None,usage={'promptTokenCount':10,'candidatesTokenCount':5,'thoughtsTokenCount':0,'totalTokenCount':15}),
            response_payload('last',usage={'candidatesTokenCount':0,'thoughtsTokenCount':7,'totalTokenCount':17})]
    state=native_client(monkeypatch,httpx2,lambda request:sse(request,chunks))
    output=list(GeminiProvider('dummy-key').stream('system','user'));usage=output[-1]
    assert output[:-1]==['first','last'] and isinstance(usage,UsageInfo)
    assert (usage.prompt_tokens,usage.completion_tokens)==(10,0)
    assert (usage.metadata.prompt_tokens,usage.metadata.candidate_tokens,usage.metadata.thoughts_tokens,usage.metadata.total_tokens)==(10,0,7,17)
    assert usage.model==MODEL and usage.metadata.model_version==VERSION
    assert len(state.requests)==1 and state.closes==state.clients


@pytest.mark.parametrize('method',['complete','stream'])
def test_native_thought_summaries_never_become_business_text(monkeypatch,method):
    raw=response_payload('visible',usage={'thoughtsTokenCount':7})
    raw['candidates'][0]['content']['parts'].insert(0,{'thought':True,'text':PRIVATE})
    state=native_client(monkeypatch,httpx2,lambda request:sse(request,[raw]) if method=='stream' else httpx2.Response(200,request=request,json=raw))
    with capture_logger('generation.providers.gemini') as sink:
        result=GeminiProvider(PRIVATE)
        output=list(result.stream(PRIVATE,PRIVATE)) if method=='stream' else result.complete(PRIVATE,PRIVATE,options=OPTIONS)
    assert (output[0] if method=='stream' else output.text)=='visible'
    assert PRIVATE not in sink.getvalue()
    assert state.closes==state.clients


@pytest.mark.parametrize('later',[MODEL+'-002','gemini-3.6-flash',PRIVATE])
def test_stream_version_change_after_text_never_replays_or_completes(monkeypatch,later):
    state=native_client(monkeypatch,httpx2,lambda request:sse(request,[response_payload('first',finish=None),response_payload(PRIVATE,version=later)]))
    output=GeminiProvider('dummy-key').stream('system','user');assert next(output)=='first'
    with pytest.raises(ProviderError) as caught:list(output)
    assert caught.value.code=='provider_model_unavailable' and PRIVATE not in str(caught.value)
    assert len(state.requests)==1 and state.closes==state.clients


def test_invalid_usage_is_unknown_without_breaking_valid_body(monkeypatch):
    raw=response_payload('{}',usage={'promptTokenCount':-1,'candidatesTokenCount':-2,'thoughtsTokenCount':-3,'totalTokenCount':2**31})
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=raw))
    result=GeminiProvider('dummy-key').complete('system','user',options=OPTIONS)
    assert result.text=='{}' and (result.prompt_tokens,result.completion_tokens)==(0,0)
    assert all(getattr(result.metadata,field) is None for field in ['prompt_tokens','candidate_tokens','thoughts_tokens','total_tokens'])
    assert state.closes==state.clients


@pytest.mark.django_db
@pytest.mark.parametrize('operation',['save','activate','verify'])
@pytest.mark.parametrize('reported',[VERSION,None])
def test_real_key_consumers_record_only_this_reported_version(api_client,make_user_with_key,monkeypatch,operation,reported):
    user=make_user_with_key();api_client.force_authenticate(user=user)
    old=UserProviderKey.objects.get(user=user,provider='gemini');old_cipher=old.api_key_encrypted
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload('OK',version=reported)))
    url='/api/auth/llm-config/' if operation!='verify' else '/api/auth/llm-config/verify/'
    body={'provider':'gemini'}
    if operation=='save':body['api_key']='candidate-synthetic-key'
    reply=getattr(api_client,'patch' if operation=='activate' else 'post')(url,body,format='json')
    assert reply.status_code==200
    old.refresh_from_db()
    assert old.verification_model==MODEL and old.verification_resolved_model==reported
    assert reply.data['providers']['gemini']['verification_resolved_model']==reported
    if operation!='save':assert old.api_key_encrypted==old_cipher
    assert len(state.requests)==1 and state.closes==state.clients


@pytest.mark.django_db
def test_get_retains_historical_model_evidence_without_rechecking(api_client,make_user_with_key,monkeypatch):
    user=make_user_with_key();api_client.force_authenticate(user=user)
    key=UserProviderKey.objects.get(user=user,provider='gemini')
    key.verification_model='gemini-2.5-flash';key.verification_resolved_model='gemini-2.5-flash-001';key.verification_status='valid';key.save()
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload('OK')))
    reply=api_client.get('/api/auth/llm-config/');key.refresh_from_db()
    assert reply.data['providers']['gemini']['model']==MODEL
    assert key.verification_model=='gemini-2.5-flash' and key.verification_resolved_model=='gemini-2.5-flash-001'
    assert not state.requests


@pytest.mark.django_db
@pytest.mark.parametrize('raw,version,finish',[('{}',VERSION,'STOP'),('broken '+PRIVATE,VERSION,'STOP'),('{}',VERSION,'MAX_TOKENS'),('{}','gemini-3.6-flash','STOP')])
def test_native_infer_keeps_write_and_log_boundaries(api_client,make_user_with_key,monkeypatch,raw,version,finish):
    user=make_user_with_key();api_client.force_authenticate(user=user)
    fragment=Fragment.objects.create(owner=user,text='synthetic author input',tags={'old':'manual'},is_confirmed=True)
    before=(fragment.tags,fragment.updated_at,fragment.is_confirmed)
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload(raw,version=version,finish=finish)))
    with CaptureQueriesContext(connection) as queries:
        response=api_client.post(f'/api/examples/fragments/{fragment.id}/infer-tags/',{},format='json')
    fragment.refresh_from_db();valid=raw=='{}' and version==VERSION and finish=='STOP'
    assert response.status_code==(200 if valid else 503 if version!=VERSION else 500)
    log=LlmCallLog.objects.get(user=user,feature='tag_inference')
    assert log.status==('success' if valid else 'error') and PRIVATE not in log.error_message
    if valid:assert fragment.tags=={} and not fragment.is_confirmed and fragment.updated_at!=before[1]
    else:
        assert (fragment.tags,fragment.updated_at,fragment.is_confirmed)==before
        assert not any(q['sql'].upper().startswith('UPDATE "EXAMPLES_FRAGMENT"') for q in queries)
    assert len(state.requests)==1 and state.closes==state.clients


@pytest.mark.django_db
@pytest.mark.parametrize('finish,version',[('STOP',VERSION),('MAX_TOKENS',VERSION),('STOP','gemini-3.6-flash')])
def test_real_sse_wrapper_preserves_terminal_and_persistence_contract(api_client,make_user_with_key,monkeypatch,finish,version):
    user=make_user_with_key();card=BaseCardFactory(owner=user);api_client.force_authenticate(user=user)
    chunks=[response_payload('first',finish=None),response_payload('last',finish=finish,version=version,usage={'promptTokenCount':10,'candidatesTokenCount':5,'thoughtsTokenCount':7,'totalTokenCount':22})]
    state=native_client(monkeypatch,httpx2,lambda request:sse(request,chunks))
    reply=api_client.post(reverse('generate-stream'),{'character_id':str(card.id),'scene_input':{'location':'cafe','intent':'talk'}},format='json')
    events=[json.loads(line.removeprefix('data:').strip()) for line in b''.join(reply.streaming_content).decode().splitlines() if line.startswith('data:')]
    valid=finish=='STOP' and version==VERSION
    assert events[0]=={'type':'chunk','content':'first'}
    assert events[-1]['type']==('done' if valid else 'error')
    if not valid:assert events[-1]['code']==('provider_model_unavailable' if version!=VERSION else 'generation_failed')
    log=LlmCallLog.objects.get(user=user,feature='character_generate')
    assert log.status==('success' if valid else 'error') and log.model==(MODEL if valid else '')
    if valid:assert (log.prompt_tokens,log.completion_tokens)==(10,5)
    assert len(state.requests)==1 and state.closes==state.clients


@pytest.mark.parametrize('last_finish',['STOP','FINISH_REASON_UNSPECIFIED'])
def test_stream_intermediate_unspecified_is_not_a_premature_refusal(monkeypatch,last_finish):
    chunks=[response_payload('first',finish='FINISH_REASON_UNSPECIFIED'),response_payload('last',finish=last_finish)]
    state=native_client(monkeypatch,httpx2,lambda request:sse(request,chunks))
    output=GeminiProvider('dummy-key').stream('system','user');assert next(output)=='first'
    if last_finish=='STOP':
        rest=list(output);assert rest[0]=='last' and rest[-1].metadata.finish_reason=='STOP'
    else:
        assert next(output)=='last'
        with pytest.raises(ProviderError) as caught:list(output)
        assert caught.value.code=='generation_failed'
    assert len(state.requests)==1 and state.closes==state.clients


def test_metadata_placeholder_does_not_erase_reported_stop(monkeypatch):
    chunks=[response_payload('first'),response_payload(None,finish='FINISH_REASON_UNSPECIFIED',usage={'candidatesTokenCount':0})]
    state=native_client(monkeypatch,httpx2,lambda request:sse(request,chunks))
    output=list(GeminiProvider('dummy-key').stream('system','user'))
    assert output[0]=='first' and output[-1].metadata.finish_reason=='STOP'
    assert output[-1].completion_tokens==0 and state.closes==state.clients


@pytest.mark.django_db
def test_explicit_verify_recovers_after_wrong_reported_version(api_client,make_user_with_key,monkeypatch):
    user=make_user_with_key();api_client.force_authenticate(user=user)
    key=UserProviderKey.objects.get(user=user,provider='gemini');cipher=key.api_key_encrypted
    versions=iter(['gemini-3.6-flash',VERSION])
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload('OK',version=next(versions))))
    failed=api_client.post('/api/auth/llm-config/verify/',{'provider':'gemini'},format='json')
    key.refresh_from_db()
    assert failed.status_code==503 and key.verification_status=='provider_model_unavailable'
    assert key.api_key_encrypted==cipher
    restored=api_client.post('/api/auth/llm-config/verify/',{'provider':'gemini'},format='json')
    key.refresh_from_db()
    assert restored.status_code==200 and key.verification_status=='valid' and key.verification_resolved_model==VERSION
    assert key.api_key_encrypted==cipher and len(state.requests)==2 and state.closes==state.clients


@pytest.mark.django_db
def test_bad_candidate_version_never_overwrites_key_or_active_provider(api_client,make_user_with_key,monkeypatch):
    from users.encryption import encrypt_key
    user=make_user_with_key();api_client.force_authenticate(user=user)
    key=UserProviderKey.objects.get(user=user,provider='gemini');cipher=key.api_key_encrypted
    UserProviderKey.objects.create(user=user,provider='groq',api_key_encrypted=encrypt_key('stored-groq-test-key'))
    user.llm_config.provider='groq';user.llm_config.save(update_fields=['provider'])
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload('OK',version='gemini-3.6-flash')))
    failed=api_client.post('/api/auth/llm-config/',{'provider':'gemini','api_key':'candidate-synthetic-key'},format='json')
    key.refresh_from_db();user.llm_config.refresh_from_db()
    assert failed.status_code==503 and key.api_key_encrypted==cipher and user.llm_config.provider=='groq'
    assert len(state.requests)==1 and state.closes==state.clients


@pytest.mark.django_db
def test_native_judge_reasoning_and_model_log_keep_existing_meaning(api_client,make_user_with_key,monkeypatch):
    from evaluation.models import ConsistencyScore
    user=make_user_with_key();card=BaseCardFactory(owner=user);api_client.force_authenticate(user=user)
    generation=LlmCallLog.objects.create(user=user,generation_id=uuid.uuid4(),feature='character_generate',model='historical-generated-model',latency_ms=1,status='success')
    raw=json.dumps({'score':8,'reasoning':'synthetic judge reasoning'})
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=response_payload(raw,usage={'promptTokenCount':10,'candidatesTokenCount':5,'thoughtsTokenCount':7,'totalTokenCount':22})))
    reply=api_client.post('/api/evaluation/score/',{'generationId':str(generation.generation_id),'characterId':str(card.id),'generatedText':'synthetic author text'},format='json')
    assert reply.status_code==200 and reply.data['reasoning']=='synthetic judge reasoning'
    saved=ConsistencyScore.objects.get(user=user)
    assert saved.judge_reasoning=='synthetic judge reasoning' and saved.judge_model==MODEL
    log=LlmCallLog.objects.get(user=user,feature='consistency_check')
    assert log.model==MODEL and (log.prompt_tokens,log.completion_tokens)==(10,5)
    assert len(state.requests)==1 and state.closes==state.clients


def test_precise_metadata_logs_do_not_guess_missing_thoughts_or_costs(monkeypatch):
    raw=response_payload('{}',usage={'promptTokenCount':10,'candidatesTokenCount':5,'totalTokenCount':22})
    native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=raw))
    with capture_logger('generation.providers.gemini') as sink:GeminiProvider(PRIVATE).complete(PRIVATE,PRIVATE,options=OPTIONS)
    text=sink.getvalue()
    assert 'unattributed_tokens=7' in text and 'thoughts_tokens=None' in text and 'unaccounted_tokens=None' in text
    assert PRIVATE not in text and 'model_version='+VERSION in text
