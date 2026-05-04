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
    message 保留中文描述，仅用于服务端日志，不直接展示给用户。

    当前错误码：
      provider_key_invalid   — API Key 无效（401）
      provider_rate_limit    — 请求频率超限（Anthropic 429）
      provider_quota_daily   — 今日配额耗尽（Groq / OpenRouter 429）
      provider_quota_monthly — 本月配额耗尽（Cerebras 429）
      generation_failed      — 非预期技术异常（兜底，由 views.py 使用）

    与普通 Exception 的区别：
      ProviderError → views.py 用 logger.warning，SSE error 携带 code
      Exception     → views.py 用 logger.exception，SSE error 使用 generation_failed 兜底
    """

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


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
        max_tokens 可按场景调整：标签推断用默认 2000，文章切割用 4000+。
        """
        ...