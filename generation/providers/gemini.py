from google import genai
from google.genai import types
from .base import BaseProvider, UsageInfo


class GeminiProvider(BaseProvider):
    MODEL = 'gemini-2.5-flash'

    def stream(self, system_prompt: str, user_prompt: str):
        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content_stream(
            model=self.MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=2000,
            ),
        )
        last_usage = None
        for chunk in response:
            if chunk.text:
                yield chunk.text
            # usage_metadata 在每个 chunk 上都存在，取最后一个（累计值最全）
            if chunk.usage_metadata:
                last_usage = chunk.usage_metadata

        yield UsageInfo(
            model=self.MODEL,
            prompt_tokens=last_usage.prompt_token_count if last_usage else 0,
            completion_tokens=last_usage.candidates_token_count if last_usage else 0,
        )