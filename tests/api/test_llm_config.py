import pytest

from generation.providers.base import CompleteResult, ProviderError
from users.encryption import decrypt_key, encrypt_key
from users.models import UserLLMConfig, UserProviderKey


CONFIG_URL = '/api/auth/llm-config/'
VERIFY_URL = '/api/auth/llm-config/verify/'


def _success(model='resolved-provider-model'):
    return CompleteResult(
        text='OK',
        model=model,
        prompt_tokens=1,
        completion_tokens=1,
    )


def _authenticate(api_client, user):
    api_client.force_authenticate(user=user)


@pytest.mark.django_db
def test_get_reports_saved_state_without_calling_provider(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    _authenticate(api_client, user)

    def unexpected_verification(*_args, **_kwargs):
        raise AssertionError('GET must not spend provider quota')

    monkeypatch.setattr(
        'users.views.verify_provider_api_key', unexpected_verification,
    )

    response = api_client.get(CONFIG_URL)

    assert response.status_code == 200
    info = response.data['providers']['gemini']
    assert info == {
        'has_key': True,
        'model': 'gemini-2.5-flash',
        'enabled': True,
        'disabled_reason': None,
        'capabilities': {'video': True, 'embedding': True},
        'verification_status': None,
        'verification_checked_at': None,
        'verification_model': None,
        'verification_resolved_model': None,
    }
    rendered = response.json()['providers']['gemini']
    assert rendered['verificationStatus'] is None
    assert rendered['verificationCheckedAt'] is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('provider', 'model'),
    [
        ('anthropic', 'claude-sonnet-4-6'),
        ('cerebras', 'gpt-oss-120b'),
        ('openrouter', 'google/gemma-4-31b-it:free'),
    ],
)
def test_get_preserves_disabled_provider_key_but_reports_no_active_provider(
    api_client, make_user_with_key, provider, model,
):
    user = make_user_with_key(provider=provider)
    _authenticate(api_client, user)

    response = api_client.get(CONFIG_URL)

    assert response.status_code == 200
    assert response.data['active_provider'] is None
    info = response.data['providers'][provider]
    assert info['has_key'] is True
    assert info['model'] == model
    assert info['enabled'] is False
    assert info['disabled_reason'] == 'provider_disabled'
    assert UserProviderKey.objects.filter(
        user=user, provider=provider,
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('method', 'url'),
    [
        ('post', CONFIG_URL),
        ('patch', CONFIG_URL),
        ('post', VERIFY_URL),
    ],
)
@pytest.mark.parametrize('provider', ['anthropic', 'cerebras', 'openrouter'])
def test_disabled_provider_rejects_config_actions_without_provider_io(
    api_client, make_user_with_key, monkeypatch, method, url, provider,
):
    user = make_user_with_key(provider='gemini')
    disabled_key = UserProviderKey.objects.create(
        user=user,
        provider=provider,
        api_key_encrypted=encrypt_key(f'stored-{provider}-key'),
        verification_status='provider_rate_limit',
    )
    original_ciphertext = disabled_key.api_key_encrypted
    _authenticate(api_client, user)

    def unexpected_verification(*_args, **_kwargs):
        raise AssertionError('disabled provider must not make provider calls')

    monkeypatch.setattr(
        'users.views.verify_provider_api_key', unexpected_verification,
    )

    payload = {'provider': provider}
    if method == 'post' and url == CONFIG_URL:
        payload['api_key'] = 'replacement-key'
    response = getattr(api_client, method)(url, payload, format='json')

    assert response.status_code == 503
    assert response.data == {
        'code': 'provider_disabled',
        'detail': '该服务商已暂时下线，请选择其他服务商',
    }
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'
    disabled_key.refresh_from_db()
    assert disabled_key.api_key_encrypted == original_ciphertext
    assert disabled_key.verification_status == 'provider_rate_limit'


@pytest.mark.django_db
def test_save_verifies_candidate_before_replacing_key_and_switching(
    api_client, user_factory, monkeypatch,
):
    user = user_factory()
    _authenticate(api_client, user)
    calls = []

    def verify(provider, api_key):
        calls.append((provider, api_key))
        return _success('provider-actual-model')

    monkeypatch.setattr('users.views.verify_provider_api_key', verify)

    response = api_client.post(
        CONFIG_URL,
        {'provider': 'groq', 'api_key': 'candidate-key'},
        format='json',
    )

    assert response.status_code == 200
    assert calls == [('groq', 'candidate-key')]
    key = UserProviderKey.objects.get(user=user, provider='groq')
    assert decrypt_key(key.api_key_encrypted) == 'candidate-key'
    assert key.verification_status == 'valid'
    assert key.verification_checked_at is not None
    assert key.verification_model == 'openai/gpt-oss-120b'
    assert key.verification_resolved_model == 'provider-actual-model'
    assert UserLLMConfig.objects.get(user=user).provider == 'groq'


