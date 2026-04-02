import anthropic as anthropic_sdk
from .base import BaseProvider


class AnthropicProvider(BaseProvider):
    MODEL = 'claude-sonnet-4-20250514'

    def stream(self, system_prompt: str, user_prompt: str):
        client = anthropic_sdk.Anthropic(api_key=self.api_key)
        with client.messages.stream(
            model=self.MODEL,
            max_tokens=2000,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        ) as s:
            for text in s.text_stream:
                yield text