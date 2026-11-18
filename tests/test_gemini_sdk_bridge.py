"""Native SDK JSON/SSE contracts; every wire request uses MockTransport."""
import json
from types import SimpleNamespace

from google import genai
from google.genai import types
import httpx
import httpx2
import pytest

from examples.embedding import get_embedding
from generation.providers.base import CompletionOptions, ProviderError, UsageInfo
from generation.providers.gemini import GeminiProvider


MARKER = 'private-vendor-content'
OPTIONS = CompletionOptions(response_format='json', max_tokens=800, reasoning_effort='low')
SUCCESS = {
    'candidates': [{'content': {'parts': [{'text': 'result'}]}, 'finishReason': 'STOP'}],
    'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 5, 'totalTokenCount': 22},
}


class RetainedIterator:
    """Keep SDK generators alive so GC cannot impersonate explicit close()."""
    def __init__(self, inner):
        self.inner = inner
        self.close_calls = 0

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.inner)

    def close(self):
        self.close_calls += 1
        self.inner.close()


def native_client(monkeypatch, library, handler, *, close_error=False, iterator_close_error=False):
    real_client = genai.Client
    state = SimpleNamespace(clients=[], http_clients=[], requests=[], responses=[], policies=[], iterators=[], closes=[])

    def tracked_handler(request):
        state.requests.append(request)
        response = handler(request)
        state.responses.append(response)
        return response

    def factory(**kwargs):
        state.policies.append(kwargs.copy())
        policy = kwargs.pop('http_options', types.HttpOptions())
        # Inject the transport, not an externally-owned HTTP client: the SDK
        # must own/close its client exactly as it does in production.
        policy = policy.model_copy(update={'client_args': {**(policy.client_args or {}),
            'transport': library.MockTransport(tracked_handler), 'trust_env': False,
        }})
        client = real_client(**kwargs, http_options=policy)
        state.clients.append(client)
        state.http_clients.append(client._api_client._httpx_client)
        original_close = client.close

        def close():
            state.closes.append(client)
            original_close()
            if close_error:
                raise RuntimeError(MARKER)

        monkeypatch.setattr(client, 'close', close)
        original_stream = client.models.generate_content_stream

        def stream(**args):
            iterator = RetainedIterator(original_stream(**args))
            state.iterators.append(iterator)
            if iterator_close_error:
                original_iterator_close = iterator.close

                def close_iterator():
                    original_iterator_close()
                    raise RuntimeError(MARKER)

                monkeypatch.setattr(iterator, 'close', close_iterator)
            return iterator

        monkeypatch.setattr(client.models, 'generate_content_stream', stream)
        return client

    monkeypatch.setattr(genai, 'Client', factory)
    return state


def sse_response(library, request, *, metadata_first=False, mid_error=None, empty=False):
    class Body(library.SyncByteStream):
        def __iter__(self):
            if empty:
                return
            if metadata_first:
                yield b'data: {"usageMetadata":{"promptTokenCount":10}}\n\n'
            else:
                yield b'data: {"candidates":[{"content":{"parts":[{"text":"result"}]}}]}\n\n'
            if mid_error:
                raise mid_error(MARKER, request=request)
            if metadata_first:
                yield ('data: ' + json.dumps(SUCCESS) + '\n\n').encode()
            else:
                yield ('data: ' + json.dumps({'usageMetadata': SUCCESS['usageMetadata']}) + '\n\n').encode()

    return library.Response(200, request=request, headers={'content-type': 'text/event-stream'}, stream=Body())


