import logging
import groq as groq_sdk
from .base import BaseProvider, UsageInfo, CompleteResult, ProviderError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {500, 502, 503}


class GroqProvider(BaseProvider):
    MODEL = 'openai/gpt-oss-120b'

    def stream(self, system_prompt: str, user_prompt: str):
        client = groq_sdk.Groq(api_key=self.api_key)

        for attempt in range(2):
            started = False
            prompt_tokens = 0
            completion_tokens = 0
            try:
                stream = client.chat.completions.create(
                    model=self.MODEL,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_prompt},
                    ],
                    max_tokens=4000,
                    reasoning_effort='low',
                    stream=True,
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        started = True
                        yield delta.content
                    if chunk.usage:
                        prompt_tokens = chunk.usage.prompt_tokens
                        completion_tokens = chunk.usage.completion_tokens

                yield UsageInfo(
                    model=self.MODEL,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                return

            except groq_sdk.AuthenticationError:
                raise ProviderError(
                    'Groq API Key 无效', code='provider_key_invalid'
                ) from None

            except groq_sdk.RateLimitError:
                raise ProviderError(
                    'Groq 请求频率或额度已受限，请稍后重试',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None

            except groq_sdk.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0 and not started:
                    logger.warning(
                        'Groq status=%s model=%s attempt=%d; retrying',
                        e.status_code, self.MODEL, attempt + 1,
                    )
                    continue
                if e.status_code == 404:
                    raise ProviderError(
                        'Groq 当前配置的模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'Groq 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None

            except groq_sdk.APIConnectionError as e:
                if attempt == 0 and not started:
                    logger.warning(
                        'Groq connection error model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Groq 连接失败，请稍后重试',
                    code='provider_connection_failed',
                    http_status=503,
                ) from None

    def complete(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 2000
    ) -> CompleteResult:
        client = groq_sdk.Groq(api_key=self.api_key)

        for attempt in range(2):
            try:
                response = client.chat.completions.create(
                    model=self.MODEL,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_prompt},
                    ],
                    max_tokens=max_tokens,
                    reasoning_effort='low',
                    response_format={'type': 'json_object'},
                )
                usage = response.usage
                return CompleteResult(
                    text=response.choices[0].message.content,
                    model=self.MODEL,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                )

            except groq_sdk.AuthenticationError:
                raise ProviderError(
                    'Groq API Key 无效', code='provider_key_invalid'
                ) from None

            except groq_sdk.RateLimitError:
                raise ProviderError(
                    'Groq 请求频率或额度已受限，请稍后重试',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None

            except groq_sdk.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0:
                    logger.warning(
                        'Groq status=%s model=%s attempt=%d; retrying',
                        e.status_code, self.MODEL, attempt + 1,
                    )
                    continue
                if e.status_code == 404:
                    raise ProviderError(
                        'Groq 当前配置的模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'Groq 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None

            except groq_sdk.APIConnectionError as e:
                if attempt == 0:
                    logger.warning(
                        'Groq connection error model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Groq 连接失败，请稍后重试',
                    code='provider_connection_failed',
                    http_status=503,
                ) from None
