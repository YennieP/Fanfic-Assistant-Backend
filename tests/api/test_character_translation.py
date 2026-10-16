from types import SimpleNamespace

import pytest

from characters.models import BaseCard
from generation.providers.base import ProviderError
from logs.models import LlmCallLog
from tests.factories import BaseCardFactory
from tests.fakes import FakeProvider


def _translation_url(canonical_id):
    return f'/api/characters/{canonical_id}/translate/'


@pytest.mark.django_db
def test_translation_requires_authentication(api_client):
    response = api_client.post(
        _translation_url('00000000-0000-0000-0000-000000000001'),
        data={'sourceLang': 'zh', 'targetLang': 'en', 'fields': ['name']},
        format='json',
    )

    assert response.status_code == 401
    assert response.data['code'] == 'unauthorized'
    assert not LlmCallLog.objects.exists()


@pytest.mark.django_db
def test_translation_missing_or_unowned_source_uses_standard_error_contract(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key()
    other_users_card = BaseCardFactory(language='zh')
    api_client.force_authenticate(user=user)

    def fail_if_resolved(_user):
        raise AssertionError('provider must not be resolved before source ownership')

    monkeypatch.setattr(
        'characters.translate.resolve_active_provider',
        fail_if_resolved,
    )

    response = api_client.post(
        _translation_url(other_users_card.canonical_id),
        data={'sourceLang': 'zh', 'targetLang': 'en', 'fields': ['name']},
        format='json',
    )

    assert response.status_code == 404
    assert response.data == {
        'code': 'character_not_found',
        'detail': '未找到 zh 版本的角色卡',
    }
    assert BaseCard.objects.count() == 1
    assert not LlmCallLog.objects.exists()


@pytest.mark.django_db
def test_translation_empty_result_uses_standard_error_contract_without_creating_card(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider(complete_text='')
    monkeypatch.setattr(
        'characters.translate.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    character = BaseCardFactory(owner=user, language='zh')
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _translation_url(character.canonical_id),
        data={'sourceLang': 'zh', 'targetLang': 'en', 'fields': ['name']},
        format='json',
    )

    assert response.status_code == 500
    assert response.data == {
        'code': 'translation_result_invalid',
        'detail': '翻译返回为空，请重试',
    }
    assert BaseCard.objects.count() == 1


@pytest.mark.django_db
def test_translation_non_object_result_uses_standard_error_contract_without_creating_card(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider(complete_text='[]')
    monkeypatch.setattr(
        'characters.translate.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    character = BaseCardFactory(owner=user, language='zh')
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _translation_url(character.canonical_id),
        data={'sourceLang': 'zh', 'targetLang': 'en', 'fields': ['name']},
        format='json',
    )

    assert response.status_code == 500
    assert response.data == {
        'code': 'translation_result_invalid',
        'detail': '翻译结果格式异常，请重试',
    }
    assert BaseCard.objects.count() == 1


@pytest.mark.django_db
def test_translation_empty_field_selection_keeps_success_shape_without_calling_provider(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider()
    monkeypatch.setattr(
        'characters.translate.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    character = BaseCardFactory(owner=user, language='zh')
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _translation_url(character.canonical_id),
        data={'sourceLang': 'zh', 'targetLang': 'en', 'fields': []},
        format='json',
    )

    assert response.status_code == 200
    assert response.data == {'translations': {}}
    assert provider.complete_calls == []
    assert BaseCard.objects.count() == 1
    assert not LlmCallLog.objects.exists()


@pytest.mark.django_db
def test_translation_success_is_preview_only_and_reattaches_existing_ids(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider(
        complete_text=(
            '{"coreValues": [{"content": "Courage"}], '
            '"unknownField": "ignored"}'
        ),
    )
    monkeypatch.setattr(
        'characters.translate.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    character = BaseCardFactory(
        owner=user,
        language='zh',
        core_values=[{'id': 'value-1', 'content': '勇气'}],
    )
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _translation_url(character.canonical_id),
        data={
            'sourceLang': 'zh',
            'targetLang': 'en',
            'fields': ['coreValues'],
        },
        format='json',
    )

    assert response.status_code == 200
    assert response.data == {
        'translations': {
            'coreValues': [{'id': 'value-1', 'content': 'Courage'}],
        },
    }
    assert BaseCard.objects.count() == 1
    assert provider.complete_calls[0].options.response_format == 'json'
    assert provider.complete_calls[0].options.reasoning_effort == 'low'


@pytest.mark.django_db
def test_translation_provider_error_keeps_global_safe_contract_and_no_card_write(
    api_client, make_user_with_key, monkeypatch,
):
    provider = FakeProvider(error=ProviderError(
        'safe provider message',
        code='provider_temporarily_unavailable',
        http_status=503,
    ))
    monkeypatch.setattr(
        'characters.translate.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    character = BaseCardFactory(owner=user, language='zh')
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _translation_url(character.canonical_id),
        data={'sourceLang': 'zh', 'targetLang': 'en', 'fields': ['name']},
        format='json',
    )

    assert response.status_code == 503
    assert response.data == {
        'code': 'provider_temporarily_unavailable',
        'detail': 'safe provider message',
    }
    assert BaseCard.objects.count() == 1
    log = LlmCallLog.objects.get(feature='character_translate')
    assert log.status == LlmCallLog.Status.ERROR
    assert log.error_message == 'provider_temporarily_unavailable'


@pytest.mark.django_db
def test_translation_unexpected_provider_exception_keeps_safe_generation_error(
    api_client, make_user_with_key, monkeypatch,
):
    vendor_marker = 'vendor-private-translation-response'
    provider = FakeProvider(error=RuntimeError(vendor_marker))
    monkeypatch.setattr(
        'characters.translate.resolve_active_provider',
        lambda _user: SimpleNamespace(instance=provider),
    )
    user = make_user_with_key()
    character = BaseCardFactory(owner=user, language='zh')
    api_client.force_authenticate(user=user)

    response = api_client.post(
        _translation_url(character.canonical_id),
        data={'sourceLang': 'zh', 'targetLang': 'en', 'fields': ['name']},
        format='json',
    )

    assert response.status_code == 500
    assert response.data == {
        'code': 'generation_failed',
        'detail': '翻译调用失败，请稍后重试',
    }
    assert vendor_marker not in str(response.data)
    assert BaseCard.objects.count() == 1
    log = LlmCallLog.objects.get(feature='character_translate')
    assert log.status == LlmCallLog.Status.ERROR
    assert log.error_message == 'generation_failed'
