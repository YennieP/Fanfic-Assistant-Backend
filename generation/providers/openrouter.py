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
                raise ProviderError('OpenRouter API Key 无效', code='provider_key_invalid')

            except openai.RateLimitError:
                raise ProviderError('OpenRouter 今日免费请求次数（200 次）已用完', code='provider_quota_daily')

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0 and not started:
                    logger.warning('OpenRouter %s on attempt %d, retrying: %s', e.status_code, attempt + 1, e)
                    continue
                raise

            except openai.APITimeoutError as e:
                if attempt == 0 and not started:
                    logger.warning('OpenRouter read timeout on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise ProviderError('OpenRouter 响应超时，请稍后重试', code='generation_failed')

            except openai.APIConnectionError as e:
                if attempt == 0 and not started:
                    logger.warning('OpenRouter connection error on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise

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
                raise ProviderError('OpenRouter API Key 无效', code='provider_key_invalid')

            except openai.RateLimitError:
                raise ProviderError('OpenRouter 今日免费请求次数（200 次）已用完', code='provider_quota_daily')

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0:
                    logger.warning('OpenRouter %s on attempt %d, retrying: %s', e.status_code, attempt + 1, e)
                    continue
                raise

            except openai.APITimeoutError as e:
                if attempt == 0:
                    logger.warning('OpenRouter read timeout on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise ProviderError('OpenRouter 响应超时，请稍后重试', code='generation_failed')

            except openai.APIConnectionError as e:
                if attempt == 0:
                    logger.warning('OpenRouter connection error on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise