"""Raw completion -> real inference parser/logging -> API/CAS/SQLite contract."""
import json
from types import SimpleNamespace

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
import httpx2
import pytest

from core.taxonomy import TAXONOMY, TAXONOMY_EN
from examples.models import Article, Fragment
from generation.providers.base import CompleteResult, ProviderError
from generation.providers.gemini import GeminiProvider
from logs.models import LlmCallLog
from tests.factories import BaseCardFactory
from tests.test_gemini_sdk_bridge import native_client


ERROR = {'code':'generation_failed', 'detail':'标签推断失败，请稍后重试'}
MALFORMED = [
    None, '', '  ', 'I cannot complete this request.', '{"scene_type":',
    '{"scene_type":"日常闲聊",',
    'Explanation before {"scene_type":"日常闲聊"}',
    '{"scene_type":"日常闲聊"} trailing explanation',
    '[]', 'null', 'true', '1', '"a string"',
    '{"scene_type":"日常闲聊","scene_type":null}',
    '{"emotion":{"shared":["平静"],"shared":null}}',
    '{"scene_type":NaN}', '{"scene_type":Infinity}',
    '{"unexpected":"private-vendor-marker"}',
    '{"scene_type":true}', '{"scene_type":0}',
    '{"scene_type":[]}', '{"scene_type":{}}',
    '{"scene_type":"invented model label"}',
    '{"emotion":"平静"}', '{"emotion":{"unexpected":"平静"}}',
    '{"emotion":{"shared":["平静",false]}}',
    '{"emotion":{"shared":["平静","invented"]}}',
    '{"emotion":{"shared":123}}', '{"emotion":{"intensity":[]}}',
]


def make_fragment(user):
    card=BaseCardFactory(owner=user)
    article=Article.objects.create(owner=user,character=card,title='synthetic article',content='synthetic text')
    return Fragment.objects.create(owner=user,article=article,character=card,text='synthetic fragment',
        order=1,tags={'scene_type':'existing-custom-label'},is_confirmed=True,embedding=[0.25]*768,start_line=1,end_line=1)


def snapshot(fragment):
    fragment.refresh_from_db()
    data={name:getattr(fragment,name) for name in ['text','tags','updated_at','is_confirmed','fragment_type','start_line','end_line']}
    data['embedding']=list(fragment.embedding) if fragment.embedding is not None else None
    return data


class RawProvider:
    def __init__(self,text,finish=None,interference=None,error=None):
        self.text=text;self.finish=finish;self.interference=interference;self.error=error;self.calls=[]

    def complete(self,system_prompt,user_prompt,*,options):
        self.calls.append(options)
        if self.interference: self.interference()
        if self.error: raise self.error
        result=CompleteResult(text=self.text,model='synthetic',prompt_tokens=10,completion_tokens=5)
        # Works on the pre-fix dataclass too; no missing-field error as a fake regression.
        result.finish_reason=self.finish
        return result


def request(api_client,user,fragment,provider,monkeypatch,lang='zh'):
    monkeypatch.setattr('examples.views.resolve_active_provider',lambda actor:SimpleNamespace(instance=provider))
    api_client.force_authenticate(user=user)
    return api_client.post(f'/api/examples/fragments/{fragment.id}/infer-tags/',{'lang':lang},format='json')


@pytest.mark.django_db
@pytest.mark.parametrize('raw',MALFORMED,ids=['none','empty','whitespace','refusal-text','broken-json','truncated-json',
    'prose-prefix','prose-suffix','array-root','null-root','bool-root','number-root','string-root',
    'duplicate-key','duplicate-emotion-key','nan','infinity','unknown-key','bool-value','number-value',
    'array-value','object-value','unknown-enum','emotion-string','unknown-emotion-field',
    'mixed-emotion-array','unknown-emotion-enum','number-emotion','array-intensity'])
