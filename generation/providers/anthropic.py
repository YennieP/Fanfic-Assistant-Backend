import anthropic as anthropic_sdk
from .base import BaseProvider, UsageInfo


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
            # with 块结束前取用量，get_final_message() 在流关闭后仍可调用
            final = s.get_final_message()

        yield UsageInfo(
            model=self.MODEL,
            prompt_tokens=final.usage.input_tokens,
            completion_tokens=final.usage.output_tokens,
        )