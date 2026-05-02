"""
OpenRouter provider — meta-llama/llama-3.3-70b-instruct:free
免费模型 200 req/天（充值 $10 后升至 1000 req/天），11+ 个免费模型可选
OpenAI SDK 兼容，需要 HTTP-Referer header
API Key 申请：https://openrouter.ai/keys
"""
import logging
from openai import OpenAI
from .base import BaseProvider, UsageInfo, CompleteResult

logger = logging.getLogger(__name__)


class OpenRouterProvider(BaseProvider):
    supports_video     = False
    supports_embedding = False

    MODEL = 'meta-llama/llama-3.3-70b-instruct:free'

    def __init__(self, api_key: str):
        self.client = OpenAI(
            api_key=api_key,
            base_url='https://openrouter.ai/api/v1',
            default_headers={
                'HTTP-Referer': 'https://fanfic-assistant-production.up.railway.app',
                'X-Title':      'Fanfic Assistant',
            },
        )

    def stream(self, system_prompt: str, user_prompt: str):
        response = self.client.chat.completions.create(
            model=self.MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': user_prompt},
            ],
            stream=True,
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
            prompt_tokens=usage.prompt_tokens         if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )