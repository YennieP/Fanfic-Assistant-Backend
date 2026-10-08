import time
import logging
from google import genai
from google.genai import types
from google.genai.errors import ServerError, ClientError
from .base import BaseProvider, UsageInfo, CompleteResult, ProviderError

logger = logging.getLogger(__name__)

_RETRY_CODES = {503, 429}


class GeminiProvider(BaseProvider):
    MODEL = 'gemini-2.5-flash'
    supports_video = True
    supports_embedding = True

    def stream(self, system_prompt: str, user_prompt: str):
        """
        真实 streaming 实现，重试策略限定于「建立连接 + 收到第一个 chunk」阶段。

        设计取舍：
        - 一旦开始 yield chunk，不再捕获 ServerError——因为此时前端已收到部分文字，
          重试会导致内容重复。流式传输中途断流由 views.py 的 except 处理为 SSE error 事件。
        - 实践中 503/429 几乎只发生在请求建立阶段，流式中途断流概率极低。
        - 与原缓冲方案（7 次全程重试）的差异：仅在建立连接阶段重试，UX 显著改善
          （用户 1-2 秒内看到第一个字），接受中途断流时无法续传的代价。
        详见 architecture.md「Gemini Streaming 设计」章节。
        """
        client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=4000,
        )

        # ── 阶段一：建立连接 + 获取第一个 chunk，失败可重试 ─────────────────
        last_error = None
        stream_iter = None
        usage = None

        for attempt in range(7):
            try:
                stream_iter = client.models.generate_content_stream(
                    model=self.MODEL,
                    contents=user_prompt,
                    config=config,
                )
                # 调用 next() 触发真正的网络请求，确认连接成功
                first_chunk = next(stream_iter)
                if first_chunk.text:
                    yield first_chunk.text
                if first_chunk.usage_metadata:
                    usage = first_chunk.usage_metadata
                break  # 连接成功，退出重试循环
            except StopIteration:
                # 模型返回了空响应，不重试
                break
            except ClientError as e:
                # 4xx 错误不重试，按状态转为安全的业务错误。
                if e.code == 401:
                    raise ProviderError(
                        'Gemini API Key 无效', code='provider_key_invalid'
                    ) from None
                if e.code == 429:
                    raise ProviderError(
                        'Gemini 请求频率或额度已受限，请稍后重试',
                        code='provider_rate_limit',
                        http_status=429,
                    ) from None
                if e.code == 404:
                    raise ProviderError(
                        'Gemini 当前配置的模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'Gemini 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None
            except ServerError as e:
                if e.code in _RETRY_CODES:
                    wait = min(2 ** attempt, 32)
                    logger.warning(
                        'Gemini status=%s model=%s attempt=%d; retrying in %ds',
                        e.code, self.MODEL, attempt + 1, wait,
                    )
                    time.sleep(wait)
                    last_error = e
                    stream_iter = None
                else:
                    raise ProviderError(
                        'Gemini 服务暂时不可用，请稍后重试',
                        code='provider_temporarily_unavailable',
                        http_status=503,
                    ) from None
        else:
            # 7 次重试全部失败
            if last_error and getattr(last_error, 'code', None) == 429:
                raise ProviderError(
                    'Gemini 请求频率或额度已受限，请稍后重试',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None
            raise ProviderError(
                'Gemini 服务暂时不可用，请稍后再试',
                code='provider_temporarily_unavailable',
                http_status=503,
            ) from None

        if stream_iter is None:
            # 7 次重试全部失败（last_error 已在上方 raise，此处不可达）
            return

        # ── 阶段二：消费剩余 chunk，不再重试 ────────────────────────────────
        for chunk in stream_iter:
            if chunk.text:
                yield chunk.text
            if chunk.usage_metadata and chunk.usage_metadata.candidates_token_count:
                usage = chunk.usage_metadata

        yield UsageInfo(
            model=self.MODEL,
            prompt_tokens=usage.prompt_token_count if usage else 0,
            completion_tokens=usage.candidates_token_count if usage else 0,
        )

    def complete(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 2000
    ) -> CompleteResult:
        client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            # Every synchronous completion call site currently expects a JSON
            # object (segmentation, tagging, suggestions, translation, judge).
            # Ask Gemini to enforce JSON syntax instead of relying on prompt
            # wording alone.
            response_mime_type='application/json',
        )

        last_error = None
        for attempt in range(5):
            try:
                response = client.models.generate_content(
                    model=self.MODEL,
                    contents=user_prompt,
                    config=config,
                )
                usage = response.usage_metadata
                candidate = response.candidates[0] if response.candidates else None
                raw_finish_reason = getattr(candidate, 'finish_reason', None)
                finish_reason = (
                    getattr(raw_finish_reason, 'value', None)
                    or str(raw_finish_reason or 'UNKNOWN')
                )
                prompt_tokens = usage.prompt_token_count if usage else 0
                completion_tokens = usage.candidates_token_count if usage else 0
                total_tokens = usage.total_token_count if usage else 0
                prompt_tokens = prompt_tokens or 0
                completion_tokens = completion_tokens or 0
                total_tokens = total_tokens or 0
                unattributed_tokens = max(
                    0, total_tokens - prompt_tokens - completion_tokens,
                )
                # Capacity telemetry only: never log prompts, model output, or
                # API keys. "unattributed" is intentionally neutral because
                # google-genai 1.0.0 does not expose a thoughts_token_count.
                logger.info(
                    'Gemini complete capacity: model=%s finish_reason=%s '
                    'prompt_tokens=%d completion_tokens=%d total_tokens=%d '
                    'unattributed_tokens=%d max_output_tokens=%d',
                    self.MODEL,
                    finish_reason,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    unattributed_tokens,
                    max_tokens,
                )
                return CompleteResult(
                    text=response.text,
                    model=self.MODEL,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            except ClientError as e:
                if e.code == 401:
                    raise ProviderError(
                        'Gemini API Key 无效', code='provider_key_invalid'
                    ) from None
                if e.code == 429:
                    raise ProviderError(
                        'Gemini 请求频率或额度已受限，请稍后重试',
                        code='provider_rate_limit',
                        http_status=429,
                    ) from None
                if e.code == 404:
                    raise ProviderError(
                        'Gemini 当前配置的模型不可用',
                        code='provider_model_unavailable',
                        http_status=503,
                    ) from None
                raise ProviderError(
                    'Gemini 请求失败，请稍后重试',
                    code='provider_temporarily_unavailable',
                    http_status=503,
                ) from None
            except ServerError as e:
                if e.code in _RETRY_CODES:
                    wait = min(2 ** attempt, 4)
                    logger.warning(
                        'Gemini status=%s model=%s attempt=%d; retrying in %ds',
                        e.code, self.MODEL, attempt + 1, wait,
                    )
                    time.sleep(wait)
                    last_error = e
                else:
                    raise ProviderError(
                        'Gemini 服务暂时不可用，请稍后重试',
                        code='provider_temporarily_unavailable',
                        http_status=503,
                    ) from None

        if last_error and getattr(last_error, 'code', None) == 429:
            raise ProviderError(
                'Gemini 请求频率或额度已受限，请稍后重试',
                code='provider_rate_limit',
                http_status=429,
            ) from None
        raise ProviderError(
            'Gemini 服务暂时不可用，请稍后再试',
            code='provider_temporarily_unavailable',
            http_status=503,
        ) from None
