from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generator, Union


@dataclass
class UsageInfo:
    """
    Provider stream 末尾 yield 的 sentinel，携带本次调用的用量信息。
    消费方（decorator）负责过滤，view 层不会看到这个对象。
    """
    model: str
    prompt_tokens: int
    completion_tokens: int


@dataclass
class CompleteResult:
    """
    complete() 的返回类型，携带完整文本和用量信息。
    decorator 提取 text 返回给调用方，用量信息用于写 LlmCallLog。
    """
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class BaseProvider(ABC):
    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    def stream(
        self, system_prompt: str, user_prompt: str
    ) -> Generator[Union[str, UsageInfo], None, None]:
        """
        逐 chunk yield 生成文字（str），最后 yield 一个 UsageInfo sentinel。
        调用方通过 @log_llm_call 装饰后消费，UsageInfo 会被 decorator 过滤掉。
        """
        ...

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> CompleteResult:
        """
        非流式一次性调用，返回完整文本和用量信息。
        适用于 judge 评估、行为提取等不需要实时展示的场景。
        """
        ...