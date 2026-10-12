from generation.providers.base import CompleteResult, CompletionOptions
from logs.decorators import log_llm_call


def test_completion_options_require_explicit_response_format():
    options = CompletionOptions(
        response_format='json',
        max_tokens=321,
        reasoning_effort='low',
    )

    assert options.response_format == 'json'
    assert options.max_tokens == 321
    assert options.reasoning_effort == 'low'


def test_llm_log_decorator_preserves_complete_result(db):
    @log_llm_call(feature='execution_contract_test', sync=True)
    def complete(generation_id=None):
        return CompleteResult(
            text='{"ok":true}',
            model='fake-model',
            prompt_tokens=3,
            completion_tokens=4,
        )

    result = complete()

    assert isinstance(result, CompleteResult)
    assert result.text == '{"ok":true}'
