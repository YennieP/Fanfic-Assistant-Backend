from types import SimpleNamespace

import anthropic as anthropic_sdk
import groq as groq_sdk
import httpx
import openai
import pytest
import requests
from google.genai.errors import ClientError

from generation.providers.anthropic import AnthropicProvider
from generation.providers.base import ProviderError
from generation.providers.cerebras import CerebrasProvider
from generation.providers.gemini import GeminiProvider
from generation.providers.groq import GroqProvider
from generation.providers.openrouter import OpenRouterProvider


VENDOR_MARKER = 'vendor-user-123 request-secret-456'


class _FakeCompletions:
    def __init__(self, *, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def _chat_client(completions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _completion_result(model='resolved-model'):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
    )


def _status_error(error_type, status):
    request = httpx.Request('POST', 'https://provider.invalid/v1/chat/completions')
    response = httpx.Response(status, request=request)
    return error_type(
        VENDOR_MARKER,
        response=response,
        body={'error': {'message': VENDOR_MARKER, 'user': 'vendor-user-123'}},
    )


def _gemini_client_error(status):
    response = requests.Response()
    response.status_code = status
    response.reason = 'vendor status'
    response._content = (
        '{"error":{"message":"' + VENDOR_MARKER + '"}}'
    ).encode()
    return ClientError(status, response)


def test_recovery_models_use_official_replacements():
    assert AnthropicProvider.MODEL == 'claude-sonnet-4-6'
    assert GroqProvider.MODEL == 'openai/gpt-oss-120b'
    assert CerebrasProvider.MODEL == 'gpt-oss-120b'


def test_groq_gpt_oss_complete_requests_json_with_low_reasoning(monkeypatch):
    completions = _FakeCompletions(result=_completion_result())
    monkeypatch.setattr(
        'generation.providers.groq.groq_sdk.Groq',
        lambda api_key: _chat_client(completions),
    )

    result = GroqProvider('key').complete('system', 'user', max_tokens=321)

    assert result.text == '{"ok":true}'
    assert completions.calls == [{
        'model': 'openai/gpt-oss-120b',
        'messages': [
            {'role': 'system', 'content': 'system'},
            {'role': 'user', 'content': 'user'},
        ],
        'max_tokens': 321,
        'reasoning_effort': 'low',
        'response_format': {'type': 'json_object'},
    }]


def test_cerebras_gpt_oss_complete_requests_json_with_low_reasoning():
    completions = _FakeCompletions(result=_completion_result())
    provider = object.__new__(CerebrasProvider)
    provider.client = _chat_client(completions)

    result = provider.complete('system', 'user', max_tokens=654)

    assert result.text == '{"ok":true}'
    assert completions.calls == [{
        'model': 'gpt-oss-120b',
        'messages': [
            {'role': 'system', 'content': 'system'},
            {'role': 'user', 'content': 'user'},
        ],
        'max_tokens': 654,
        'reasoning_effort': 'low',
        'response_format': {'type': 'json_object'},
    }]


def test_groq_gpt_oss_stream_uses_low_reasoning(monkeypatch):
    completions = _FakeCompletions(result=iter(()))
    monkeypatch.setattr(
        'generation.providers.groq.groq_sdk.Groq',
        lambda api_key: _chat_client(completions),
    )

    list(GroqProvider('key').stream('system', 'user'))

    assert completions.calls[0]['model'] == 'openai/gpt-oss-120b'
    assert completions.calls[0]['max_tokens'] == 4000
    assert completions.calls[0]['reasoning_effort'] == 'low'
    assert completions.calls[0]['stream'] is True


def test_cerebras_gpt_oss_stream_uses_low_reasoning():
    completions = _FakeCompletions(result=iter(()))
    provider = _provider_with_client(
        CerebrasProvider, _chat_client(completions),
    )

    list(provider.stream('system', 'user'))

    assert completions.calls[0]['model'] == 'gpt-oss-120b'
    assert completions.calls[0]['max_tokens'] == 4000
    assert completions.calls[0]['reasoning_effort'] == 'low'
    assert completions.calls[0]['stream'] is True


@pytest.mark.parametrize(
    ('provider_factory', 'error'),
    [
        (
            lambda monkeypatch, error: (
                monkeypatch.setattr(
                    'generation.providers.groq.groq_sdk.Groq',
                    lambda api_key: _chat_client(_FakeCompletions(error=error)),
                ),
                GroqProvider('key'),
            )[1],
            lambda: _status_error(groq_sdk.APIStatusError, 404),
        ),
        (
            lambda _monkeypatch, error: _provider_with_client(
                CerebrasProvider, _chat_client(_FakeCompletions(error=error)),
            ),
            lambda: _status_error(openai.APIStatusError, 404),
        ),
        (
            lambda _monkeypatch, error: _provider_with_client(
                OpenRouterProvider, _chat_client(_FakeCompletions(error=error)),
            ),
            lambda: _status_error(openai.APIStatusError, 404),
        ),
    ],
)
def test_openai_compatible_404_is_safe_model_unavailable(
    monkeypatch, provider_factory, error,
):
    provider = provider_factory(monkeypatch, error())

    with pytest.raises(ProviderError) as exc_info:
        provider.complete('system', 'user')

    assert exc_info.value.code == 'provider_model_unavailable'
    assert exc_info.value.http_status == 503
    assert VENDOR_MARKER not in str(exc_info.value)


def test_cerebras_logs_only_safe_status_metadata(caplog):
    error = _status_error(openai.APIStatusError, 422)
    provider = _provider_with_client(
        CerebrasProvider,
        _chat_client(_FakeCompletions(error=error)),
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete('system', 'user')

    assert exc_info.value.code == 'provider_temporarily_unavailable'
    assert 'status=422' in caplog.text
    assert 'model=gpt-oss-120b' in caplog.text
    assert 'operation=complete' in caplog.text
    assert VENDOR_MARKER not in caplog.text


@pytest.mark.parametrize('method', ['complete', 'stream'])
def test_cerebras_402_is_safe_payment_required(method):
    error = _status_error(openai.APIStatusError, 402)
    provider = _provider_with_client(
        CerebrasProvider,
        _chat_client(_FakeCompletions(error=error)),
    )

    with pytest.raises(ProviderError) as exc_info:
        if method == 'complete':
            provider.complete('system', 'user')
        else:
            list(provider.stream('system', 'user'))

    assert exc_info.value.code == 'provider_payment_required'
    assert exc_info.value.http_status == 402
    assert VENDOR_MARKER not in str(exc_info.value)


def _provider_with_client(provider_type, client):
    provider = object.__new__(provider_type)
    provider.client = client
    return provider


def test_anthropic_404_is_safe_model_unavailable(monkeypatch):
    messages = _FakeCompletions(
        error=_status_error(anthropic_sdk.APIStatusError, 404),
    )
    monkeypatch.setattr(
        'generation.providers.anthropic.anthropic_sdk.Anthropic',
        lambda api_key: SimpleNamespace(messages=messages),
    )

    with pytest.raises(ProviderError) as exc_info:
        AnthropicProvider('key').complete('system', 'user')

    assert exc_info.value.code == 'provider_model_unavailable'
    assert exc_info.value.http_status == 503
    assert VENDOR_MARKER not in str(exc_info.value)


@pytest.mark.parametrize(
    ('error_type', 'status', 'expected_code', 'expected_status'),
    [
        (groq_sdk.AuthenticationError, 401, 'provider_key_invalid', 400),
        (groq_sdk.RateLimitError, 429, 'provider_rate_limit', 429),
        (groq_sdk.APIStatusError, 400, 'provider_temporarily_unavailable', 503),
    ],
)
def test_groq_status_errors_are_safe(
    monkeypatch, error_type, status, expected_code, expected_status,
):
    error = _status_error(error_type, status)
    completions = _FakeCompletions(error=error)
    monkeypatch.setattr(
        'generation.providers.groq.groq_sdk.Groq',
        lambda api_key: _chat_client(completions),
    )

    with pytest.raises(ProviderError) as exc_info:
        GroqProvider('key').complete('system', 'user')

    assert exc_info.value.code == expected_code
    assert exc_info.value.http_status == expected_status
    assert VENDOR_MARKER not in str(exc_info.value)
    assert len(completions.calls) == 1


@pytest.mark.parametrize('method', ['complete', 'stream'])
def test_gemini_429_is_safe_rate_limit(monkeypatch, method):
    error = _gemini_client_error(429)

    class _Models:
        def generate_content(self, **kwargs):
            raise error

        def generate_content_stream(self, **kwargs):
            raise error

    monkeypatch.setattr(
        'generation.providers.gemini.genai.Client',
        lambda api_key: SimpleNamespace(models=_Models()),
    )

    provider = GeminiProvider('key')
    with pytest.raises(ProviderError) as exc_info:
        if method == 'complete':
            provider.complete('system', 'user')
        else:
            list(provider.stream('system', 'user'))

    assert exc_info.value.code == 'provider_rate_limit'
    assert exc_info.value.http_status == 429
    assert VENDOR_MARKER not in str(exc_info.value)


def test_retryable_status_retries_once_without_logging_vendor_body(
    monkeypatch, caplog,
):
    error = _status_error(groq_sdk.APIStatusError, 503)
    completions = _FakeCompletions(error=error)
    monkeypatch.setattr(
        'generation.providers.groq.groq_sdk.Groq',
        lambda api_key: _chat_client(completions),
    )

    with pytest.raises(ProviderError) as exc_info:
        GroqProvider('key').complete('system', 'user')

    assert exc_info.value.code == 'provider_temporarily_unavailable'
    assert len(completions.calls) == 2
    assert VENDOR_MARKER not in caplog.text


def test_connection_error_retries_once_then_becomes_safe(monkeypatch, caplog):
    request = httpx.Request('POST', 'https://provider.invalid/v1/chat/completions')
    error = groq_sdk.APIConnectionError(
        message=VENDOR_MARKER,
        request=request,
    )
    completions = _FakeCompletions(error=error)
    monkeypatch.setattr(
        'generation.providers.groq.groq_sdk.Groq',
        lambda api_key: _chat_client(completions),
    )

    with pytest.raises(ProviderError) as exc_info:
        GroqProvider('key').complete('system', 'user')

    assert exc_info.value.code == 'provider_connection_failed'
    assert len(completions.calls) == 2
    assert VENDOR_MARKER not in caplog.text


def test_provider_error_handler_preserves_safe_code_and_http_status():
    from core.exceptions import custom_exception_handler

    response = custom_exception_handler(
        ProviderError(
            '当前模型不可用',
            code='provider_model_unavailable',
            http_status=503,
        ),
        {'view': object()},
    )

    assert response.status_code == 503
    assert response.data == {
        'code': 'provider_model_unavailable',
        'detail': '当前模型不可用',
    }


@pytest.mark.django_db
def test_llm_log_does_not_store_unexpected_exception_body():
    from logs.decorators import log_llm_call
    from logs.models import LlmCallLog

    @log_llm_call(feature='provider_recovery_test', sync=True)
    def fail(generation_id=None):
        raise RuntimeError(VENDOR_MARKER)

    with pytest.raises(RuntimeError, match=VENDOR_MARKER):
        fail()

    log = LlmCallLog.objects.get(feature='provider_recovery_test')
    assert log.status == 'error'
    assert log.error_message == 'generation_failed'
    assert VENDOR_MARKER not in log.error_message