@pytest.mark.parametrize('library', [httpx2, httpx], ids=['httpx2', 'httpx'])
@pytest.mark.parametrize('method', ['complete', 'stream'])
@pytest.mark.parametrize('kind', ['timeout', 'connection', 'protocol'])
def test_native_transport_errors_have_safe_bounded_retries(monkeypatch, caplog, library, method, kind):
    error_type = {'timeout': library.ReadTimeout, 'connection': library.ConnectError,
                  'protocol': library.RemoteProtocolError}[kind]

    def handler(request):
        raise error_type(MARKER, request=request)

    state = native_client(monkeypatch, library, handler)
    sleeps = []
    monkeypatch.setattr('generation.providers.gemini.time.sleep', sleeps.append)
    with pytest.raises(ProviderError) as caught:
        if method == 'complete':
            GeminiProvider('dummy-key').complete('system', 'user', options=OPTIONS)
        else:
            list(GeminiProvider('dummy-key').stream('system', 'user'))
    assert caught.value.code == ('provider_timeout' if kind == 'timeout' else 'provider_connection_failed')
    assert caught.value.http_status == 503
    assert len(state.requests) == (5 if method == 'complete' else 7)
    assert sleeps == ([1, 2, 4, 4] if method == 'complete' else [1, 2, 4, 8, 16, 32])
    assert state.closes == state.clients
    assert all(client.is_closed for client in state.http_clients)
    assert all(iterator.close_calls == 1 for iterator in state.iterators)
    assert MARKER not in str(caught.value) + caplog.text


@pytest.mark.parametrize('library', [httpx2, httpx], ids=['httpx2', 'httpx'])
@pytest.mark.parametrize('method', ['complete', 'stream'])
@pytest.mark.parametrize(('status', 'code', 'wire'), [
    (401, 'provider_key_invalid', 1), (402, 'provider_payment_required', 1),
    (404, 'provider_model_unavailable', 1), (429, 'provider_rate_limit', 1),
    (500, 'provider_temporarily_unavailable', 1), (503, 'provider_temporarily_unavailable', None),
])
def test_native_status_retry_boundaries(monkeypatch, caplog, library, method, status, code, wire):
    state = native_client(monkeypatch, library, lambda request: library.Response(
        status, request=request, json={'error': {'code': status, 'message': MARKER, 'status': 'UNAVAILABLE'}},
    ))
    sleeps = []
    monkeypatch.setattr('generation.providers.gemini.time.sleep', sleeps.append)
    with pytest.raises(ProviderError) as caught:
        if method == 'complete':
            GeminiProvider('dummy-key').complete('system', 'user', options=OPTIONS)
        else:
            list(GeminiProvider('dummy-key').stream('system', 'user'))
    assert caught.value.code == code
    assert len(state.requests) == (wire or (5 if method == 'complete' else 7))
    assert sleeps == ([] if wire else ([1, 2, 4, 4] if method == 'complete' else [1, 2, 4, 8, 16, 32]))
    assert state.closes == state.clients
    assert all(iterator.close_calls == 1 for iterator in state.iterators)
    assert MARKER not in str(caught.value) + caplog.text


@pytest.mark.parametrize('library', [httpx2, httpx], ids=['httpx2', 'httpx'])
@pytest.mark.parametrize('metadata_first', [False, True])
def test_native_midstream_timeout_never_replays_first_chunk(monkeypatch, library, metadata_first):
    state = native_client(monkeypatch, library, lambda request: sse_response(
        library, request, metadata_first=metadata_first, mid_error=library.ReadTimeout,
    ))
    sleeps = []
    monkeypatch.setattr('generation.providers.gemini.time.sleep', sleeps.append)
    emitted = []
    with pytest.raises(ProviderError) as caught:
        for item in GeminiProvider('dummy-key').stream('system', 'user'):
            emitted.append(item)
    assert caught.value.code == 'provider_timeout'
    assert emitted == ([] if metadata_first else ['result'])
    assert len(state.requests) == 1
    assert sleeps == []
    assert state.closes == state.clients
    assert state.iterators[0].close_calls == 1
    assert all(response.is_closed for response in state.responses)


