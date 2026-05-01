from groq import Groq
from .base import BaseProvider, UsageInfo, CompleteResult


class GroqProvider(BaseProvider):
    MODEL = 'llama-3.3-70b-versatile'

    def stream(self, system_prompt: str, user_prompt: str):
        client = Groq(api_key=self.api_key)
        stream = client.chat.completions.create(
            model=self.MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            max_tokens=4000,
            stream=True,
        )
        prompt_tokens = 0
        completion_tokens = 0
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
            if chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens
                completion_tokens = chunk.usage.completion_tokens

        yield UsageInfo(
            model=self.MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def complete(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 2000
    ) -> CompleteResult:
        client = Groq(api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            max_tokens=max_tokens,
        )
        usage = response.usage
        return CompleteResult(
            text=response.choices[0].message.content,
            model=self.MODEL,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )