"""Native SDK termination metadata; every HTTP request uses MockTransport."""
import anthropic
import groq
import httpx
import httpx2
import openai
import pytest

from generation.providers.anthropic import AnthropicProvider
from generation.providers.base import CompletionOptions
from generation.providers.cerebras import CerebrasProvider
from generation.providers.gemini import GeminiProvider
from generation.providers.groq import GroqProvider
from generation.providers.openrouter import OpenRouterProvider
from tests.test_gemini_sdk_bridge import native_client


@pytest.mark.parametrize('provider_name',['gemini','groq','anthropic','cerebras','openrouter'])
@pytest.mark.parametrize('termination',['normal','truncated','alternate'])
def test_completion_keeps_real_sdk_finish_reason(monkeypatch,provider_name,termination):
    reasons={'gemini':{'normal':'STOP','truncated':'MAX_TOKENS','alternate':'SAFETY'},
             'anthropic':{'normal':'end_turn','truncated':'max_tokens','alternate':'stop_sequence'}}
    finish=reasons.get(provider_name,{'normal':'stop','truncated':'length','alternate':'content_filter'})[termination]
    wire=[]
    if provider_name=='gemini':
        body={'candidates':[{'content':{'parts':[{'text':'{}'}]},'finishReason':finish}]}
        state=native_client(monkeypatch,httpx2,lambda request:httpx2.Response(200,request=request,json=body))
        provider=GeminiProvider('dummy-key')
    else:
        library=httpx if provider_name in ['anthropic','groq'] else httpx2
        if provider_name=='anthropic':
            body={'id':'msg_synthetic','type':'message','role':'assistant','model':'synthetic',
                  'content':[{'type':'text','text':'{}'}],'stop_reason':finish,'stop_sequence':None,
                  'usage':{'input_tokens':10,'output_tokens':5}}
            real=anthropic.Anthropic
            path='generation.providers.anthropic.anthropic_sdk.Anthropic'
            sdk_http=anthropic.DefaultHttpxClient
            cls=AnthropicProvider
        else:
            body={'id':'synthetic','object':'chat.completion','created':0,'model':'synthetic',
                  'choices':[{'index':0,'message':{'role':'assistant','content':'{}'},'finish_reason':finish}],
                  'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
            if provider_name=='groq':
                real=groq.Groq;path='generation.providers.groq.groq_sdk.Groq';sdk_http=groq.DefaultHttpxClient;cls=GroqProvider
            else:
                real=openai.OpenAI;path='generation.providers.openai_compatible.OpenAI';sdk_http=openai.DefaultHttpxClient
                cls=CerebrasProvider if provider_name=='cerebras' else OpenRouterProvider
        def handler(request):
            wire.append(request)
            return library.Response(200,request=request,json=body)
        clients=[]
        def factory(**kwargs):
            client=real(**kwargs,http_client=sdk_http(transport=library.MockTransport(handler),trust_env=False))
            clients.append(client)
            return client
        monkeypatch.setattr(path,factory)
        provider=cls('dummy-key')
    try:
        result=provider.complete('synthetic-system','synthetic-user',options=CompletionOptions(response_format='json',max_tokens=1000))
        assert result.text=='{}'
        assert result.finish_reason==finish
        assert len(state.requests if provider_name=='gemini' else wire)==1
    finally:
        if provider_name!='gemini':
            for client in clients: client.close()
