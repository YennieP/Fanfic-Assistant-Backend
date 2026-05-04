"""
Cerebras provider
免费额度约 10 亿 token/月，速度极快（~1000 TPS），OpenAI SDK 兼容
API Key 申请：https://cloud.cerebras.ai/

可用模型（按账号 tier 而定）：
  llama3.1-8b               ← 免费 tier 确认可用
  llama-3.3-70b             ← 需要确认账号权限
  llama-4-scout-17b-16e-instruct  ← 较新，性能更好
"""
import logging
from openai import OpenAI
import openai
from .base import BaseProvider, UsageInfo, CompleteResult, ProviderError

logger = logging.getLogger(__name__)

# Cerebras 使用 OpenAI SDK 兼容接口，异常类型为 openai.*
# 5xx = 服务端瞬发，值得重试；429 = 月配额耗尽，不重试
_RETRYABLE_STATUS = {500, 502, 503}


class CerebrasProvider(BaseProvider):
    supports_video = False
    supports_embedding = False

    # 若 llama-3.3-70b 报 404，换成 llama3.1-8b 或 llama-4-scout-17b-16e-instruct
    MODEL = 'llama3.1-8b'

    def __init__(self, api_key: str):
        self.client = OpenAI(
            api_key=api_key,
            base_url='https://api.cerebras.ai/v1',
        )

    def stream(self, system_prompt: str, user_prompt: str):
        """
        重试策略：first-chunk 阶段（started=False）发生 5xx 或连接错误时重试 1 次。
        429 月配额耗尽不重试，直接返回用户可读错误。
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
                return  # 成功完成

            except openai.AuthenticationError:
                raise ProviderError('Cerebras API Key 无效，请在设置页重新配置')

            except openai.RateLimitError:
                raise ProviderError('Cerebras 本月免费额度（~10 亿 tokens）已用完，请下月再试')

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0 and not started:
                    logger.warning('Cerebras %s on attempt %d, retrying: %s', e.status_code, attempt + 1, e)
                    continue
                raise

            except openai.APIConnectionError as e:
                if attempt == 0 and not started:
                    logger.warning('Cerebras connection error on attempt %d, retrying: %s', attempt + 1, e)
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
                raise ProviderError('Cerebras API Key 无效，请在设置页重新配置')

            except openai.RateLimitError:
                raise ProviderError('Cerebras 本月免费额度（~10 亿 tokens）已用完，请下月再试')

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0:
                    logger.warning('Cerebras %s on attempt %d, retrying: %s', e.status_code, attempt + 1, e)
                    continue
                raise

            except openai.APIConnectionError as e:
                if attempt == 0:
                    logger.warning('Cerebras connection error on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise