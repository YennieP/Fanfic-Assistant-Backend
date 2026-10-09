from types import SimpleNamespace

import anthropic as anthropic_sdk
import groq as groq_sdk
import httpx
import openai
import pytest
import requests
from google.genai.errors import ClientError, ServerError

from generation.providers.anthropic import AnthropicProvider
from generation.providers.base import ProviderError
from generation.providers.cerebras import CerebrasProvider
from generation.providers.gemini import GeminiProvider
from generation.providers.groq import GroqProvider
from generation.providers.openrouter import OpenRouterProvider


VENDOR_MARKER = 'vendor-user-123 request-secret-456'
PROVIDER_TYPES = {
    'anthropic': AnthropicProvider,
    'gemini': GeminiProvider,
    'groq': GroqProvider,
    'cerebras': CerebrasProvider,
    'openrouter': OpenRouterProvider,
}


def _retryable_status(provider_name):
    return 500 if provider_name == 'anthropic' else 503


class _FailingCall:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        raise self.error


def _response(status):
    response = requests.Response()
    response.status_code = status
    response.reason = 'vendor status'
    response._content = (
        '{"error":{"message":"' + VENDOR_MARKER + '"}}'
    ).encode()
    return response


def _sdk_status_error(error_type, status):
    request = httpx.Request(
        'POST', 'https://provider.invalid/v1/chat/completions',
    )
    response = httpx.Response(status, request=request)
    return error_type(
        VENDOR_MARKER,
        response=response,
        body={'error': {'message': VENDOR_MARKER}},
    )


def _http_error(provider_name, status):
    if provider_name == 'gemini':
        error_type = ServerError if status >= 500 else ClientError
        return error_type(status, _response(status))

    if provider_name == 'anthropic':
        error_type = {
            401: anthropic_sdk.AuthenticationError,
            429: anthropic_sdk.RateLimitError,
        }.get(status, anthropic_sdk.APIStatusError)
    elif provider_name == 'groq':
        error_type = {
            401: groq_sdk.AuthenticationError,
            429: groq_sdk.RateLimitError,
        }.get(status, groq_sdk.APIStatusError)
    else:
        error_type = {
            401: openai.AuthenticationError,
            429: openai.RateLimitError,
        }.get(status, openai.APIStatusError)
    return _sdk_status_error(error_type, status)


def _transport_error(provider_name, kind):
    if provider_name == 'gemini':
        error_type = requests.Timeout if kind == 'timeout' else requests.ConnectionError
        return error_type(VENDOR_MARKER)

    request = httpx.Request(
        'POST', 'https://provider.invalid/v1/chat/completions',
    )
    sdk = {
        'anthropic': anthropic_sdk,
        'groq': groq_sdk,
        'cerebras': openai,
        'openrouter': openai,
    }[provider_name]
    if kind == 'timeout':
        return sdk.APITimeoutError(request=request)
    return sdk.APIConnectionError(message=VENDOR_MARKER, request=request)


def _provider_with_failing_complete(monkeypatch, provider_name, error):
    failing_call = _FailingCall(error)
    if provider_name == 'anthropic':
        monkeypatch.setattr(
            'generation.providers.anthropic.anthropic_sdk.Anthropic',
            lambda api_key: SimpleNamespace(messages=failing_call),
        )
        provider = AnthropicProvider('key')
    elif provider_name == 'gemini':
        models = SimpleNamespace(generate_content=failing_call.create)
        monkeypatch.setattr(
            'generation.providers.gemini.genai.Client',
            lambda api_key: SimpleNamespace(models=models),
        )
        provider = GeminiProvider('key')
    elif provider_name == 'groq':
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=failing_call),
        )
        monkeypatch.setattr(
            'generation.providers.groq.groq_sdk.Groq',
            lambda api_key: client,
        )
        provider = GroqProvider('key')
    else:
        provider = object.__new__(PROVIDER_TYPES[provider_name])
        provider.client = SimpleNamespace(
            chat=SimpleNamespace(completions=failing_call),
        )
    return provider, failing_call


