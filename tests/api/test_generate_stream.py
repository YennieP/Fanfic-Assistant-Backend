"""生成 SSE 链路 · 成功路径契约测试（P0）。

测的是**契约**而非内容：注入 FakeProvider，消费 SSE 流，断言事件序列结构，
**不断言生成了什么文字**（LLM 输出非确定）。用了 FakeProvider 即保证全程不触真实 API。
"""
import json
from types import SimpleNamespace

import pytest
from django.urls import reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from generation.providers.base import ProviderError
from generation.views import GenerateStreamView
from logs.context import request_id_var
from logs.middleware import RequestLoggingMiddleware
from logs.models import LlmCallLog, VectorSearchLog
from tests.factories import BaseCardFactory
from tests.fakes import FakeProvider
from users.models import UserLLMConfig


def _parse_sse(response):
    """把 StreamingHttpResponse 的 streaming_content 解析成事件 dict 列表。

    SSE 行格式：`data: {json}\n\n`。逐块拼成字节串，按行抽取 data: 负载。
    """
    body = b''.join(response.streaming_content).decode('utf-8')
    events = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith('data:'):
            events.append(json.loads(line[len('data:'):].strip()))
    return events


@pytest.mark.django_db
def test_generate_stream_success_contract(api_client, make_user_with_key, monkeypatch):
    # 注入 resolver 结果，确保测试不触发真实 provider。
    monkeypatch.setattr(
        'generation.views.resolve_active_provider',
        lambda _user: SimpleNamespace(
            name='gemini', instance=FakeProvider(chunks=['你好', '世界']),
        ),
    )

    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    api_client.force_authenticate(user=user)

    resp = api_client.post(
        reverse('generate-stream'),
        data={
            'character_id': str(character.id),
            'scene_input': {'location': '咖啡馆', 'intent': '两人初次见面'},
        },
        format='json',
    )

    assert resp.status_code == 200
    assert resp['Content-Type'] == 'text/event-stream'

    events = _parse_sse(resp)

    # 契约 1：至少有一个 chunk 事件。
    chunk_events = [e for e in events if e.get('type') == 'chunk']
    assert len(chunk_events) >= 1

    # 契约 2：最后一个事件是 done，且带 generationId。
    assert events[-1]['type'] == 'done'
    assert events[-1]['generationId']

    # 契约 3：不应出现 error 事件。
    assert not any(e.get('type') == 'error' for e in events)


@pytest.mark.django_db
def test_stream_keeps_request_id_after_middleware_returns(make_user_with_key, monkeypatch):
    monkeypatch.setattr(
        'generation.views.resolve_active_provider',
        lambda _user: SimpleNamespace(
            name='gemini', instance=FakeProvider(chunks=['hello']),
        ),
    )
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)

    def fake_style_search(
        character, scene_input, user, provider_name, generation_id, limit=5,
    ):
        VectorSearchLog.objects.create(
            request_id=request_id_var.get(),
            user=user,
            generation_id=generation_id,
            feature='style_retrieval',
            character_id=character.id,
            query_text='test query',
            top_k=limit,
            result_count=0,
            top_similarity=None,
            latency_ms=1,
            style_injected=False,
        )
        return []

    monkeypatch.setattr('generation.views._get_style_fragments', fake_style_search)
    rest_records = []
    monkeypatch.setattr('logs.middleware.logger.handle', rest_records.append)

    request = APIRequestFactory().post(
        reverse('generate-stream'),
        data={
            'character_id': str(character.id),
            'scene_input': {'location': 'cafe', 'intent': 'meet'},
        },
        format='json',
    )
    request.user = user
    force_authenticate(request, user=user)
    response = RequestLoggingMiddleware(GenerateStreamView.as_view())(request)

    assert request_id_var.get() == ''
    events = _parse_sse(response)
    assert events[-1]['type'] == 'done'

    llm_log = LlmCallLog.objects.get(feature='character_generate')
    vector_log = VectorSearchLog.objects.get(feature='style_retrieval')
    assert len(rest_records) == 1
    assert rest_records[0].status_code == 200
    assert rest_records[0].request_id == llm_log.request_id == vector_log.request_id


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('provider_error', 'expected_code'),
    [
        (ProviderError('quota reached', code='provider_quota_daily'), 'provider_quota_daily'),
        (RuntimeError('provider socket failed'), 'generation_failed'),
    ],
)
def test_stream_error_records_business_failure(
    api_client, make_user_with_key, monkeypatch, provider_error, expected_code,
):
    monkeypatch.setattr(
        'generation.views.resolve_active_provider',
        lambda _user: SimpleNamespace(
            name='gemini', instance=FakeProvider(error=provider_error),
        ),
    )
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        reverse('generate-stream'),
        data={
            'character_id': str(character.id),
            'scene_input': {'location': 'cafe', 'intent': 'meet'},
        },
        format='json',
    )
    events = _parse_sse(response)

    assert response.status_code == 200
    assert events[-1] == {'type': 'error', 'code': expected_code}
    log = LlmCallLog.objects.get(feature='character_generate')
    assert log.status == LlmCallLog.Status.ERROR
    assert log.error_message == expected_code
    assert str(provider_error) not in log.error_message


@pytest.mark.django_db
def test_client_interruption_is_recorded(api_client, make_user_with_key, monkeypatch):
    monkeypatch.setattr(
        'generation.views.resolve_active_provider',
        lambda _user: SimpleNamespace(
            name='gemini', instance=FakeProvider(chunks=['first', 'second']),
        ),
    )
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        reverse('generate-stream'),
        data={
            'character_id': str(character.id),
            'scene_input': {'location': 'cafe', 'intent': 'meet'},
        },
        format='json',
    )
    iterator = iter(response.streaming_content)
    first_event = next(iterator).decode('utf-8')
    assert '"type": "chunk"' in first_event
    response.close()

    log = LlmCallLog.objects.get(feature='character_generate')
    assert log.status == LlmCallLog.Status.ERROR
    assert log.error_message == 'stream interrupted before completion'


@pytest.mark.django_db
def test_unknown_stored_provider_fails_closed_in_sse(api_client, user_factory):
    user = user_factory()
    UserLLMConfig.objects.create(user=user, provider='unknown-provider')
    character = BaseCardFactory(owner=user)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        reverse('generate-stream'),
        data={
            'character_id': str(character.id),
            'scene_input': {'location': 'cafe', 'intent': 'meet'},
        },
        format='json',
    )

    assert response.status_code == 200
    assert _parse_sse(response) == [{
        'type': 'error',
        'code': 'provider_model_unavailable',
    }]
