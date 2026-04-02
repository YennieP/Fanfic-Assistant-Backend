from abc import ABC, abstractmethod
from typing import Generator


class BaseProvider(ABC):
    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    def stream(self, system_prompt: str, user_prompt: str) -> Generator[str, None, None]:
        """逐 chunk yield 生成文字"""
        ...