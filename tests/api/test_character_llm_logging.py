from types import SimpleNamespace

import pytest

from generation.providers.base import ProviderError
from logs.models import LlmCallLog
from tests.factories import BaseCardFactory
from tests.fakes import FakeProvider


@pytest.mark.django_db
def test_character_suggestion_uses_explicit_json_options_and_writes_log(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider(complete_text='{"suggestions": []}')
    monkeypatch.setattr(
        'characters.suggest.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    api_client.force_authenticate(user=user)

    response = api_client.post(
        '/api/characters/suggest-completions/',
        data={'characterData': {'name': '测试角色'}},
        format='json',
    )

    assert response.status_code == 200
    assert provider.complete_calls[0].options.response_format == 'json'
    assert provider.complete_calls[0].options.reasoning_effort == 'low'
    log = LlmCallLog.objects.get(feature='character_suggest')
    assert log.status == LlmCallLog.Status.SUCCESS
    assert log.model == 'fake'


@pytest.mark.django_db
def test_character_suggestion_failure_writes_only_safe_error_code(
    api_client, make_user_with_key, monkeypatch,
):
    vendor_marker = 'vendor-private-response'
    provider = FakeProvider(error=ProviderError(
        vendor_marker,
        code='provider_temporarily_unavailable',
        http_status=503,
    ))
    monkeypatch.setattr(
        'characters.suggest.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    api_client.force_authenticate(user=user)

    response = api_client.post(
        '/api/characters/suggest-completions/',
        data={'characterData': {'name': '测试角色'}},
        format='json',
    )

    assert response.status_code == 503
    log = LlmCallLog.objects.get(feature='character_suggest')
    assert log.status == LlmCallLog.Status.ERROR
    assert log.error_message == 'provider_temporarily_unavailable'
    assert vendor_marker not in log.error_message


@pytest.mark.django_db
def test_character_translation_uses_explicit_json_options_and_writes_log(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider(complete_text='{"name": "Test Character"}')
    monkeypatch.setattr(
        'characters.translate.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    character = BaseCardFactory(owner=user, name='测试角色', language='zh')
    api_client.force_authenticate(user=user)

    response = api_client.post(
        f'/api/characters/{character.canonical_id}/translate/',
        data={
            'sourceLang': 'zh',
            'targetLang': 'en',
            'fields': ['name'],
        },
        format='json',
    )

    assert response.status_code == 200
    assert response.data['translations']['name'] == 'Test Character'
    assert provider.complete_calls[0].options.response_format == 'json'
    assert provider.complete_calls[0].options.reasoning_effort == 'low'
    log = LlmCallLog.objects.get(feature='character_translate')
    assert log.status == LlmCallLog.Status.SUCCESS
    assert log.model == 'fake'
