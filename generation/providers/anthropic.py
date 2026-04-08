import anthropic as anthropic_sdk
from .base import BaseProvider, UsageInfo, CompleteResult


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
            final = s.get_final_message()

        yield UsageInfo(
            model=self.MODEL,
            prompt_tokens=final.usage.input_tokens,
            completion_tokens=final.usage.output_tokens,
        )

    def complete(self, system_prompt: str, user_prompt: str) -> CompleteResult:
        client = anthropic_sdk.Anthropic(api_key=self.api_key)
        msg = client.messages.create(
            model=self.MODEL,
            max_tokens=1000,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        return CompleteResult(
            text=msg.content[0].text,
            model=self.MODEL,
            prompt_tokens=msg.usage.input_tokens,
            completion_tokens=msg.usage.output_tokens,
        )