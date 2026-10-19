from types import SimpleNamespace

import pytest

from characters.models import BaseCard
from generation.providers.base import ProviderError
from logs.models import LlmCallLog
from tests.fakes import FakeProvider


SUGGESTIONS_URL = '/api/characters/suggest-completions/'


@pytest.mark.django_db
def test_character_suggestions_require_authentication_without_side_effects(
    api_client, monkeypatch,
):
    def fail_if_resolved(_user):
        raise AssertionError('provider must not be resolved before authentication')

    monkeypatch.setattr(
        'characters.suggest.resolve_active_provider',
        fail_if_resolved,
    )

    response = api_client.post(
        SUGGESTIONS_URL,
        {'characterData': {'name': '测试角色'}},
        format='json',
    )

    assert response.status_code == 401
    assert response.data['code'] == 'unauthorized'
    assert not BaseCard.objects.exists()
    assert not LlmCallLog.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    'character_data',
    [
        {},
        {'name': '   '},
    ],
)
def test_character_suggestions_missing_name_uses_standard_error_contract(
    api_client, user_factory, monkeypatch, character_data,
):
    def fail_if_resolved(_user):
        raise AssertionError('provider must not be resolved without a character name')

    monkeypatch.setattr(
        'characters.suggest.resolve_active_provider',
        fail_if_resolved,
    )
    user = user_factory()
    api_client.force_authenticate(user=user)

    response = api_client.post(
        SUGGESTIONS_URL,
        {'characterData': character_data},
        format='json',
    )

    assert response.status_code == 400
    assert response.data == {
        'code': 'missing_required_fields',
        'detail': '请先填写角色名',
    }
    assert not BaseCard.objects.exists()
    assert not LlmCallLog.objects.exists()


@pytest.mark.django_db
def test_character_suggestions_empty_result_uses_standard_error_contract(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider(complete_text='')
    monkeypatch.setattr(
        'characters.suggest.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    api_client.force_authenticate(user=user)

    response = api_client.post(
        SUGGESTIONS_URL,
        {'characterData': {'name': '测试角色'}},
        format='json',
    )

    assert response.status_code == 500
    assert response.data == {
        'code': 'suggestion_result_invalid',
        'detail': '推断返回为空，请重试',
    }
    assert not BaseCard.objects.exists()
    log = LlmCallLog.objects.get(feature='character_suggest')
    assert log.status == LlmCallLog.Status.SUCCESS


@pytest.mark.django_db
def test_character_suggestions_success_is_preview_only(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider(
        complete_text=(
            '{"suggestions": [{"field": "coreValues", "type": "list_append", '
            '"items": ["勇气"], "reasoning": "根据现有行为推断"}]}'
        ),
    )
    monkeypatch.setattr(
        'characters.suggest.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    api_client.force_authenticate(user=user)

    response = api_client.post(
        SUGGESTIONS_URL,
        {'characterData': {'name': '测试角色'}},
        format='json',
    )

    assert response.status_code == 200
    assert response.data == {
        'suggestions': [
            {
                'field': 'coreValues',
                'type': 'list_append',
                'items': ['勇气'],
                'reasoning': '根据现有行为推断',
            },
        ],
    }
    assert not BaseCard.objects.exists()
    assert LlmCallLog.objects.get(feature='character_suggest').status == (
        LlmCallLog.Status.SUCCESS
    )


@pytest.mark.django_db
def test_character_suggestions_provider_error_keeps_global_safe_contract(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider(error=ProviderError(
        'safe provider message',
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
        SUGGESTIONS_URL,
        {'characterData': {'name': '测试角色'}},
        format='json',
    )

    assert response.status_code == 503
    assert response.data == {
        'code': 'provider_temporarily_unavailable',
        'detail': 'safe provider message',
    }
    assert not BaseCard.objects.exists()
    log = LlmCallLog.objects.get(feature='character_suggest')
    assert log.status == LlmCallLog.Status.ERROR
    assert log.error_message == 'provider_temporarily_unavailable'


@pytest.mark.django_db
def test_character_suggestions_unexpected_exception_keeps_generation_error(
    api_client, make_user_with_key, monkeypatch,
):
    vendor_marker = 'vendor-private-suggestion-response'
    provider = FakeProvider(error=RuntimeError(vendor_marker))
    monkeypatch.setattr(
        'characters.suggest.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    api_client.force_authenticate(user=user)

    response = api_client.post(
        SUGGESTIONS_URL,
        {'characterData': {'name': '测试角色'}},
        format='json',
    )

    assert response.status_code == 500
    assert response.data == {
        'code': 'generation_failed',
        'detail': '角色建议调用失败，请稍后重试',
    }
    assert vendor_marker not in str(response.data)
    assert not BaseCard.objects.exists()
    log = LlmCallLog.objects.get(feature='character_suggest')
    assert log.status == LlmCallLog.Status.ERROR
    assert log.error_message == 'generation_failed'
