from generation.providers.gemini import GeminiProvider


class _Usage:
    prompt_token_count = 10
    candidates_token_count = 5


class _Response:
    text = '{"segments": []}'
    usage_metadata = _Usage()


def test_gemini_complete_requests_structured_json(monkeypatch):
    captured = {}

    class _Models:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return _Response()

    class _Client:
        def __init__(self, api_key):
            assert api_key == 'test-key'
            self.models = _Models()

    monkeypatch.setattr('generation.providers.gemini.genai.Client', _Client)

    result = GeminiProvider('test-key').complete(
        system_prompt='Return JSON.',
        user_prompt='Input',
        max_tokens=800,
    )

    assert result.text == '{"segments": []}'
    assert captured['config'].response_mime_type == 'application/json'
    assert captured['config'].max_output_tokens == 800
