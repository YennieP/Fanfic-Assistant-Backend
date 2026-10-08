"""
Cerebras provider
OpenAI SDK 兼容
API Key 申请：https://cloud.cerebras.ai/
"""
import logging
import httpx
from openai import OpenAI
import openai
from .base import BaseProvider, UsageInfo, CompleteResult, ProviderError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {500, 502, 503}


class CerebrasProvider(BaseProvider):
    supports_video = False
    supports_embedding = False

    MODEL = 'gpt-oss-120b'

    def __init__(self, api_key: str):
        self.client = OpenAI(
            api_key=api_key,
            base_url='https://api.cerebras.ai/v1',
            # stream() 中途卡住时防止 gunicorn sync worker 因无法发送心跳而被 SIGKILL
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
        )

    def stream(self, system_prompt: str, user_prompt: str):
        for attempt in range(2):
            started = False
            prompt_tokens = 0
            completion_tokens = 0
            try:
                response = self.client.chat.completions.create(
                    model=self.MODEL,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_prompt},
                    ],
                    max_tokens=4000,
                    reasoning_effort='low',
                    stream=True,
                    stream_options={'include_usage': True},
                )
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        started = True
                        yield chunk.choices[0].delta.content
                    if chunk.usage:
                        prompt_tokens = chunk.usage.prompt_tokens or 0
                        completion_tokens = chunk.usage.completion_tokens or 0

                yield UsageInfo(
                    model=self.MODEL,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                return

            except openai.AuthenticationError:
                raise ProviderError(
                    'Cerebras API Key 无效', code='provider_key_invalid'
                ) from None

            except openai.RateLimitError:
                raise ProviderError(
                    'Cerebras 请求频率或额度已受限，请稍后重试',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0 and not started:
                    logger.warning(
                        'Cerebras status=%s model=%s attempt=%d; retrying',
                        e.status_code, self.MODEL, attempt + 1,
                    )
                    continue
                logger.warning(
                    'Cerebras request failed status=%s model=%s operation=stream',
                    e.status_code, self.MODEL,
                )
                if e.status_code == 402:
                    raise ProviderError(
                        'Cerebras 账户当前没有可用额度，请在服务商控制台检查试用额度或计费设置',
                        code='provider_payment_required',
                        http_status=402,
                    ) from None
                if e.status_code == 404:
                    raise ProviderError(
                        'Cerebras 当前配置的模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'Cerebras 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None

            except openai.APITimeoutError as e:
                if attempt == 0 and not started:
                    logger.warning(
                        'Cerebras timeout model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Cerebras 响应超时，请稍后重试',
                    code='provider_timeout',
                    http_status=503,
                ) from None

            except openai.APIConnectionError as e:
                if attempt == 0 and not started:
                    logger.warning(
                        'Cerebras connection error model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Cerebras 连接失败，请稍后重试',
                    code='provider_connection_failed',
                    http_status=503,
                ) from None

    def complete(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 1000,
    ) -> CompleteResult:
        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.MODEL,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_prompt},
                    ],
                    max_tokens=max_tokens,
                    reasoning_effort='low',
                    response_format={'type': 'json_object'},
                )
                text = response.choices[0].message.content or ''
                usage = response.usage
                return CompleteResult(
                    text=text,
                    model=self.MODEL,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                )

            except openai.AuthenticationError:
                raise ProviderError(
                    'Cerebras API Key 无效', code='provider_key_invalid'
                ) from None

            except openai.RateLimitError:
                raise ProviderError(
                    'Cerebras 请求频率或额度已受限，请稍后重试',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0:
                    logger.warning(
                        'Cerebras status=%s model=%s attempt=%d; retrying',
                        e.status_code, self.MODEL, attempt + 1,
                    )
                    continue
                logger.warning(
                    'Cerebras request failed status=%s model=%s operation=complete',
                    e.status_code, self.MODEL,
                )
                if e.status_code == 402:
                    raise ProviderError(
                        'Cerebras 账户当前没有可用额度，请在服务商控制台检查试用额度或计费设置',
                        code='provider_payment_required',
                        http_status=402,
                    ) from None
                if e.status_code == 404:
                    raise ProviderError(
                        'Cerebras 当前配置的模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'Cerebras 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None

            except openai.APITimeoutError as e:
                if attempt == 0:
                    logger.warning(
                        'Cerebras timeout model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Cerebras 响应超时，请稍后重试',
                    code='provider_timeout',
                    http_status=503,
                ) from None

            except openai.APIConnectionError as e:
                if attempt == 0:
                    logger.warning(
                        'Cerebras connection error model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Cerebras 连接失败，请稍后重试',
                    code='provider_connection_failed',
                    http_status=503,
                ) from None
