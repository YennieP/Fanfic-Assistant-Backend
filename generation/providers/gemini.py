import time
import logging
import requests
import httpx
import httpx2
from google.genai import types
from google.genai.errors import ServerError, ClientError
from .base import (
    BaseProvider,
    CompleteResult,
    CompletionOptions,
    ProviderError,
    UsageInfo,
    ResponseMetadata,
    close_provider_resource,
    completion_finish_reason,
)
from .catalog import get_provider_definition
from .gemini_client import managed_gemini_client
from generation.diagnostics import finish_reason_for_log
from .gemini_response import (
    response_metadata, require_usable_response, unusable_response,
    log_response_metadata,
)

logger = logging.getLogger(__name__)

_RETRY_CODES = {503}
_STREAM_ATTEMPTS = 7
_COMPLETE_ATTEMPTS = 5
_THINKING_LEVELS = {
    None: types.ThinkingLevel.MINIMAL,
    'low': types.ThinkingLevel.MINIMAL,
    'medium': types.ThinkingLevel.MEDIUM,
    'high': types.ThinkingLevel.HIGH,
}
_TIMEOUT_ERRORS = (requests.Timeout, httpx.TimeoutException, httpx2.TimeoutException)
_TRANSPORT_ERRORS = (
    requests.ConnectionError, httpx.TransportError, httpx2.TransportError,
) + _TIMEOUT_ERRORS


def _client_error(error: ClientError) -> ProviderError:
    if error.code == 401:
        return ProviderError(
            'Gemini API Key 无效', code='provider_key_invalid',
        )
    if error.code == 402:
        return ProviderError(
            'Gemini 账户当前没有可用额度',
            code='provider_payment_required',
            http_status=402,
        )
    if error.code == 429:
        return ProviderError(
            'Gemini 请求频率或额度已受限，请稍后重试',
            code='provider_rate_limit',
            http_status=429,
        )
    if error.code == 404:
        return ProviderError(
            'Gemini 当前配置的模型不可用',
            code='provider_model_unavailable',
            http_status=503,
        )
    return ProviderError(
        'Gemini 请求失败，请稍后重试',
        code='provider_temporarily_unavailable',
        http_status=503,
    )


def _server_error(error: ServerError) -> ProviderError:
    if error.code == 429:
        return ProviderError(
            'Gemini 请求频率或额度已受限，请稍后重试',
            code='provider_rate_limit',
            http_status=429,
        )
    return ProviderError(
        'Gemini 服务暂时不可用，请稍后重试',
        code='provider_temporarily_unavailable',
        http_status=503,
    )


def _transport_error(error: Exception) -> ProviderError:
    if isinstance(error, _TIMEOUT_ERRORS):
        return ProviderError(
            'Gemini 响应超时，请稍后重试',
            code='provider_timeout',
            http_status=503,
        )
    return ProviderError(
        'Gemini 连接失败，请稍后重试',
        code='provider_connection_failed',
        http_status=503,
    )