@pytest.mark.parametrize('operation', ['json', 'key-text', 'stream', 'empty-stream', 'embedding'])
def test_native_success_keeps_wire_contract_and_closes_client(monkeypatch, operation):
    def handler(request):
        if operation == 'embedding':
            return httpx2.Response(200, request=request, json={'embeddings': [{'values': [0.0] * 768}]})
        if operation in ('stream', 'empty-stream'):
            return sse_response(httpx2, request, empty=operation == 'empty-stream')
        return httpx2.Response(200, request=request, json=SUCCESS)

    state = native_client(monkeypatch, httpx2, handler)
    if operation == 'embedding':
        assert get_embedding('synthetic tags', 'dummy-key') == [0.0] * 768
    elif operation in ('stream', 'empty-stream'):
        result = list(GeminiProvider('dummy-key').stream('system', 'user'))
        assert result == (['result', UsageInfo('gemini-2.5-flash', 10, 5)] if operation == 'stream'
                          else [UsageInfo('gemini-2.5-flash', 0, 0)])
    else:
        options = OPTIONS if operation == 'json' else CompletionOptions(response_format='text', max_tokens=64)
        result = GeminiProvider('dummy-key').complete('system', 'user', options=options)
        assert (result.text, result.model, result.prompt_tokens, result.completion_tokens) == (
            'result', 'gemini-2.5-flash', 10, 5,
        )
    assert len(state.requests) == 1
    assert state.closes == state.clients
    assert all(client.is_closed for client in state.http_clients)
    assert all(iterator.close_calls == 1 for iterator in state.iterators)
    policy = state.policies[0]
    assert policy['vertexai'] is False
    assert policy['http_options'].api_version == 'v1beta'
    assert policy['http_options'].timeout == 120_000
    assert policy['http_options'].retry_options.attempts == 1
    request = state.requests[0]
    assert request.url.host == 'generativelanguage.googleapis.com'
    assert request.url.path.startswith('/v1beta/models/')
    body = json.loads(request.content)
    if operation == 'embedding':
        assert request.url.path == '/v1beta/models/gemini-embedding-001:batchEmbedContents'
        assert body['requests'][0]['model'] == 'models/gemini-embedding-001'
        assert body['requests'][0]['outputDimensionality'] == 768
    else:
        assert 'gemini-2.5-flash:' in request.url.path
        config = body['generationConfig']
        assert config['maxOutputTokens'] == (800 if operation == 'json' else 64 if operation == 'key-text' else 4000)
        assert config.get('responseMimeType') == ('application/json' if operation == 'json' else None)
        assert 'thinkingConfig' not in config


@pytest.mark.parametrize('fails', [False, True])
def test_client_close_failure_preserves_result_or_original_error(monkeypatch, caplog, fails):
    state = native_client(monkeypatch, httpx2, lambda request: httpx2.Response(
        429 if fails else 200, request=request,
        json={'error': {'code': 429, 'message': MARKER}} if fails else SUCCESS,
    ), close_error=True)
    if fails:
        with pytest.raises(ProviderError, match='Gemini') as caught:
            GeminiProvider('dummy-key').complete('system', 'user', options=OPTIONS)
        assert caught.value.code == 'provider_rate_limit'
    else:
        assert GeminiProvider('dummy-key').complete('system', 'user', options=OPTIONS).text == 'result'
    assert state.closes == state.clients
    assert MARKER not in caplog.text


@pytest.mark.django_db
@pytest.mark.parametrize('library', [httpx2, httpx], ids=['httpx2', 'httpx'])
@pytest.mark.parametrize('cancel', [True, False])
def test_django_response_close_reaches_retained_gemini_resources(
    monkeypatch, api_client, make_user_with_key, cancel, library,
):
    from logs import decorators
    from logs.models import LlmCallLog
    from tests.factories import BaseCardFactory

    state = native_client(monkeypatch, library, lambda request: sse_response(library, request))
    provider = GeminiProvider('dummy-key')
    retained = []
    real_stream = provider.stream

    def stream(*args):
        gen = real_stream(*args)
        retained.append(gen)
        return gen

    monkeypatch.setattr(provider, 'stream', stream)
    original_wrapper = decorators._wrap_generator

    def wrapper(*args):
        gen = original_wrapper(*args)
        retained.append(gen)
        return gen

    monkeypatch.setattr(decorators, '_wrap_generator', wrapper)
    monkeypatch.setattr('generation.views.resolve_active_provider', lambda user: SimpleNamespace(name='gemini', instance=provider))
    user = make_user_with_key()
    api_client.force_authenticate(user=user)
    response = api_client.post('/api/generate/stream/', {
        'characterId': str(BaseCardFactory(owner=user).id),
        'sceneInput': {'location': 'synthetic place', 'intent': 'synthetic task'},
    }, format='json')
    assert response.status_code == 200
    events = iter(response.streaming_content)
    assert json.loads(next(events).decode().removeprefix('data: '))['type'] == 'chunk'
    if not cancel:
        done = json.loads(next(events).decode().removeprefix('data: '))
        assert done['type'] == 'done'
        assert LlmCallLog.objects.filter(generation_id=done['generationId'], status='success').count() == 1
        assert state.closes == state.clients  # closed before done is delivered
    response.close()
    assert state.closes == state.clients
    assert state.iterators[0].close_calls == 1
    assert all(client.is_closed for client in state.http_clients)
    assert all(response.is_closed for response in state.responses)
    assert all(gen.gi_frame is None for gen in retained)
    log = LlmCallLog.objects.get(feature='character_generate', user=user)
    assert log.status == ('error' if cancel else 'success')


