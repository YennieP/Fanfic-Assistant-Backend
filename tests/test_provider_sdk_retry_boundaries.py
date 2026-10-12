import time

import anthropic as anthropic_sdk
import groq as groq_sdk
import httpx
import httpx2
import openai
import pytest

from generation.providers.anthropic import AnthropicProvider
from generation.providers.base import CompletionOptions, ProviderError
from generation.providers.cerebras import CerebrasProvider
from generation.providers.groq import GroqProvider
from generation.providers.openrouter import OpenRouterProvider


JSON_OPTIONS = CompletionOptions(
    response_format='json',
    reasoning_effort='low',
)
PROVIDER_TYPES = {
    'anthropic': AnthropicProvider,
    'groq': GroqProvider,
    'cerebras': CerebrasProvider,
    'openrouter': OpenRouterProvider,
}


def _sdk_error_body(provider_name, status):
    if provider_name == 'anthropic':
        error_type = 'rate_limit_error' if status == 429 else 'api_error'
        return {
            'type': 'error',
            'error': {'type': error_type, 'message': 'safe test failure'},
        }
    return {
        'error': {
            'message': 'safe test failure',
            'type': 'rate_limit_error' if status == 429 else 'server_error',
            'code': 'rate_limit_exceeded' if status == 429 else 'server_error',
        },
    }


def _provider_with_sdk_transport(
    monkeypatch,
    provider_name,
    *,
    status=None,
    connection_error=False,
):
    calls = []

    if provider_name in {'cerebras', 'openrouter'}:
        transport_module = httpx2
        real_client = openai.OpenAI

        def handler(request):
            calls.append(request)
            if connection_error:
                raise transport_module.ConnectError(
                    'safe test connection failure', request=request,
                )
            return transport_module.Response(
                status,
                request=request,
                json=_sdk_error_body(provider_name, status),
            )

        transport = transport_module.MockTransport(handler)

        def client_factory(**kwargs):
            return real_client(
                **kwargs,
                http_client=openai.DefaultHttpxClient(transport=transport),
            )

        monkeypatch.setattr(
            'generation.providers.openai_compatible.OpenAI',
            client_factory,
        )
    elif provider_name == 'groq':
        real_client = groq_sdk.Groq

        def handler(request):
            calls.append(request)
            if connection_error:
                raise httpx.ConnectError(
                    'safe test connection failure', request=request,
                )
            return httpx.Response(
                status,
                request=request,
                json=_sdk_error_body(provider_name, status),
            )

        transport = httpx.MockTransport(handler)

        def client_factory(**kwargs):
            return real_client(
                **kwargs,
                http_client=groq_sdk.DefaultHttpxClient(transport=transport),
            )

        monkeypatch.setattr(
            'generation.providers.groq.groq_sdk.Groq',
            client_factory,
        )
    else:
        real_client = anthropic_sdk.Anthropic

        def handler(request):
            calls.append(request)
            if connection_error:
                raise httpx.ConnectError(
                    'safe test connection failure', request=request,
                )
            return httpx.Response(
                status,
                request=request,
                json=_sdk_error_body(provider_name, status),
            )

        transport = httpx.MockTransport(handler)

        def client_factory(**kwargs):
            return real_client(
                **kwargs,
                http_client=anthropic_sdk.DefaultHttpxClient(transport=transport),
            )

        monkeypatch.setattr(
            'generation.providers.anthropic.anthropic_sdk.Anthropic',
            client_factory,
        )

    return PROVIDER_TYPES[provider_name]('test-key'), calls


@pytest.mark.parametrize('provider_name', PROVIDER_TYPES)
def test_sdk_does_not_hide_rate_limit_retries(monkeypatch, provider_name):
    monkeypatch.setattr(time, 'sleep', lambda _wait: None)
    provider, calls = _provider_with_sdk_transport(
        monkeypatch,
        provider_name,
        status=429,
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete('system', 'user', options=JSON_OPTIONS)

    assert exc_info.value.code == 'provider_rate_limit'
    assert len(calls) == 1


@pytest.mark.parametrize('provider_name', PROVIDER_TYPES)
def test_project_retryable_status_is_two_wire_requests(
    monkeypatch,
    provider_name,
):
    monkeypatch.setattr(time, 'sleep', lambda _wait: None)
    status = 500 if provider_name == 'anthropic' else 503
    provider, calls = _provider_with_sdk_transport(
        monkeypatch,
        provider_name,
        status=status,
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete('system', 'user', options=JSON_OPTIONS)

    assert exc_info.value.code == 'provider_temporarily_unavailable'
    assert len(calls) == 2


@pytest.mark.parametrize('provider_name', PROVIDER_TYPES)
def test_project_connection_retry_is_two_wire_requests(
    monkeypatch,
    provider_name,
):
    monkeypatch.setattr(time, 'sleep', lambda _wait: None)
    provider, calls = _provider_with_sdk_transport(
        monkeypatch,
        provider_name,
        connection_error=True,
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.complete('system', 'user', options=JSON_OPTIONS)

    assert exc_info.value.code == 'provider_connection_failed'
    assert len(calls) == 2