class GeminiProvider(BaseProvider):
    _DEFINITION = get_provider_definition('gemini')
    MODEL = _DEFINITION.model
    supports_video = _DEFINITION.supports_video
    supports_embedding = _DEFINITION.supports_embedding

    def stream(self, system_prompt: str, user_prompt: str):
        """Retry only until any first SDK chunk, including metadata-only chunks."""
        with managed_gemini_client(self.api_key) as (client, release_responses):
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=4000,
                automatic_continuation=False,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.MINIMAL,
                    include_thoughts=False,
                ),
            )
            stream_iter = None
            metadata = ResponseMetadata()
            has_visible_text = False
            first_chunk = None
            try:
                for attempt in range(_STREAM_ATTEMPTS):
                    connected = False
                    try:
                        stream_iter = client.models.generate_content_stream(
                            model=self.MODEL, contents=user_prompt, config=config,
                        )
                        first_chunk = next(stream_iter)
                        connected = True
                        break
                    except StopIteration:
                        # An empty response is terminal, not a retry trigger.
                        connected = True
                        break
                    except ClientError as exc:
                        raise _client_error(exc) from None
                    except ServerError as exc:
                        if exc.code not in _RETRY_CODES:
                            raise _server_error(exc) from None
                        last_error = exc
                    except _TRANSPORT_ERRORS as exc:
                        last_error = exc
                    finally:
                        if not connected:
                            close_provider_resource(stream_iter)
                            stream_iter = None
                            release_responses()

                    if attempt == _STREAM_ATTEMPTS - 1:
                        if isinstance(last_error, _TRANSPORT_ERRORS):
                            raise _transport_error(last_error) from None
                        raise _server_error(last_error) from None
                    wait = min(2 ** attempt, 32)
                    logger.warning(
                        'Gemini retry model=%s attempt=%d status=%s transport=%s; '
                        'retrying in %ds',
                        self.MODEL, attempt + 1, getattr(last_error, 'code', None),
                        type(last_error).__name__, wait,
                    )
                    # Release the failed iterator before waiting or opening another.
                    time.sleep(wait)

                # Once even a metadata-only chunk is received, never replay the
                # stream. Mid-stream errors and GeneratorExit reach the finally.
                try:
                    if first_chunk is not None:
                        metadata = response_metadata(first_chunk, self.MODEL, metadata)
                        require_usable_response(first_chunk, metadata, intermediate=True)
                        text = first_chunk.text
                        if text:
                            has_visible_text = has_visible_text or bool(text.strip())
                            yield text
                    for chunk in stream_iter:
                        metadata = response_metadata(chunk, self.MODEL, metadata)
                        require_usable_response(chunk, metadata, intermediate=True)
                        text = chunk.text
                        if text:
                            has_visible_text = has_visible_text or bool(text.strip())
                            yield text
                    require_usable_response(None, metadata)
                    if not has_visible_text:
                        raise unusable_response()
                except ClientError as exc:
                    raise _client_error(exc) from None
                except ServerError as exc:
                    raise _server_error(exc) from None
                except _TRANSPORT_ERRORS as exc:
                    raise _transport_error(exc) from None
            finally:
                log_response_metadata(logger, self.MODEL, metadata)
                close_provider_resource(stream_iter)
                release_responses()

            yield UsageInfo(
                model=self.MODEL,
                prompt_tokens=metadata.prompt_tokens or 0,
                completion_tokens=metadata.candidate_tokens or 0,
                metadata=metadata,
            )

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        options: CompletionOptions,
    ) -> CompleteResult:
        with managed_gemini_client(self.api_key) as (client, _release_responses):
            config_kwargs = {
                'system_instruction': system_prompt,
                'max_output_tokens': options.max_tokens,
                'automatic_continuation': False,
                'thinking_config': types.ThinkingConfig(
                    thinking_level=_THINKING_LEVELS[options.reasoning_effort],
                    include_thoughts=False,
                ),
            }
            if options.response_format == 'json':
                config_kwargs['response_mime_type'] = 'application/json'
            config = types.GenerateContentConfig(**config_kwargs)

            last_error = None
            for attempt in range(_COMPLETE_ATTEMPTS):
                try:
                    response = client.models.generate_content(
                        model=self.MODEL,
                        contents=user_prompt,
                        config=config,
                    )
                    metadata = response_metadata(response, self.MODEL)
                    candidate = response.candidates[0] if response.candidates else None
                    raw_finish_reason = getattr(candidate, 'finish_reason', None)
                    finish_reason = finish_reason_for_log(raw_finish_reason)
                    prompt_tokens = metadata.prompt_tokens
                    completion_tokens = metadata.candidate_tokens
                    total_tokens = metadata.total_tokens
                    prompt_tokens = prompt_tokens or 0
                    completion_tokens = completion_tokens or 0
                    total_tokens = total_tokens or 0
                    unattributed_tokens = max(
                        0, total_tokens - prompt_tokens - completion_tokens,
                    )
                    # Capacity telemetry only: never log prompts, model output, or
                    # API keys. Preserve the existing neutral "unattributed"
                    # definition. Report actual thinking/unknown counts in the
                    # separate optional metadata; no DB field is redefined.
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
                        options.max_tokens,
                    )
                    log_response_metadata(logger, self.MODEL, metadata)
                    require_usable_response(response, metadata)
                    text = response.text
                    if not isinstance(text, str) or not text.strip():
                        raise unusable_response()
                    return CompleteResult(
                        text=text,
                        model=self.MODEL,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        finish_reason=completion_finish_reason(raw_finish_reason),
                        metadata=metadata,
                    )
                except ClientError as e:
                    raise _client_error(e) from None
                except ServerError as e:
                    if e.code in _RETRY_CODES:
                        last_error = e
                        if attempt < _COMPLETE_ATTEMPTS - 1:
                            wait = min(2 ** attempt, 4)
                            logger.warning(
                                'Gemini status=%s model=%s attempt=%d; '
                                'retrying in %ds',
                                e.code, self.MODEL, attempt + 1, wait,
                            )
                            time.sleep(wait)
                    else:
                        raise _server_error(e) from None
                except _TRANSPORT_ERRORS as e:
                    last_error = e
                    if attempt < _COMPLETE_ATTEMPTS - 1:
                        wait = min(2 ** attempt, 4)
                        logger.warning(
                            'Gemini transport=%s model=%s attempt=%d; '
                            'retrying in %ds',
                            type(e).__name__, self.MODEL, attempt + 1, wait,
                        )
                        time.sleep(wait)

            if last_error and getattr(last_error, 'code', None) == 429:
                raise ProviderError(
                    'Gemini 请求频率或额度已受限，请稍后重试',
                    code='provider_rate_limit',
                    http_status=429,
                ) from None
            if isinstance(last_error, _TRANSPORT_ERRORS):
                raise _transport_error(last_error) from None
            raise ProviderError(
                'Gemini 服务暂时不可用，请稍后再试',
                code='provider_temporarily_unavailable',
                http_status=503,
            ) from None
