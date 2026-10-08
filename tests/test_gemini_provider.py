from django.conf import settings

from generation.providers.gemini import GeminiProvider


class _Usage:
    prompt_token_count = 10
    candidates_token_count = 5
    total_token_count = 22


class _FinishReason:
    value = 'STOP'


class _Candidate:
    finish_reason = _FinishReason()


class _Response:
    text = '{"segments": ["private-model-output"]}'
    usage_metadata = _Usage()
    candidates = [_Candidate()]


def test_gemini_complete_requests_structured_json_and_logs_safe_capacity(
    monkeypatch,
):
    captured = {}
    telemetry = []

    class _Models:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return _Response()

    class _Client:
        def __init__(self, api_key):
            assert api_key == 'test-key'
            self.models = _Models()

    monkeypatch.setattr('generation.providers.gemini.genai.Client', _Client)
    monkeypatch.setattr(
        'generation.providers.gemini.logger.info',
        lambda message, *args: telemetry.append(message % args),
    )

    result = GeminiProvider('test-key').complete(
        system_prompt='Return JSON.',
        user_prompt='Input',
        max_tokens=800,
    )

    assert result.text == '{"segments": ["private-model-output"]}'
    assert captured['config'].response_mime_type == 'application/json'
    assert captured['config'].max_output_tokens == 800
    assert (
        'Gemini complete capacity: model=gemini-2.5-flash '
        'finish_reason=STOP prompt_tokens=10 completion_tokens=5 '
        'total_tokens=22 unattributed_tokens=7 max_output_tokens=800'
        in telemetry
    )
    assert all('private-model-output' not in message for message in telemetry)
    assert all('test-key' not in message for message in telemetry)


def test_gemini_capacity_telemetry_is_visible_at_default_log_level():
    logger_config = settings.LOGGING['loggers']['generation.providers.gemini']

    assert logger_config['level'] == 'INFO'
    assert logger_config['propagate'] is False
