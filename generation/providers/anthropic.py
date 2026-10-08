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
    MODEL = 'claude-sonnet-4-6'

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
                raise ProviderError(
                    'Anthropic API Key 无效', code='provider_key_invalid'
                ) from None

            except anthropic_sdk.RateLimitError:
                raise ProviderError(
                    'Anthropic 请求频率超限',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None

            except anthropic_sdk.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0 and not started:
                    logger.warning(
                        'Anthropic status=%s model=%s attempt=%d; retrying',
                        e.status_code, self.MODEL, attempt + 1,
                    )
                    time.sleep(2)
                    continue
                if e.status_code == 404:
                    raise ProviderError(
                        'Anthropic 当前配置的模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'Anthropic 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None

            except anthropic_sdk.APIConnectionError as e:
                if attempt == 0 and not started:
                    logger.warning(
                        'Anthropic connection error model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Anthropic 连接失败，请稍后重试',
                    code='provider_connection_failed',
                    http_status=503,
                ) from None

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
                raise ProviderError(
                    'Anthropic API Key 无效', code='provider_key_invalid'
                ) from None

            except anthropic_sdk.RateLimitError:
                raise ProviderError(
                    'Anthropic 请求频率超限',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None

            except anthropic_sdk.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0:
                    logger.warning(
                        'Anthropic status=%s model=%s attempt=%d; retrying',
                        e.status_code, self.MODEL, attempt + 1,
                    )
                    time.sleep(2)
                    continue
                if e.status_code == 404:
                    raise ProviderError(
                        'Anthropic 当前配置的模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'Anthropic 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None

            except anthropic_sdk.APIConnectionError as e:
                if attempt == 0:
                    logger.warning(
                        'Anthropic connection error model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Anthropic 连接失败，请稍后重试',
                    code='provider_connection_failed',
                    http_status=503,
                ) from None
