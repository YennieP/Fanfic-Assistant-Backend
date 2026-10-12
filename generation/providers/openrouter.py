"""
OpenRouter provider — meta-llama/llama-3.3-70b-instruct:free
免费模型 200 req/天（充值 $10 后升至 1000 req/天）
OpenAI SDK 兼容，需要 HTTP-Referer header
API Key 申请：https://openrouter.ai/keys
"""
import logging
import openai
from .base import (
    BaseProvider,
    CompleteResult,
    CompletionOptions,
    ProviderError,
    UsageInfo,
)
from .catalog import get_provider_definition
from .openai_compatible import complete_chat, create_client, stream_chat

logger = logging.getLogger(__name__)

# 502 = OpenRouter 转发上游模型失败，较常见，值得重试
_RETRYABLE_STATUS = {500, 502, 503}


class OpenRouterProvider(BaseProvider):
    MODEL = get_provider_definition('openrouter').model

    def __init__(self, api_key: str):
        self.client = create_client(
            api_key=api_key,
            base_url='https://openrouter.ai/api/v1',
            default_headers={
                'HTTP-Referer': 'https://fanfic-assistant-production.up.railway.app',
                'X-Title': 'Fanfic Assistant',
            },
        )

    def stream(self, system_prompt: str, user_prompt: str):
        for attempt in range(2):
            started = False
            try:
                response = stream_chat(
                    self.client,
                    model=self.MODEL,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                for item in response:
                    if isinstance(item, UsageInfo):
                        yield item
                    else:
                        started = True
                        yield item
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
                if e.status_code == 402:
                    raise ProviderError(
                        'OpenRouter 账户当前没有可用额度',
                        code='provider_payment_required',
                        http_status=402,
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
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        options: CompletionOptions,
    ) -> CompleteResult:
        for attempt in range(2):
            try:
                return complete_chat(
                    self.client,
                    model=self.MODEL,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    options=options,
                    supports_reasoning_effort=False,
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
                if e.status_code == 402:
                    raise ProviderError(
                        'OpenRouter 账户当前没有可用额度',
                        code='provider_payment_required',
                        http_status=402,
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
