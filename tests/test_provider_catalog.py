import pytest
from django.utils.module_loading import import_string

from examples.models import Article
from generation.providers.base import ProviderError
from tests.factories import BaseCardFactory
from users.models import SUPPORTED_PROVIDERS, UserLLMConfig


VENDOR_MARKER = 'vendor-user-123 request-secret-456'


def test_catalog_is_the_single_source_for_names_models_and_capabilities():
    from generation.providers.catalog import PROVIDER_CATALOG

    assert tuple(SUPPORTED_PROVIDERS) == tuple(PROVIDER_CATALOG)
    assert {
        name: definition.model
        for name, definition in PROVIDER_CATALOG.items()
    } == {
        'anthropic': 'claude-sonnet-4-6',
        'gemini': 'gemini-2.5-flash',
        'groq': 'openai/gpt-oss-120b',
        'cerebras': 'gpt-oss-120b',
        'openrouter': 'meta-llama/llama-3.3-70b-instruct:free',
    }
    assert PROVIDER_CATALOG['gemini'].supports_video is True
    assert PROVIDER_CATALOG['gemini'].supports_embedding is True
    assert all(
        not definition.supports_video and not definition.supports_embedding
        for name, definition in PROVIDER_CATALOG.items()
        if name != 'gemini'
    )
    for definition in PROVIDER_CATALOG.values():
        provider_type = import_string(definition.provider_class)
        assert provider_type.MODEL == definition.model
        assert provider_type.supports_video is definition.supports_video
        assert provider_type.supports_embedding is definition.supports_embedding


def test_unknown_provider_fails_closed_without_exposing_the_supplied_key():
    from generation.providers import get_provider

    with pytest.raises(ProviderError) as exc_info:
        get_provider('unknown-provider', VENDOR_MARKER)

    assert exc_info.value.code == 'provider_model_unavailable'
    assert exc_info.value.http_status == 503
    assert VENDOR_MARKER not in str(exc_info.value)


@pytest.mark.django_db
def test_resolver_reports_missing_config_with_a_safe_code(user_factory):
    from generation.provider_service import resolve_active_provider

    user = user_factory()

    with pytest.raises(ProviderError) as exc_info:
        resolve_active_provider(user)

    assert exc_info.value.code == 'no_api_key'
    assert exc_info.value.http_status == 400


@pytest.mark.django_db
def test_resolver_rejects_unknown_stored_provider_before_key_lookup(user_factory):
    from generation.provider_service import resolve_active_provider

    user = user_factory()
    UserLLMConfig.objects.create(user=user, provider='unknown-provider')

    with pytest.raises(ProviderError) as exc_info:
        resolve_active_provider(user)

    assert exc_info.value.code == 'provider_model_unavailable'
    assert exc_info.value.http_status == 503


@pytest.mark.django_db
def test_resolver_hides_key_decryption_failures(
    make_user_with_key, monkeypatch, caplog,
):
    from generation.provider_service import resolve_active_provider

    user = make_user_with_key()

    def fail_decryption(_encrypted):
        raise RuntimeError(VENDOR_MARKER)

    monkeypatch.setattr(
        'generation.provider_service.decrypt_key', fail_decryption,
    )

    with pytest.raises(ProviderError) as exc_info:
        resolve_active_provider(user)

    assert exc_info.value.code == 'provider_temporarily_unavailable'
    assert exc_info.value.http_status == 503
    assert VENDOR_MARKER not in str(exc_info.value)
    assert VENDOR_MARKER not in caplog.text


@pytest.mark.django_db
def test_segment_endpoint_does_not_return_local_provider_exception_text(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key()
    character = BaseCardFactory(owner=user)
    article = Article.objects.create(
        owner=user,
        character=character,
        title='safe provider resolution',
        content='synthetic line',
    )
    api_client.force_authenticate(user=user)

    def fail_decryption(_encrypted):
        raise RuntimeError(VENDOR_MARKER)

    monkeypatch.setattr(
        'generation.provider_service.decrypt_key', fail_decryption,
    )

    response = api_client.post(
        f'/api/examples/articles/{article.id}/segment/',
        format='json',
    )

    assert response.status_code == 503
    assert response.data == {
        'code': 'provider_temporarily_unavailable',
        'detail': 'Provider 配置暂时不可用，请稍后重试',
    }
    assert VENDOR_MARKER not in str(response.data)
