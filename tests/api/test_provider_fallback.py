"""PROV-003 request-scoped provider override contracts.

The override is deliberately narrow: it resolves only one enabled provider
whose saved key belongs to the request user, and it never changes the user's
persisted active provider.
"""

import json
from types import SimpleNamespace

import pytest

from examples.models import Article
from generation.providers.base import ProviderError
from tests.factories import BaseCardFactory, UserFactory
from tests.fakes import FakeProvider
from users.encryption import encrypt_key
from users.models import UserLLMConfig, UserProviderKey


def _parse_sse(response):
    body = b''.join(response.streaming_content).decode('utf-8')
    return [
        json.loads(line[len('data:'):].strip())
        for line in body.splitlines()
        if line.strip().startswith('data:')
    ]


@pytest.mark.django_db
def test_override_resolves_only_the_request_users_saved_key_without_switching(
    make_user_with_key, monkeypatch,
):
    from generation.provider_service import resolve_provider

    user = make_user_with_key(provider='gemini')
    UserProviderKey.objects.create(
        user=user,
        provider='groq',
        api_key_encrypted=encrypt_key('owned-groq-key'),
    )
    calls = []

    monkeypatch.setattr(
        'generation.provider_service.get_provider',
        lambda provider, api_key: calls.append((provider, api_key)) or FakeProvider(),
    )

    resolved = resolve_provider(user, 'groq')

    assert resolved.name == 'groq'
    assert calls == [('groq', 'owned-groq-key')]
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'


@pytest.mark.django_db
def test_override_cannot_use_another_users_saved_key(make_user_with_key):
    from generation.provider_service import resolve_provider

    user = make_user_with_key(provider='gemini')
    other = UserFactory()
    UserProviderKey.objects.create(
        user=other,
        provider='groq',
        api_key_encrypted=encrypt_key('other-users-key'),
    )

    with pytest.raises(ProviderError) as exc_info:
        resolve_provider(user, 'groq')

    assert exc_info.value.code == 'no_api_key'
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'


@pytest.mark.django_db
@pytest.mark.parametrize('provider', ['anthropic', 'cerebras', 'openrouter'])
def test_override_rejects_disabled_provider_before_key_decryption(
    make_user_with_key, monkeypatch, provider,
):
    from generation.provider_service import resolve_provider

    user = make_user_with_key(provider='gemini')
    UserProviderKey.objects.create(
        user=user,
        provider=provider,
        api_key_encrypted=encrypt_key(f'owned-{provider}-key'),
    )

    def unexpected_decryption(_encrypted):
        raise AssertionError('disabled override must fail before key decryption')

    monkeypatch.setattr(
        'generation.provider_service.decrypt_key', unexpected_decryption,
    )

    with pytest.raises(ProviderError) as exc_info:
        resolve_provider(user, provider)

    assert exc_info.value.code == 'provider_disabled'
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'


@pytest.mark.django_db(transaction=True)
def test_segment_request_uses_override_without_switching_active_provider(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    article = Article.objects.create(
        owner=user,
        character=character,
        title='provider override',
        content='synthetic line',
    )
    provider = FakeProvider(complete_text=json.dumps({
        'segments': [{'start': 0, 'end': 0, 'type': 'story'}],
    }))
    calls = []

    monkeypatch.setattr(
        'examples.views.resolve_provider',
        lambda request_user, override: calls.append((request_user.id, override))
        or SimpleNamespace(name=override, instance=provider),
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        f'/api/examples/articles/{article.id}/segment/',
        {'providerOverride': 'groq'},
        format='json',
    )

    assert response.status_code == 200
    assert response.json()['count'] == 1
    assert calls == [(user.id, 'groq')]
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'


@pytest.mark.django_db
def test_stream_request_uses_override_without_switching_or_continuing_state(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    character = BaseCardFactory(owner=user)
    calls = []

    monkeypatch.setattr(
        'generation.views.resolve_provider',
        lambda request_user, override: calls.append((request_user.id, override))
        or SimpleNamespace(
            name=override,
            instance=FakeProvider(chunks=['fresh', ' result']),
        ),
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        '/api/generate/stream/',
        {
            'characterId': str(character.id),
            'sceneInput': {'location': 'cafe', 'intent': 'meet'},
            'providerOverride': 'groq',
        },
        format='json',
    )
    events = _parse_sse(response)

    assert calls == [(user.id, 'groq')]
    assert [event['content'] for event in events if event['type'] == 'chunk'] == [
        'fresh', ' result',
    ]
    assert events[-1]['type'] == 'done'
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'
