import logging
import groq as groq_sdk
from .base import BaseProvider, UsageInfo, CompleteResult, ProviderError

logger = logging.getLogger(__name__)

# Groq 的 429 = 日配额耗尽（100K tokens/天），重试无意义，直接告知用户
# 5xx = 服务端瞬发错误，值得重试 1 次
_RETRYABLE_STATUS = {500, 502, 503}


class GroqProvider(BaseProvider):
    MODEL = 'llama-3.3-70b-versatile'

    def stream(self, system_prompt: str, user_prompt: str):
        """
        重试策略：first-chunk 阶段（started=False）发生 5xx 或连接错误时重试 1 次。
        429 配额耗尽不重试，直接返回用户可读错误。
        """
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
                return  # 成功完成

            except groq_sdk.AuthenticationError:
                raise ProviderError('Groq API Key 无效，请在设置页重新配置')

            except groq_sdk.RateLimitError:
                raise ProviderError('Groq 今日免费额度（100K tokens）已用完，请明天再试')

            except groq_sdk.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0 and not started:
                    logger.warning('Groq %s on attempt %d, retrying: %s', e.status_code, attempt + 1, e)
                    continue
                raise

            except groq_sdk.APIConnectionError as e:
                if attempt == 0 and not started:
                    logger.warning('Groq connection error on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise

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
                )
                usage = response.usage
                return CompleteResult(
                    text=response.choices[0].message.content,
                    model=self.MODEL,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                )

            except groq_sdk.AuthenticationError:
                raise ProviderError('Groq API Key 无效，请在设置页重新配置')

            except groq_sdk.RateLimitError:
                raise ProviderError('Groq 今日免费额度（100K tokens）已用完，请明天再试')

            except groq_sdk.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0:
                    logger.warning('Groq %s on attempt %d, retrying: %s', e.status_code, attempt + 1, e)
                    continue
                raise

            except groq_sdk.APIConnectionError as e:
                if attempt == 0:
                    logger.warning('Groq connection error on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise