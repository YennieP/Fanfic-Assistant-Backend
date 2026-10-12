from types import SimpleNamespace

from generation.providers.base import CompletionOptions
from generation.providers.openai_compatible import complete_chat


class RecordingCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content='result'),
            )],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3),
        )


def _client(completions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_openai_compatible_json_request_applies_explicit_reasoning():
    completions = RecordingCompletions()

    result = complete_chat(
        _client(completions),
        model='test-model',
        system_prompt='system',
        user_prompt='user',
        options=CompletionOptions(
            response_format='json',
            max_tokens=321,
            reasoning_effort='low',
        ),
        supports_reasoning_effort=True,
    )

    assert result.text == 'result'
    assert completions.calls == [{
        'model': 'test-model',
        'messages': [
            {'role': 'system', 'content': 'system'},
            {'role': 'user', 'content': 'user'},
        ],
        'max_tokens': 321,
        'response_format': {'type': 'json_object'},
        'reasoning_effort': 'low',
    }]


def test_openai_compatible_text_request_omits_json_and_unsupported_reasoning():
    completions = RecordingCompletions()

    complete_chat(
        _client(completions),
        model='test-model',
        system_prompt='system',
        user_prompt='user',
        options=CompletionOptions(
            response_format='text',
            max_tokens=123,
            reasoning_effort='low',
        ),
        supports_reasoning_effort=False,
    )

    assert completions.calls == [{
        'model': 'test-model',
        'messages': [
            {'role': 'system', 'content': 'system'},
            {'role': 'user', 'content': 'user'},
        ],
        'max_tokens': 123,
    }]
