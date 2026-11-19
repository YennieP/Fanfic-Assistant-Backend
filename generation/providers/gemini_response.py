"""Fixed Gemini response policy; no feature JSON parsing or raw-content logs."""
import re

from generation.diagnostics import finish_reason_for_log
from .base import ProviderError, ResponseMetadata, completion_finish_reason


def _reported_version(value, requested_model, previous):
    if value in (None, ''):
        return previous
    # Stable family plus numeric revisions; never accept latest/preview routes
    # or reflect arbitrary server values into diagnostics or Key history.
    pattern = re.escape(requested_model) + r'(?:-[0-9]+)*'
    if (not isinstance(value, str) or len(value) > 64
            or re.fullmatch(pattern, value) is None
            or (previous is not None and value != previous)):
        raise ProviderError(
            'Gemini 返回的模型版本与当前配置不一致，请检查配置后重试',
            code='provider_model_unavailable', http_status=503,
        )
    return value


def response_metadata(response, requested_model, previous=None):
    """Merge latest reported fields, never sum cumulative stream usage."""
    previous = previous or ResponseMetadata()
    version = _reported_version(
        getattr(response, 'model_version', None), requested_model,
        previous.model_version,
    )
    candidates = getattr(response, 'candidates', None)
    candidate = candidates[0] if candidates else None
    raw_finish = getattr(candidate, 'finish_reason', None)
    finish = completion_finish_reason(raw_finish) if raw_finish is not None else previous.finish_reason
    if finish == 'FINISH_REASON_UNSPECIFIED' and previous.finish_reason is not None:
        # Intermediate/default placeholders do not invalidate an already
        # reported terminal reason in a later metadata-only chunk.
        finish = previous.finish_reason
    usage = getattr(response, 'usage_metadata', None)
    values = {}
    for field_name, sdk_name in [
        ('prompt_tokens', 'prompt_token_count'),
        ('candidate_tokens', 'candidates_token_count'),
        ('thoughts_tokens', 'thoughts_token_count'),
        ('total_tokens', 'total_token_count'),
    ]:
        raw = getattr(usage, sdk_name, None)
        if raw is None:
            values[field_name] = getattr(previous, field_name)
        else:
            # Legacy DB counts are signed 32-bit integers. Invalid telemetry
            # stays unknown, rather than becoming zero or breaking a result.
            values[field_name] = raw if type(raw) is int and 0 <= raw <= 2**31-1 else None
    return ResponseMetadata(model_version=version, finish_reason=finish, **values)


def require_usable_response(response, metadata, *, intermediate=False):
    """Reject reported refusal/truncation; absent termination stays unknown."""
    feedback = getattr(response, 'prompt_feedback', None)
    blocked = completion_finish_reason(getattr(feedback, 'block_reason', None))
    if blocked not in (None, 'BLOCKED_REASON_UNSPECIFIED'):
        raise unusable_response()
    if intermediate and metadata.finish_reason == 'FINISH_REASON_UNSPECIFIED':
        return
    if metadata.finish_reason is not None and metadata.finish_reason.upper() != 'STOP':
        raise unusable_response()


def unusable_response():
    return ProviderError(
        'Gemini 未返回完整可用的正文，请调整请求后重试',
        code='generation_failed', http_status=500,
    )


def log_response_metadata(logger, requested_model, metadata):
    # Keep the original neutral unattributed definition separate from actual
    # thinking counts. Only compute precise residuals when all inputs exist.
    p, c, t, total = (
        metadata.prompt_tokens, metadata.candidate_tokens,
        metadata.thoughts_tokens, metadata.total_tokens,
    )
    unattributed = total-p-c if None not in (p, c, total) and total >= p+c else None
    unaccounted = total-p-c-t if None not in (p, c, t, total) and total >= p+c+t else None
    logger.info(
        'Gemini response metadata: requested_model=%s model_version=%s '
        'finish_reason=%s reported_prompt_tokens=%s reported_candidate_tokens=%s '
        'thoughts_tokens=%s reported_total_tokens=%s '
        'reported_unattributed_tokens=%s unaccounted_tokens=%s',
        requested_model, metadata.model_version or 'UNKNOWN',
        finish_reason_for_log(metadata.finish_reason), p, c, t, total,
        unattributed, unaccounted,
    )
