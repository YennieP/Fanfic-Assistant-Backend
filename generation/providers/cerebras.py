"""
Cerebras provider
OpenAI SDK 兼容
API Key 申请：https://cloud.cerebras.ai/
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

_RETRYABLE_STATUS = {500, 502, 503}


class CerebrasProvider(BaseProvider):
    MODEL = get_provider_definition('cerebras').model

    def __init__(self, api_key: str):
        self.client = create_client(
            api_key=api_key,
            base_url='https://api.cerebras.ai/v1',
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
                    max_tokens=4000,
                    reasoning_effort='low',
                    include_usage=True,
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
                    'Cerebras API Key 无效', code='provider_key_invalid'
                ) from None

            except openai.RateLimitError:
                raise ProviderError(
                    'Cerebras 请求频率或额度已受限，请稍后重试',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0 and not started:
                    logger.warning(
                        'Cerebras status=%s model=%s attempt=%d; retrying',
                        e.status_code, self.MODEL, attempt + 1,
                    )
                    continue
                logger.warning(
                    'Cerebras request failed status=%s model=%s operation=stream',
                    e.status_code, self.MODEL,
                )
                if e.status_code == 402:
                    raise ProviderError(
                        'Cerebras 账户当前没有可用额度，请在服务商控制台检查试用额度或计费设置',
                        code='provider_payment_required',
                        http_status=402,
                    ) from None
                if e.status_code == 404:
                    raise ProviderError(
                        'Cerebras 当前配置的模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'Cerebras 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None

            except openai.APITimeoutError as e:
                if attempt == 0 and not started:
                    logger.warning(
                        'Cerebras timeout model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Cerebras 响应超时，请稍后重试',
                    code='provider_timeout',
                    http_status=503,
                ) from None

            except openai.APIConnectionError as e:
                if attempt == 0 and not started:
                    logger.warning(
                        'Cerebras connection error model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Cerebras 连接失败，请稍后重试',
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
                    supports_reasoning_effort=True,
                )

            except openai.AuthenticationError:
                raise ProviderError(
                    'Cerebras API Key 无效', code='provider_key_invalid'
                ) from None

            except openai.RateLimitError:
                raise ProviderError(
                    'Cerebras 请求频率或额度已受限，请稍后重试',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None

            except openai.APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUS and attempt == 0:
                    logger.warning(
                        'Cerebras status=%s model=%s attempt=%d; retrying',
                        e.status_code, self.MODEL, attempt + 1,
                    )
                    continue
                logger.warning(
                    'Cerebras request failed status=%s model=%s operation=complete',
                    e.status_code, self.MODEL,
                )
                if e.status_code == 402:
                    raise ProviderError(
                        'Cerebras 账户当前没有可用额度，请在服务商控制台检查试用额度或计费设置',
                        code='provider_payment_required',
                        http_status=402,
                    ) from None
                if e.status_code == 404:
                    raise ProviderError(
                        'Cerebras 当前配置的模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'Cerebras 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None

            except openai.APITimeoutError as e:
                if attempt == 0:
                    logger.warning(
                        'Cerebras timeout model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Cerebras 响应超时，请稍后重试',
                    code='provider_timeout',
                    http_status=503,
                ) from None

            except openai.APIConnectionError as e:
                if attempt == 0:
                    logger.warning(
                        'Cerebras connection error model=%s attempt=%d; retrying',
                        self.MODEL, attempt + 1,
                    )
                    continue
                raise ProviderError(
                    'Cerebras 连接失败，请稍后重试',
                    code='provider_connection_failed',
                    http_status=503,
                ) from None
