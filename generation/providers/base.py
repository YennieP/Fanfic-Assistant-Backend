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
    用户可读的 provider 业务错误，message 可直接展示给用户。

    与技术性异常（网络超时、SDK 内部 bug）的区别：
    - ProviderError：预期内的业务错误（Key 无效、配额耗尽），logger.warning 即可
    - 其他 Exception：非预期技术异常，需要 logger.exception 记录完整 traceback

    views.py 的 except 块对两者分级处理，SSE error 事件的 message 均使用 str(e)，
    ProviderError 的 message 会直接呈现给用户，因此必须是中文可读文案。
    """
    pass


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