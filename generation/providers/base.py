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


class BaseProvider(ABC):
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