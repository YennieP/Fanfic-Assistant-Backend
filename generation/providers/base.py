from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generator, Union


@dataclass
class UsageInfo:
    model: str
    prompt_tokens: int
    completion_tokens: int


@dataclass
class CompleteResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class ProviderError(Exception):
    """
    用户可读的 provider 业务错误。

    code 字段为机器可读的错误类型标识，由前端通过 i18n 表映射为对应语言的展示文案。
    message 必须是固定的安全描述，可用于服务端日志和非 SSE `detail`；
    不得包含 vendor response body、request ID 或用户标识。

    当前错误码：
      provider_key_invalid   — API Key 无效（401）
      provider_payment_required      — 账户没有可用额度或计费权限（402）
      provider_rate_limit             — 请求频率或额度受限（429）
      provider_model_unavailable      — 当前配置的模型不存在或不可用（404）
      provider_timeout                — provider 超时
      provider_connection_failed      — provider 连接失败
      provider_temporarily_unavailable — 其他安全归一化的上游错误
      generation_failed               — 非预期技术异常（兜底，由 views.py 使用）

    与普通 Exception 的区别：
      ProviderError → views.py 用 logger.warning，SSE error 携带 code
      Exception     → 只记录异常类型，SSE error 使用 generation_failed 兜底
    """

    def __init__(self, message: str, code: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def safe_error_summary(exc: Exception) -> str:
    """Return a persistence-safe error label without vendor response bodies."""
    if isinstance(exc, ProviderError):
        return exc.code
    return 'generation_failed'


class BaseProvider(ABC):
    # Scaffold: 能力标志（architecture.md LLM Provider 设计）
    # 子类按实际能力覆盖，避免到处写 if provider == 'gemini' 的 hardcode 判断
    supports_video: bool = False
    supports_embedding: bool = False

    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    def stream(
        self, system_prompt: str, user_prompt: str
    ) -> Generator[Union[str, UsageInfo], None, None]:
        ...

    @abstractmethod
    def complete(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 2000
    ) -> CompleteResult:
        """
        非流式一次性调用。
        max_tokens 由调用场景显式调整；当前文章切割使用 4000。
        """
        ...
