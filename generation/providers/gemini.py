import time
import logging
from google import genai
from google.genai import types
from google.genai.errors import ServerError
from .base import BaseProvider, UsageInfo, CompleteResult

logger = logging.getLogger(__name__)

_RETRY_CODES = {503, 429}
_CHUNK_SIZE = 20  # 每次推送的字符数，控制前端显示速度


class GeminiProvider(BaseProvider):
    MODEL = 'gemini-2.5-flash'

    def stream(self, system_prompt: str, user_prompt: str):
        """
        内部用非流式接口获取完整内容（带重试），
        拿到完整结果后逐块推给前端，模拟流式效果。
        优点：避免 Gemini 503 截断；前端体验不变。
        缺点：首字延迟略长（需等完整内容生成完）。
        """
        client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=4000,
        )

        last_error = None
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=self.MODEL,
                    contents=user_prompt,
                    config=config,
                )
                full_text = response.text or ''
                usage = response.usage_metadata
                break
            except ServerError as e:
                if e.code in _RETRY_CODES:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(
                        'Gemini %s on attempt %d, retrying in %ds: %s',
                        e.code, attempt + 1, wait, e,
                    )
                    time.sleep(wait)
                    last_error = e
                else:
                    raise
        else:
            raise last_error

        # 拿到完整内容后，分块推给前端模拟流式
        for i in range(0, len(full_text), _CHUNK_SIZE):
            yield full_text[i:i + _CHUNK_SIZE]

        yield UsageInfo(
            model=self.MODEL,
            prompt_tokens=usage.prompt_token_count if usage else 0,
            completion_tokens=usage.candidates_token_count if usage else 0,
        )

    def complete(self, system_prompt: str, user_prompt: str) -> CompleteResult:
        client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=2000,
        )

        last_error = None
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=self.MODEL,
                    contents=user_prompt,
                    config=config,
                )
                usage = response.usage_metadata
                return CompleteResult(
                    text=response.text,
                    model=self.MODEL,
                    prompt_tokens=usage.prompt_token_count if usage else 0,
                    completion_tokens=usage.candidates_token_count if usage else 0,
                )
            except ServerError as e:
                if e.code in _RETRY_CODES:
                    wait = 2 ** attempt
                    logger.warning(
                        'Gemini %s on attempt %d, retrying in %ds: %s',
                        e.code, attempt + 1, wait, e,
                    )
                    time.sleep(wait)
                    last_error = e
                else:
                    raise

        raise last_error