"""
Cerebras provider
免费额度约 10 亿 token/月，速度极快（~1000 TPS），OpenAI SDK 兼容
API Key 申请：https://cloud.cerebras.ai/

可用模型（按账号 tier 而定）：
  llama3.1-8b               ← 免费 tier 确认可用
  llama-3.3-70b             ← 需要确认账号权限
  llama-4-scout-17b-16e-instruct  ← 较新，性能更好
"""
import logging
from openai import OpenAI
from .base import BaseProvider, UsageInfo, CompleteResult

logger = logging.getLogger(__name__)


class CerebrasProvider(BaseProvider):
    supports_video     = False
    supports_embedding = False

    # 若 llama-3.3-70b 报 404，换成 llama3.1-8b 或 llama-4-scout-17b-16e-instruct
    MODEL = 'llama3.1-8b'

    def __init__(self, api_key: str):
        self.client = OpenAI(
            api_key=api_key,
            base_url='https://api.cerebras.ai/v1',
        )

    def stream(self, system_prompt: str, user_prompt: str):
        response = self.client.chat.completions.create(
            model=self.MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': user_prompt},
            ],
            stream=True,
            stream_options={'include_usage': True},
        )
        prompt_tokens     = 0
        completion_tokens = 0
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            if chunk.usage:
                prompt_tokens     = chunk.usage.prompt_tokens     or 0
                completion_tokens = chunk.usage.completion_tokens or 0
        yield UsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
    ) -> CompleteResult:
        response = self.client.chat.completions.create(
            model=self.MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': user_prompt},
            ],
            max_tokens=max_tokens,
        )
        text  = response.choices[0].message.content or ''
        usage = response.usage
        return CompleteResult(
            text=text,
            model=self.MODEL,
            prompt_tokens=usage.prompt_tokens         if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )