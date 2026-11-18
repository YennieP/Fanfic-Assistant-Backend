"""Validate model-generated fragment tags before they can reach a write."""
import json
import re


FRAGMENT_TAG_FIELDS = frozenset({
    'scene_type', 'scene_privacy', 'initiative', 'emotion',
    'target_type', 'target_count', 'relationship_state', 'speech_intent',
})
SUCCESS_FINISH_REASONS = frozenset({'STOP', 'END_TURN', 'STOP_SEQUENCE'})


class TagInferenceOutputError(ValueError):
    """Safe validation failure; never embed the model response in the error."""


def _invalid():
    raise TagInferenceOutputError('Invalid tag inference output')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _reject_constant(_value):
    _invalid()


def _is_null(value):
    # Preserve the existing model-output compatibility for the string "null".
    return value is None or (isinstance(value, str) and value == 'null')


def _validate_scalar(value, allowed):
    if not _is_null(value) and (not isinstance(value, str) or value not in allowed):
        _invalid()


def parse_inferred_tags(text, *, finish_reason, taxonomy):
    """Accept one complete JSON object; empty objects explicitly mean no tags.

    Missing termination metadata stays unknown for older/custom providers. It
    does not waive JSON/schema validation. A reported non-success termination
    rejects even syntactically complete JSON, without retrying the provider.
    """
    if finish_reason is not None:
        if not isinstance(finish_reason, str) or finish_reason.upper() not in SUCCESS_FINISH_REASONS:
            _invalid()
    if not isinstance(text, str) or not text.strip():
        _invalid()
    text = text.strip()
    # Keep complete legacy fenced JSON, but never salvage a substring or repair
    # a truncated object using the segmentation parser's recovery heuristic.
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        raw = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError):
        _invalid()
    if not isinstance(raw, dict) or raw.keys() - FRAGMENT_TAG_FIELDS:
        _invalid()
    for field, value in raw.items():
        if field != 'emotion':
            _validate_scalar(value, taxonomy[field])
            continue
        if _is_null(value):
            continue
        if not isinstance(value, dict) or value.keys() - {'shared', 'intensity'}:
            _invalid()
        shared = value.get('shared')
        if isinstance(shared, list):
            for item in shared:
                _validate_scalar(item, taxonomy['emotion']['shared'])
        else:
            # _clean_tags still normalizes a legacy single emotion to a list.
            _validate_scalar(shared, taxonomy['emotion']['shared'])
        _validate_scalar(value.get('intensity'), taxonomy['emotion']['intensity'])
    return raw
