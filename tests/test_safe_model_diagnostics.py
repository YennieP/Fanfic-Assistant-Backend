"""Keep model/prompt/key data out of logs without changing business results."""
from contextlib import contextmanager
import io
import json
import logging
import subprocess
import sys
from types import SimpleNamespace
import uuid

import httpx2
import pytest

from evaluation.models import ConsistencyScore
from evaluation.views import _parse_judge_response
from examples.llm_pipeline import infer_tags
from generation.providers.base import CompletionOptions, ProviderError
from generation.providers.gemini import GeminiProvider
from logs.models import LlmCallLog
from tests.factories import BaseCardFactory
from tests.fakes import FakeProvider
from tests.test_gemini_sdk_bridge import native_client


PROMPT = 'synthetic-private-prompt-marker'
OUTPUT = 'synthetic-private-model-output-marker'
KEY = 'synthetic-private-api-key-marker'


@contextmanager
def capture_logger(name):
    logger=logging.getLogger(name)
    level=logger.level
    sink=io.StringIO()
    handler=logging.StreamHandler(sink)
    logger.addHandler(handler);logger.setLevel(logging.INFO)
    try: yield sink
    finally:
        logger.removeHandler(handler);logger.setLevel(level);handler.close()


@pytest.mark.django_db
def test_valid_inference_logs_no_model_tags_or_prompt(user_factory):
    raw='{"scene_type":"日常闲聊","emotion":{"shared":["焦虑/不安"]}}'
    user=user_factory();provider=FakeProvider(api_key=KEY,complete_text=raw)
    with capture_logger('examples.llm_pipeline') as sink:
        tags=infer_tags(PROMPT,provider,user=user)
    logged=sink.getvalue()
    assert tags=={'scene_type':'日常闲聊','emotion':{'shared':['焦虑/不安']}}
    assert raw not in logged and '日常闲聊' not in logged and '焦虑/不安' not in logged
    assert PROMPT not in logged and KEY not in logged
    assert 'response_chars='+str(len(raw)) in logged
    assert 'tag_field_count=2' in logged
    log=LlmCallLog.objects.get(user=user,feature='tag_inference')
    assert (log.status,log.prompt_tokens,log.completion_tokens)==('success',10,5)


@pytest.mark.django_db
@pytest.mark.parametrize('raw', ['broken '+OUTPUT+' '+KEY, None, '', '  '])
def test_invalid_judge_output_logs_metadata_only(api_client,user_factory,monkeypatch,raw):
    user=user_factory();card=BaseCardFactory(owner=user)
    generation=LlmCallLog.objects.create(user=user,generation_id=uuid.uuid4(),feature='character_generate',model='synthetic',latency_ms=1,status='success')
    provider=FakeProvider(api_key=KEY,complete_text=raw)
    monkeypatch.setattr('evaluation.views.resolve_active_provider',lambda actor:SimpleNamespace(instance=provider,definition=SimpleNamespace(model='synthetic')))
    api_client.force_authenticate(user=user)
    with capture_logger('evaluation.views') as sink:
        response=api_client.post('/api/evaluation/score/',{'generationId':str(generation.generation_id),'characterId':str(card.id),'generatedText':PROMPT},format='json')
    logged=sink.getvalue()
    assert response.status_code==500 and response.data['code']=='evaluation_result_invalid'
    assert OUTPUT not in logged and KEY not in logged and PROMPT not in logged
    assert 'error_type=' in logged and 'response_chars=' in logged
    assert not ConsistencyScore.objects.filter(user=user).exists()
    # Provider call succeeded; parser failure keeps the existing LLM log meaning.
    judge=LlmCallLog.objects.get(user=user,feature='consistency_check')
    assert judge.status=='success' and judge.prompt_tokens==10 and judge.completion_tokens==5


def test_judge_parser_exception_has_no_response_excerpt():
    with pytest.raises(ValueError) as caught:
        _parse_judge_response('not-json '+OUTPUT+' '+KEY)
    assert OUTPUT not in str(caught.value) and KEY not in str(caught.value)


