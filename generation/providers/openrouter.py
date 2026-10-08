"""
OpenRouter provider — meta-llama/llama-3.3-70b-instruct:free
免费模型 200 req/天（充值 $10 后升至 1000 req/天）
OpenAI SDK 兼容，需要 HTTP-Referer header
API Key 申请：https://openrouter.ai/keys
"""
import logging
import httpx
from openai import OpenAI
import openai
from .base import BaseProvider, UsageInfo, CompleteResult, ProviderError

logger = logging.getLogger(__name__)

# 502 = OpenRouter 转发上游模型失败，较常见，值得重试
_RETRYABLE_STATUS = {500, 502, 503}


class OpenRouterProvider(BaseProvider):
    supports_video = False
    supports_embedding = False

    MODEL = 'meta-llama/llama-3.3-70b-instruct:free'

    def __init__(self, api_key: str):
        self.client = OpenAI(
            api_key=api_key,
            base_url='https://openrouter.ai/api/v1',
            default_headers={
                'HTTP-Referer': 'https://fanfic-assistant-production.up.railway.app',
                'X-Title': 'Fanfic Assistant',
            },
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
                    stream=True,
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
                    'OpenRouter API Key 无效', code='provider_key_invalid'
                ) from None

            except openai.RateLimitError:
                raise ProviderError(
                    'OpenRouter 请求频率或额度已受限，请稍后重试',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0 and not started:
                    logger.warning(
                        'OpenRouter status=%s model=%s attempt=%d; retrying',
                        e.status_code, self.MODEL, attempt + 1,
                    )
                    continue
                if e.status_code == 404:
                    raise ProviderError(
                        'OpenRouter 当前配置的免费模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'OpenRouter 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None

            except openai.APITimeoutError as e:
                if attempt == 0 and not started:
                    logger.warning(
                        'OpenRouter timeout model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'OpenRouter 响应超时，请稍后重试',
                    code='provider_timeout',
                    http_status=503,
                ) from None

            except openai.APIConnectionError as e:
                if attempt == 0 and not started:
                    logger.warning(
                        'OpenRouter connection error model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'OpenRouter 连接失败，请稍后重试',
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
                    'OpenRouter API Key 无效', code='provider_key_invalid'
                ) from None

            except openai.RateLimitError:
                raise ProviderError(
                    'OpenRouter 请求频率或额度已受限，请稍后重试',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0:
                    logger.warning(
                        'OpenRouter status=%s model=%s attempt=%d; retrying',
                        e.status_code, self.MODEL, attempt + 1,
                    )
                    continue
                if e.status_code == 404:
                    raise ProviderError(
                        'OpenRouter 当前配置的免费模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'OpenRouter 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None

            except openai.APITimeoutError as e:
                if attempt == 0:
                    logger.warning(
                        'OpenRouter timeout model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'OpenRouter 响应超时，请稍后重试',
                    code='provider_timeout',
                    http_status=503,
                ) from None

            except openai.APIConnectionError as e:
                if attempt == 0:
                    logger.warning(
                        'OpenRouter connection error model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'OpenRouter 连接失败，请稍后重试',
                    code='provider_connection_failed',
                    http_status=503,
                ) from None
