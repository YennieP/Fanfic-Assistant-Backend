"""生成 SSE 链路 · 成功路径契约测试（P0）。

测的是**契约**而非内容：注入 FakeProvider，消费 SSE 流，断言事件序列结构，
**不断言生成了什么文字**（LLM 输出非确定）。用了 FakeProvider 即保证全程不触真实 API。
"""
import json

import pytest
from django.urls import reverse

from tests.factories import BaseCardFactory
from tests.fakes import FakeProvider


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
    # 注入 FakeProvider —— get_provider 是 provider 的唯一构造点，打桩即可。
    monkeypatch.setattr(
        'generation.views.get_provider',
        lambda name, api_key: FakeProvider(chunks=['你好', '世界']),
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