@pytest.mark.django_db
def test_valid_judge_reasoning_keeps_api_and_persistence(api_client,user_factory,monkeypatch):
    user=user_factory();card=BaseCardFactory(owner=user)
    generation=LlmCallLog.objects.create(user=user,generation_id=uuid.uuid4(),feature='character_generate',model='synthetic',latency_ms=1,status='success')
    provider=FakeProvider(complete_text=json.dumps({'score':8,'reasoning':OUTPUT}))
    monkeypatch.setattr('evaluation.views.resolve_active_provider',lambda actor:SimpleNamespace(instance=provider,definition=SimpleNamespace(model='synthetic')))
    api_client.force_authenticate(user=user)
    with capture_logger('evaluation.views') as sink:
        response=api_client.post('/api/evaluation/score/',{'generationId':str(generation.generation_id),'characterId':str(card.id),'generatedText':PROMPT},format='json')
    assert response.status_code==200 and response.data['reasoning']==OUTPUT
    saved=ConsistencyScore.objects.get(user=user)
    assert saved.judge_reasoning==OUTPUT and saved.generated_text==PROMPT
    assert OUTPUT not in sink.getvalue() and PROMPT not in sink.getvalue()


def test_gemini_capacity_does_not_echo_untrusted_finish(monkeypatch):
    response=SimpleNamespace(text=OUTPUT,usage_metadata=SimpleNamespace(prompt_token_count=10,candidates_token_count=5,total_token_count=22),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value=KEY))])
    monkeypatch.setattr('generation.providers.gemini_client.genai.Client',lambda **kwargs:SimpleNamespace(models=SimpleNamespace(generate_content=lambda **args:response)))
    with capture_logger('generation.providers.gemini') as sink:
        result=GeminiProvider(KEY).complete(PROMPT,'synthetic',options=CompletionOptions(response_format='text',max_tokens=64))
    logged=sink.getvalue()
    assert result.text==OUTPUT and result.finish_reason==KEY
    assert OUTPUT not in logged and PROMPT not in logged and KEY not in logged
    assert 'finish_reason=UNKNOWN' in logged
    assert 'prompt_tokens=10 completion_tokens=5 total_tokens=22 unattributed_tokens=7 max_output_tokens=64' in logged


@pytest.mark.parametrize('method',['complete','stream'])
@pytest.mark.parametrize('status',[429,503])
def test_native_sdk_error_and_retry_logs_exclude_payloads(monkeypatch,method,status):
    state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(status,request=request,json={'error':{'code':status,'message':OUTPUT+' '+KEY+' '+PROMPT}}))
    sleeps=[];monkeypatch.setattr('generation.providers.gemini.time.sleep',sleeps.append)
    with capture_logger('generation.providers.gemini') as sink:
        with pytest.raises(ProviderError) as caught:
            if method=='complete': GeminiProvider(KEY).complete(PROMPT,'synthetic',options=CompletionOptions(response_format='json'))
            else: list(GeminiProvider(KEY).stream(PROMPT,'synthetic'))
    logged=sink.getvalue()+str(caught.value)
    assert all(marker not in logged for marker in [OUTPUT,KEY,PROMPT])
    assert len(state.requests)==(1 if status==429 else 5 if method=='complete' else 7)
    assert caught.value.code==('provider_rate_limit' if status==429 else 'provider_temporarily_unavailable')
    assert state.closes==state.clients


@pytest.mark.parametrize('untrusted',[KEY,KEY+'\n'+OUTPUT],ids=['plain','multiline'])
def test_real_startup_sdk_unknown_enum_warning_does_not_echo_value(untrusted):
    # A fresh real Django startup avoids pytest's process-wide warning capture
    # replacing the production routing. SDK validation is native, without HTTP.
    code='''import json,os,sys,warnings
os.environ['DJANGO_SETTINGS_MODULE']='core.settings_test'
import django
django.setup()
from google.genai import types
response=types.GenerateContentResponse.model_validate({'candidates':[{'content':{'parts':[{'text':'synthetic-private-model-output-marker'}]},'finishReason':sys.argv[1]}]})
warnings.warn('benign-non-sdk-warning')
print(json.dumps({'text_preserved':response.text=='synthetic-private-model-output-marker','finish_preserved':response.candidates[0].finish_reason.value==sys.argv[1]}))
'''
    result=subprocess.run([sys.executable,'-B','-c',code,untrusted],capture_output=True,text=True,timeout=30)
    assert result.returncode==0
    assert json.loads(result.stdout)=={'text_preserved':True,'finish_preserved':True}
    assert KEY not in result.stderr and OUTPUT not in result.stderr
    assert 'unknown_enum_value' in result.stderr
    assert 'benign-non-sdk-warning' in result.stderr
