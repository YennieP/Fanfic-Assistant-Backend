from google import genai
from google.genai import types
from .base import BaseProvider


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
        for chunk in response:
            if chunk.text:
                yield chunk.text