"""
OpenRouter provider — meta-llama/llama-3.3-70b-instruct:free
免费模型 200 req/天（充值 $10 后升至 1000 req/天），11+ 个免费模型可选
OpenAI SDK 兼容，需要 HTTP-Referer header
API Key 申请：https://openrouter.ai/keys
"""
import logging
from openai import OpenAI
import openai
from .base import BaseProvider, UsageInfo, CompleteResult, ProviderError

logger = logging.getLogger(__name__)

# OpenRouter 特别说明：
# 502 = OpenRouter 转发到上游模型时发生错误，短暂重试有效
# 503 = 上游模型暂时不可用，短暂重试有效
# 429 = 今日 200 req 免费配额耗尽，重试无意义
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
        )

    def stream(self, system_prompt: str, user_prompt: str):
        """
        重试策略：first-chunk 阶段（started=False）发生 5xx/502/503 或连接错误时重试 1 次。
        OpenRouter 的 502 较常见（上游模型负载波动），是重试的主要场景。
        429 今日配额耗尽不重试，直接返回用户可读错误。
        """
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
                return  # 成功完成

            except openai.AuthenticationError:
                raise ProviderError('OpenRouter API Key 无效，请在设置页重新配置')

            except openai.RateLimitError:
                raise ProviderError('OpenRouter 今日免费请求次数（200 次）已用完，请明天再试')

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0 and not started:
                    logger.warning('OpenRouter %s on attempt %d, retrying: %s', e.status_code, attempt + 1, e)
                    continue
                raise

            except openai.APIConnectionError as e:
                if attempt == 0 and not started:
                    logger.warning('OpenRouter connection error on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
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
                raise ProviderError('OpenRouter API Key 无效，请在设置页重新配置')

            except openai.RateLimitError:
                raise ProviderError('OpenRouter 今日免费请求次数（200 次）已用完，请明天再试')

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0:
                    logger.warning('OpenRouter %s on attempt %d, retrying: %s', e.status_code, attempt + 1, e)
                    continue
                raise

            except openai.APIConnectionError as e:
                if attempt == 0:
                    logger.warning('OpenRouter connection error on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise