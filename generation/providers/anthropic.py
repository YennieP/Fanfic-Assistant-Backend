import time
import logging
import anthropic as anthropic_sdk
from .base import BaseProvider, UsageInfo, CompleteResult, ProviderError

logger = logging.getLogger(__name__)

# 值得重试的 HTTP 状态码：
# 500 = 内部服务器错误（瞬发）
# 529 = Anthropic 特有过载状态，官方建议短暂等待后重试
_RETRYABLE_STATUS = {500, 529}


class AnthropicProvider(BaseProvider):
    MODEL = 'claude-sonnet-4-20250514'

    def stream(self, system_prompt: str, user_prompt: str):
        """
        重试策略：仅在「尚未开始 yield」阶段重试，最多 1 次。
        一旦开始向前端输出内容（started=True），错误直接向上传播，
        由 views.py 的 except 发出 SSE error 事件。
        """
        client = anthropic_sdk.Anthropic(api_key=self.api_key)

        for attempt in range(2):
            started = False
            try:
                with client.messages.stream(
                    model=self.MODEL,
                    max_tokens=2000,
                    system=system_prompt,
                    messages=[{'role': 'user', 'content': user_prompt}],
                ) as s:
                    for text in s.text_stream:
                        started = True
                        yield text
                    final = s.get_final_message()

                yield UsageInfo(
                    model=self.MODEL,
                    prompt_tokens=final.usage.input_tokens,
                    completion_tokens=final.usage.output_tokens,
                )
                return  # 成功完成

            except anthropic_sdk.AuthenticationError:
                raise ProviderError('Anthropic API Key 无效', code='provider_key_invalid')

            except anthropic_sdk.RateLimitError:
                raise ProviderError('Anthropic 请求频率超限', code='provider_rate_limit')

            except anthropic_sdk.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0 and not started:
                    logger.warning('Anthropic %s on attempt %d, retrying: %s', e.status_code, attempt + 1, e)
                    time.sleep(2)
                    continue
                raise

            except anthropic_sdk.APIConnectionError as e:
                if attempt == 0 and not started:
                    logger.warning('Anthropic connection error on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise

    def complete(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 2000
    ) -> CompleteResult:
        client = anthropic_sdk.Anthropic(api_key=self.api_key)

        for attempt in range(2):
            try:
                msg = client.messages.create(
                    model=self.MODEL,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=[{'role': 'user', 'content': user_prompt}],
                )
                return CompleteResult(
                    text=msg.content[0].text,
                    model=self.MODEL,
                    prompt_tokens=msg.usage.input_tokens,
                    completion_tokens=msg.usage.output_tokens,
                )

            except anthropic_sdk.AuthenticationError:
                raise ProviderError('Anthropic API Key 无效', code='provider_key_invalid')

            except anthropic_sdk.RateLimitError:
                raise ProviderError('Anthropic 请求频率超限', code='provider_rate_limit')

            except anthropic_sdk.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0:
                    logger.warning('Anthropic %s on attempt %d, retrying: %s', e.status_code, attempt + 1, e)
                    time.sleep(2)
                    continue
                raise

            except anthropic_sdk.APIConnectionError as e:
                if attempt == 0:
                    logger.warning('Anthropic connection error on attempt %d, retrying: %s', attempt + 1, e)
                    continue
                raise