def _provider_with_failing_stream(monkeypatch, provider_name, error):
    failing_call = _FailingCall(error)
    if provider_name == 'anthropic':
        failing_call.stream = failing_call.create
        monkeypatch.setattr(
            'generation.providers.anthropic.anthropic_sdk.Anthropic',
            lambda api_key: SimpleNamespace(messages=failing_call),
        )
        provider = AnthropicProvider('key')
    elif provider_name == 'gemini':
        models = SimpleNamespace(generate_content_stream=failing_call.create)
        monkeypatch.setattr(
            'generation.providers.gemini.genai.Client',
            lambda api_key: SimpleNamespace(models=models),
        )
        provider = GeminiProvider('key')
    elif provider_name == 'groq':
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=failing_call),
        )
        monkeypatch.setattr(
            'generation.providers.groq.groq_sdk.Groq',
            lambda api_key: client,
        )
        provider = GroqProvider('key')
    else:
        provider = object.__new__(PROVIDER_TYPES[provider_name])
        provider.client = SimpleNamespace(
            chat=SimpleNamespace(completions=failing_call),
        )
    return provider, failing_call


@pytest.mark.parametrize('provider_name', PROVIDER_TYPES)
@pytest.mark.parametrize(
    ('status', 'expected_code', 'expected_http_status'),
    [
        (401, 'provider_key_invalid', 400),
        (402, 'provider_payment_required', 402),
        (404, 'provider_model_unavailable', 503),
        (429, 'provider_rate_limit', 429),
        (422, 'provider_temporarily_unavailable', 503),
        (503, 'provider_temporarily_unavailable', 503),
    ],
)
def test_complete_http_error_matrix_is_safe(
    monkeypatch,
    caplog,
    provider_name,
    status,
    expected_code,
    expected_http_status,
):
    monkeypatch.setattr('time.sleep', lambda _wait: None)
    provider, failing_call = _provider_with_failing_complete(
        monkeypatch,
        provider_name,
        _http_error(provider_name, status),
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete('system', 'user')

    assert exc_info.value.code == expected_code
    assert exc_info.value.http_status == expected_http_status
    is_retryable = status == _retryable_status(provider_name)
    expected_calls = 5 if provider_name == 'gemini' and is_retryable else 2
    if status < 500:
        expected_calls = 1
    elif not is_retryable:
        expected_calls = 1
    assert failing_call.calls == expected_calls
    assert VENDOR_MARKER not in str(exc_info.value)
    assert VENDOR_MARKER not in caplog.text


@pytest.mark.parametrize('provider_name', PROVIDER_TYPES)
@pytest.mark.parametrize(
    ('kind', 'expected_code'),
    [
        ('timeout', 'provider_timeout'),
        ('connection', 'provider_connection_failed'),
    ],
)
def test_complete_transport_error_matrix_is_safe(
    monkeypatch, caplog, provider_name, kind, expected_code,
):
    monkeypatch.setattr('time.sleep', lambda _wait: None)
    provider, failing_call = _provider_with_failing_complete(
        monkeypatch,
        provider_name,
        _transport_error(provider_name, kind),
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete('system', 'user')

    assert exc_info.value.code == expected_code
    assert exc_info.value.http_status == 503
    assert failing_call.calls == (5 if provider_name == 'gemini' else 2)
    assert VENDOR_MARKER not in str(exc_info.value)
    assert VENDOR_MARKER not in caplog.text


@pytest.mark.parametrize('provider_name', PROVIDER_TYPES)
def test_complete_retries_provider_specific_5xx(
    monkeypatch, caplog, provider_name,
):
    monkeypatch.setattr('time.sleep', lambda _wait: None)
    provider, failing_call = _provider_with_failing_complete(
        monkeypatch,
        provider_name,
        _http_error(provider_name, _retryable_status(provider_name)),
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete('system', 'user')

    assert exc_info.value.code == 'provider_temporarily_unavailable'
    assert exc_info.value.http_status == 503
    assert failing_call.calls == (5 if provider_name == 'gemini' else 2)
    assert VENDOR_MARKER not in str(exc_info.value)
    assert VENDOR_MARKER not in caplog.text


@pytest.mark.parametrize('provider_name', PROVIDER_TYPES)
def test_stream_retries_5xx_only_before_first_chunk(
    monkeypatch, caplog, provider_name,
):
    monkeypatch.setattr('time.sleep', lambda _wait: None)
    provider, failing_call = _provider_with_failing_stream(
        monkeypatch,
        provider_name,
        _http_error(provider_name, _retryable_status(provider_name)),
    )

    with pytest.raises(ProviderError) as exc_info:
        list(provider.stream('system', 'user'))

    assert exc_info.value.code == 'provider_temporarily_unavailable'
    assert exc_info.value.http_status == 503
    assert failing_call.calls == (7 if provider_name == 'gemini' else 2)
    assert VENDOR_MARKER not in str(exc_info.value)
    assert VENDOR_MARKER not in caplog.text
