"""Bounded application diagnostics; never format model/prompt/key values."""
import logging
import re


_FINISH_LABELS = frozenset({
    'STOP', 'END_TURN', 'STOP_SEQUENCE', 'MAX_TOKENS', 'LENGTH', 'SAFETY',
    'RECITATION', 'CONTENT_FILTER', 'REFUSAL', 'TOOL_CALLS', 'OTHER',
    'UNKNOWN', 'FINISH_REASON_UNSPECIFIED', 'BLOCKLIST', 'PROHIBITED_CONTENT',
    'SPII', 'MALFORMED_FUNCTION_CALL', 'UNEXPECTED_TOOL_CALL', 'IMAGE_SAFETY',
    'PAUSE_TURN', 'ERROR', 'CONTINUATION',
})
_GEMINI_ENUM_WARNING = re.compile(
    r'^[^\r\n]*[/\\]google[/\\]genai[/\\]_common\.py:\d+: UserWarning: ',
)


def response_character_count(value):
    """Return character count, preserving unavailable/type-invalid as unknown."""
    return len(value) if isinstance(value, str) else None


def finish_reason_for_log(reason):
    """Log only known termination labels, while the business result stays raw."""
    try:
        value = getattr(reason, 'value', reason)
    except Exception:
        return 'UNKNOWN'
    if not isinstance(value, str):
        return 'UNKNOWN'
    label = value.upper()
    return label if label in _FINISH_LABELS else 'UNKNOWN'


class SafeGeminiWarningFilter(logging.Filter):
    """Remove untrusted enum values from the SDK's Python warning output.

    Python warning routing is enabled once during Django startup, not in a
    per-request catch_warnings context. Other warnings and all levels remain.
    """
    def filter(self, record):
        if record.name != 'py.warnings':
            return True
        message = record.getMessage()
        if _GEMINI_ENUM_WARNING.match(message) and ' is not a valid ' in message:
            record.msg = 'Gemini SDK warning: unknown_enum_value'
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True