@pytest.mark.parametrize('method', ['complete', 'stream'])
def test_native_continuation_cannot_add_hidden_wire_requests(monkeypatch, method):
    continuation = {
        'candidates': [{'content': {'parts': [{'text': 'first'}]},
                        'finishReason': 'CONTINUATION', 'continuationToken': 'dGVzdA=='}],
    }
    calls = []

    def handler(request):
        payload = continuation if not calls else SUCCESS
        calls.append(request)
        if method == 'stream':
            return httpx2.Response(200, request=request, headers={'content-type': 'text/event-stream'},
                                   content=('data: ' + json.dumps(payload) + '\n\n').encode())
        return httpx2.Response(200, request=request, json=payload)

    state = native_client(monkeypatch, httpx2, handler)
    if method == 'complete':
        assert GeminiProvider('dummy-key').complete('system', 'user', options=OPTIONS).text == 'first'
    else:
        emitted = list(GeminiProvider('dummy-key').stream('system', 'user'))
        assert [item for item in emitted if isinstance(item, str)] == ['first']
    assert len(state.requests) == 1


@pytest.mark.parametrize('library', [httpx2, httpx], ids=['httpx2', 'httpx'])
def test_failed_stream_is_released_before_sleep_and_recovery(monkeypatch, library):
    def handler(request):
        if len(state.requests) == 1:
            return library.Response(503, request=request, json={'error': {'code': 503, 'message': MARKER}})
        return sse_response(library, request)

    state = native_client(monkeypatch, library, handler)
    sleeps = []

    def sleep(wait):
        assert state.iterators[0].close_calls == 1
        assert state.responses[0].is_closed
        sleeps.append(wait)

    monkeypatch.setattr('generation.providers.gemini.time.sleep', sleep)
    assert list(GeminiProvider('dummy-key').stream('system', 'user')) == ['result', UsageInfo('gemini-2.5-flash', 10, 5)]
    assert len(state.requests) == 2
    assert sleeps == [1]
    assert state.closes == state.clients
    assert all(iterator.close_calls == 1 for iterator in state.iterators)


@pytest.mark.parametrize('fails', [False, True])
def test_iterator_close_failure_does_not_mask_stream_result_or_error(monkeypatch, caplog, fails):
    state = native_client(monkeypatch, httpx, lambda request: sse_response(
        httpx, request, mid_error=httpx.ReadTimeout if fails else None,
    ), iterator_close_error=True)
    if fails:
        with pytest.raises(ProviderError) as caught:
            list(GeminiProvider('dummy-key').stream('system', 'user'))
        assert caught.value.code == 'provider_timeout'
    else:
        assert list(GeminiProvider('dummy-key').stream('system', 'user')) == ['result', UsageInfo('gemini-2.5-flash', 10, 5)]
    assert state.closes == state.clients
    assert all(response.is_closed for response in state.responses)
    assert MARKER not in caplog.text


def test_embedding_failure_closes_client_without_adding_retries(monkeypatch):
    def handler(request):
        raise httpx2.ReadTimeout(MARKER, request=request)

    state = native_client(monkeypatch, httpx2, handler)
    with pytest.raises(httpx2.ReadTimeout):
        get_embedding('synthetic tags', 'dummy-key')
    assert len(state.requests) == 1
    assert state.closes == state.clients
    assert all(client.is_closed for client in state.http_clients)
