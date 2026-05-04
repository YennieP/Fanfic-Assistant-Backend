"""
Cerebras provider
免费额度约 10 亿 token/月，速度极快（~1000 TPS），OpenAI SDK 兼容
API Key 申请：https://cloud.cerebras.ai/
"""
import logging
from openai import OpenAI
import openai
from .base import BaseProvider, UsageInfo, CompleteResult, ProviderError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {500, 502, 503}


class CerebrasProvider(BaseProvider):
    supports_video = False
    supports_embedding = False

    MODEL = 'llama3.1-8b'

    def __init__(self, api_key: str):
        self.client = OpenAI(
            api_key=api_key,
            base_url='https://api.cerebras.ai/v1',
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
                raise ProviderError('Cerebras API Key 无效', code='provider_key_invalid')

            except openai.RateLimitError:
                raise ProviderError('Cerebras 本月免费额度（~10 亿 tokens）已用完', code='provider_quota_monthly')

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
                raise ProviderError('Cerebras API Key 无效', code='provider_key_invalid')

            except openai.RateLimitError:
                raise ProviderError('Cerebras 本月免费额度（~10 亿 tokens）已用完', code='provider_quota_monthly')

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