def test_invalid_raw_output_never_reaches_fragment_update(api_client,user_factory,monkeypatch,raw):
    user=user_factory();fragment=make_fragment(user);before=snapshot(fragment)
    provider=RawProvider(raw)
    with CaptureQueriesContext(connection) as queries:
        response=request(api_client,user,fragment,provider,monkeypatch)
    after=snapshot(fragment)
    fragment_updates=sum(q['sql'].startswith('UPDATE "examples_fragment"') for q in queries.captured_queries)
    print('API003_OBSERVATION '+json.dumps({'status':response.status_code,'fragment_updates':fragment_updates,
        'tags_unchanged':after['tags']==before['tags'],'version_unchanged':after['updated_at']==before['updated_at'],
        'confirmation_unchanged':after['is_confirmed']==before['is_confirmed'],'embedding_unchanged':after['embedding']==before['embedding']}))
    assert response.status_code == 500
    assert response.data == ERROR
    assert after==before
    assert fragment_updates==0
    assert len(provider.calls)==1
    assert provider.calls[0].response_format=='json' and provider.calls[0].max_tokens==1000
    log=LlmCallLog.objects.get(user=user,feature='tag_inference')
    assert log.status=='error' and log.error_message=='generation_failed'


@pytest.mark.django_db
@pytest.mark.parametrize('finish',['MAX_TOKENS','SAFETY','RECITATION','length','content_filter','max_tokens','refusal','tool_calls','OTHER'])
def test_known_incomplete_finish_rejects_even_valid_json(api_client,user_factory,monkeypatch,finish):
    user=user_factory();fragment=make_fragment(user);before=snapshot(fragment)
    response=request(api_client,user,fragment,RawProvider('{"scene_type":"日常闲聊"}',finish),monkeypatch)
    assert response.status_code==500 and response.data==ERROR
    assert snapshot(fragment)==before
    assert LlmCallLog.objects.get(user=user,feature='tag_inference').status=='error'


@pytest.mark.django_db
@pytest.mark.parametrize('raw', ['{}','{"scene_type":null,"emotion":{"shared":[],"intensity":null}}',
    '{"scene_type":"null","emotion":{"shared":[null,"null"],"intensity":"null"}}',
    '{"scene_type":null,"scene_privacy":null,"initiative":null,"emotion":{"shared":null,"intensity":null},"target_type":null,"target_count":null,"relationship_state":null,"speech_intent":null}'])
def test_explicit_no_label_is_successful_inference(api_client,user_factory,monkeypatch,raw):
    user=user_factory();fragment=make_fragment(user);before=snapshot(fragment)
    response=request(api_client,user,fragment,RawProvider(raw,'STOP'),monkeypatch)
    assert response.status_code==200 and response.data['tags']=={}
    fragment.refresh_from_db()
    assert fragment.tags=={} and fragment.is_confirmed is False and fragment.embedding is None
    assert fragment.updated_at!=before['updated_at'] and fragment.text==before['text']
    assert LlmCallLog.objects.get(user=user,feature='tag_inference').status=='success'


@pytest.mark.django_db
@pytest.mark.parametrize('lang',['zh','en'])
@pytest.mark.parametrize('finish',[None,'STOP','stop','end_turn','stop_sequence'])
@pytest.mark.parametrize('fenced',[False,True])
def test_valid_sparse_tags_keep_language_and_legacy_emotion_normalization(api_client,user_factory,monkeypatch,lang,finish,fenced):
    taxonomy=TAXONOMY_EN if lang=='en' else TAXONOMY
    raw={'scene_type':taxonomy['scene_type'][0],'emotion':{'shared':taxonomy['emotion']['shared'][0],'intensity':None},'initiative':None}
    text=json.dumps(raw,ensure_ascii=False)
    if fenced: text='```json\n'+text+'\n```'
    user=user_factory();fragment=make_fragment(user)
    response=request(api_client,user,fragment,RawProvider(text,finish),monkeypatch,lang)
    assert response.status_code==200
    assert response.data['tags']=={'scene_type':taxonomy['scene_type'][0],'emotion':{'shared':[taxonomy['emotion']['shared'][0]]}}


@pytest.mark.django_db
@pytest.mark.parametrize('lang',['zh','en'])
def test_full_tag_shape_with_multiple_emotions_remains_usable(api_client,user_factory,monkeypatch,lang):
    taxonomy=TAXONOMY_EN if lang=='en' else TAXONOMY
    expected={field:taxonomy[field][0] for field in ['scene_type','scene_privacy','initiative','target_type','target_count','relationship_state','speech_intent']}
    expected['emotion']={'shared':taxonomy['emotion']['shared'][:2],'intensity':taxonomy['emotion']['intensity'][0]}
    user=user_factory();fragment=make_fragment(user)
    response=request(api_client,user,fragment,RawProvider(json.dumps(expected,ensure_ascii=False),'STOP'),monkeypatch,lang)
    assert response.status_code==200 and response.data['tags']==expected


@pytest.mark.django_db
def test_wrong_language_label_does_not_replace_existing_tags(api_client,user_factory,monkeypatch):
    user=user_factory();fragment=make_fragment(user);before=snapshot(fragment)
    response=request(api_client,user,fragment,RawProvider('{"scene_type":"日常闲聊"}','STOP'),monkeypatch,'en')
    assert response.status_code==500 and response.data==ERROR and snapshot(fragment)==before


@pytest.mark.django_db
@pytest.mark.parametrize('finish',['MAX_TOKENS','SAFETY'])
def test_native_gemini_finish_is_not_lost_before_real_api_validation(api_client,user_factory,monkeypatch,finish):
    body={'candidates':[{'content':{'parts':[{'text':'{"scene_type":"日常闲聊"}'}]},'finishReason':finish}],
          'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':5,'totalTokenCount':15}}
    state=native_client(monkeypatch,httpx2,lambda req:httpx2.Response(200,request=req,json=body))
    user=user_factory();fragment=make_fragment(user);before=snapshot(fragment)
    response=request(api_client,user,fragment,GeminiProvider('dummy-key'),monkeypatch)
    assert response.status_code==500 and response.data==ERROR
    assert snapshot(fragment)==before and len(state.requests)==1
    assert state.closes==state.clients
    assert LlmCallLog.objects.get(user=user,feature='tag_inference').status=='error'


@pytest.mark.django_db
def test_recovery_is_manual_and_does_not_change_failed_snapshot(api_client,user_factory,monkeypatch):
    user=user_factory();fragment=make_fragment(user);before=snapshot(fragment)
    first=request(api_client,user,fragment,RawProvider('broken JSON'),monkeypatch)
    assert first.status_code==500 and snapshot(fragment)==before
    second=request(api_client,user,fragment,RawProvider('{"scene_type":"日常闲聊"}','STOP'),monkeypatch)
    assert second.status_code==200
    assert list(LlmCallLog.objects.filter(user=user,feature='tag_inference').order_by('created_at').values_list('status',flat=True))==['error','success']


@pytest.mark.django_db
def test_valid_raw_result_still_obeys_cas_after_concurrent_edit(api_client,user_factory,monkeypatch):
    user=user_factory();fragment=make_fragment(user)
    expected={}
    def interfere():
        Fragment.objects.filter(id=fragment.id).update(tags={'scene_type':'concurrent-custom'},updated_at=timezone.now())
        expected.update(snapshot(fragment))
    provider=RawProvider('{"scene_type":"日常闲聊"}','STOP',interference=interfere)
    response=request(api_client,user,fragment,provider,monkeypatch)
    assert response.status_code==409 and response.data['code']=='fragment_state_conflict'
    assert snapshot(fragment)==expected


@pytest.mark.django_db
def test_provider_error_contract_and_owner_guard_remain_intact(api_client,user_factory,monkeypatch):
    owner=user_factory();fragment=make_fragment(owner);before=snapshot(fragment)
    error=ProviderError('安全限流描述',code='provider_rate_limit',http_status=429)
    provider=RawProvider(None,error=error)
    denied=request(api_client,user_factory(),fragment,provider,monkeypatch)
    assert denied.status_code==404 and provider.calls==[]
    failure=request(api_client,owner,fragment,provider,monkeypatch)
    assert failure.status_code==429 and failure.data['code']=='provider_rate_limit'
    assert snapshot(fragment)==before
