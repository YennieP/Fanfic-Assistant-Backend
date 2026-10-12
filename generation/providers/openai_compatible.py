"""Shared request/response transport for OpenAI-compatible HTTP providers.

Provider-specific retry rules and error messages intentionally stay in each
provider module. This module only owns the wire-level request shape and result
normalization shared by Cerebras and OpenRouter.
"""

from collections.abc import Generator

import httpx
from openai import OpenAI

from .base import CompleteResult, CompletionOptions, UsageInfo


DEFAULT_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=120.0,
    write=10.0,
    pool=5.0,
)


def create_client(
    *,
    api_key: str,
    base_url: str,
    default_headers: dict[str, str] | None = None,
):
    kwargs = {
        'api_key': api_key,
        'base_url': base_url,
        'timeout': DEFAULT_TIMEOUT,
    }
    if default_headers:
        kwargs['default_headers'] = default_headers
    return OpenAI(**kwargs)


def stream_chat(
    client,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    include_usage: bool = False,
) -> Generator[str | UsageInfo, None, None]:
    request = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'stream': True,
    }
    if max_tokens is not None:
        request['max_tokens'] = max_tokens
    if reasoning_effort is not None:
        request['reasoning_effort'] = reasoning_effort
    if include_usage:
        request['stream_options'] = {'include_usage': True}

    prompt_tokens = 0
    completion_tokens = 0
    for chunk in client.chat.completions.create(**request):
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
        if chunk.usage:
            prompt_tokens = chunk.usage.prompt_tokens or 0
            completion_tokens = chunk.usage.completion_tokens or 0

    yield UsageInfo(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def complete_chat(
    client,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    options: CompletionOptions,
    supports_reasoning_effort: bool,
) -> CompleteResult:
    request = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'max_tokens': options.max_tokens,
    }
    if options.response_format == 'json':
        request['response_format'] = {'type': 'json_object'}
    if supports_reasoning_effort and options.reasoning_effort is not None:
        request['reasoning_effort'] = options.reasoning_effort

    response = client.chat.completions.create(**request)
    usage = response.usage
    return CompleteResult(
        text=response.choices[0].message.content or '',
        model=model,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
    )
