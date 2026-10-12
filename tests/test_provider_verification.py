import pytest

from generation.provider_verification import verify_provider_api_key
from generation.providers.base import CompleteResult, ProviderError


class RecordingProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def complete(self, system_prompt, user_prompt, *, options):
        self.calls.append((system_prompt, user_prompt, options))
        if self.error:
            raise self.error
        return self.result


def test_verification_uses_a_low_cost_text_completion(monkeypatch):
    result = CompleteResult(
        text='OK',
        model='actual-model',
        prompt_tokens=1,
        completion_tokens=1,
    )
    provider = RecordingProvider(result=result)
    monkeypatch.setattr(
        'generation.provider_verification.get_provider',
        lambda name, api_key: provider,
    )

    actual = verify_provider_api_key('gemini', 'candidate-key')

    assert actual is result
    assert len(provider.calls) == 1
    options = provider.calls[0][2]
    assert options.response_format == 'text'
    assert options.max_tokens == 64
    assert options.reasoning_effort == 'low'


def test_verification_preserves_safe_provider_errors(monkeypatch):
    error = ProviderError(
        'API Key 无效',
        code='provider_key_invalid',
        http_status=400,
    )
    provider = RecordingProvider(error=error)
    monkeypatch.setattr(
        'generation.provider_verification.get_provider',
        lambda name, api_key: provider,
    )

    with pytest.raises(ProviderError) as exc_info:
        verify_provider_api_key('gemini', 'candidate-key')

    assert exc_info.value is error


def test_verification_hides_unexpected_provider_details(monkeypatch, caplog):
    marker = 'vendor-response-secret-detail'

    def fail_construction(_name, _api_key):
        raise RuntimeError(marker)

    monkeypatch.setattr(
        'generation.provider_verification.get_provider',
        fail_construction,
    )

    with pytest.raises(ProviderError) as exc_info:
        verify_provider_api_key('gemini', 'candidate-key')

    assert exc_info.value.code == 'provider_temporarily_unavailable'
    assert exc_info.value.http_status == 503
    assert marker not in str(exc_info.value)
    assert marker not in caplog.text
