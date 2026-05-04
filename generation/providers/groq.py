import logging
import groq as groq_sdk
from .base import BaseProvider, UsageInfo, CompleteResult, ProviderError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {500, 502, 503}


class GroqProvider(BaseProvider):
    MODEL = 'llama-3.3-70b-versatile'

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
                raise ProviderError('Groq API Key 无效', code='provider_key_invalid')

            except groq_sdk.RateLimitError:
                raise ProviderError('Groq 今日免费额度（100K tokens）已用完', code='provider_quota_daily')

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
                raise ProviderError('Groq API Key 无效', code='provider_key_invalid')

            except groq_sdk.RateLimitError:
                raise ProviderError('Groq 今日免费额度（100K tokens）已用完', code='provider_quota_daily')

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