@pytest.mark.django_db
def test_failed_candidate_preserves_old_key_state_and_active_provider(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    old_key = UserProviderKey.objects.create(
        user=user,
        provider='groq',
        api_key_encrypted=encrypt_key('old-valid-key'),
        verification_status='valid',
        verification_model='old-requested-model',
        verification_resolved_model='old-resolved-model',
    )
    old_ciphertext = old_key.api_key_encrypted
    _authenticate(api_client, user)

    def reject(_provider, _api_key):
        raise ProviderError(
            'API Key 无效',
            code='provider_key_invalid',
            http_status=400,
        )

    monkeypatch.setattr('users.views.verify_provider_api_key', reject)

    response = api_client.post(
        CONFIG_URL,
        {'provider': 'groq', 'api_key': 'bad-candidate'},
        format='json',
    )

    assert response.status_code == 400
    assert response.data['code'] == 'provider_key_invalid'
    old_key.refresh_from_db()
    assert old_key.api_key_encrypted == old_ciphertext
    assert old_key.verification_status == 'valid'
    assert old_key.verification_model == 'old-requested-model'
    assert old_key.verification_resolved_model == 'old-resolved-model'
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'


@pytest.mark.django_db
def test_switch_verifies_saved_key_before_changing_active_provider(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    groq_key = UserProviderKey.objects.create(
        user=user,
        provider='groq',
        api_key_encrypted=encrypt_key('stored-groq-key'),
    )
    _authenticate(api_client, user)
    monkeypatch.setattr(
        'users.views.verify_provider_api_key',
        lambda provider, api_key: _success('resolved-groq-model'),
    )

    response = api_client.patch(
        CONFIG_URL,
        {'provider': 'groq'},
        format='json',
    )

    assert response.status_code == 200
    assert UserLLMConfig.objects.get(user=user).provider == 'groq'
    groq_key.refresh_from_db()
    assert groq_key.verification_status == 'valid'
    assert groq_key.verification_model == 'openai/gpt-oss-120b'
    assert groq_key.verification_resolved_model == 'resolved-groq-model'


@pytest.mark.django_db
def test_failed_switch_records_status_without_changing_active_provider(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    groq_key = UserProviderKey.objects.create(
        user=user,
        provider='groq',
        api_key_encrypted=encrypt_key('stored-groq-key'),
    )
    _authenticate(api_client, user)

    def reject(_provider, _api_key):
        raise ProviderError(
            '账户没有可用额度',
            code='provider_payment_required',
            http_status=402,
        )

    monkeypatch.setattr('users.views.verify_provider_api_key', reject)

    response = api_client.patch(
        CONFIG_URL,
        {'provider': 'groq'},
        format='json',
    )

    assert response.status_code == 402
    assert response.data['code'] == 'provider_payment_required'
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'
    groq_key.refresh_from_db()
    assert groq_key.verification_status == 'provider_payment_required'
    assert groq_key.verification_checked_at is not None
    assert groq_key.verification_model == 'openai/gpt-oss-120b'
    assert groq_key.verification_resolved_model is None


@pytest.mark.django_db
def test_explicit_verify_updates_state_without_switching_provider(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    groq_key = UserProviderKey.objects.create(
        user=user,
        provider='groq',
        api_key_encrypted=encrypt_key('stored-groq-key'),
    )
    _authenticate(api_client, user)
    monkeypatch.setattr(
        'users.views.verify_provider_api_key',
        lambda provider, api_key: _success('verified-groq-model'),
    )

    response = api_client.post(
        VERIFY_URL,
        {'provider': 'groq'},
        format='json',
    )

    assert response.status_code == 200
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'
    groq_key.refresh_from_db()
    assert groq_key.verification_status == 'valid'
    assert groq_key.verification_resolved_model == 'verified-groq-model'


@pytest.mark.django_db
def test_failed_explicit_verify_records_safe_status_without_switching(
    api_client, make_user_with_key, monkeypatch,
):
    user = make_user_with_key(provider='gemini')
    groq_key = UserProviderKey.objects.create(
        user=user,
        provider='groq',
        api_key_encrypted=encrypt_key('stored-groq-key'),
    )
    _authenticate(api_client, user)

    def reject(_provider, _api_key):
        raise ProviderError(
            '当前模型不可用',
            code='provider_model_unavailable',
            http_status=503,
        )

    monkeypatch.setattr('users.views.verify_provider_api_key', reject)

    response = api_client.post(
        VERIFY_URL,
        {'provider': 'groq'},
        format='json',
    )

    assert response.status_code == 503
    assert response.data['code'] == 'provider_model_unavailable'
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'
    groq_key.refresh_from_db()
    assert groq_key.verification_status == 'provider_model_unavailable'
    assert groq_key.verification_model == 'openai/gpt-oss-120b'
    assert groq_key.verification_resolved_model is None


@pytest.mark.django_db
def test_explicit_verify_hides_key_decryption_failure(
    api_client, make_user_with_key, monkeypatch, caplog,
):
    marker = 'secret-key-decryption-detail'
    user = make_user_with_key(provider='gemini')
    key = UserProviderKey.objects.get(user=user, provider='gemini')
    _authenticate(api_client, user)

    def fail_decryption(_encrypted):
        raise RuntimeError(marker)

    monkeypatch.setattr('users.views.decrypt_key', fail_decryption)

    response = api_client.post(
        VERIFY_URL,
        {'provider': 'gemini'},
        format='json',
    )

    assert response.status_code == 503
    assert response.data['code'] == 'provider_temporarily_unavailable'
    assert marker not in str(response.data)
    assert marker not in caplog.text
    key.refresh_from_db()
    assert key.verification_status == 'provider_temporarily_unavailable'
    assert UserLLMConfig.objects.get(user=user).provider == 'gemini'
