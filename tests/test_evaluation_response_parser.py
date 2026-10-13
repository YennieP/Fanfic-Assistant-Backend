import pytest

from evaluation.views import _parse_judge_response


def test_parse_judge_response_accepts_standard_json():
    assert _parse_judge_response(
        '{"score": 8, "reasoning": "行为与核心价值观一致"}',
    ) == (8, '行为与核心价值观一致')


def test_parse_judge_response_accepts_markdown_json_fence():
    assert _parse_judge_response(
        '```json\n{"score": 7, "reasoning": "保持克制"}\n```',
    ) == (7, '保持克制')


def test_parse_judge_response_repairs_literal_newline_in_reasoning():
    assert _parse_judge_response(
        '{"score": 6, "reasoning": "第一行依据\n第二行依据"}',
    ) == (6, '第一行依据\n第二行依据')


def test_parse_judge_response_falls_back_for_unescaped_reasoning_quotes():
    assert _parse_judge_response(
        '{"score": 9, "reasoning": "她说"不会退让"，符合既有模式"}',
    ) == (9, '她说"不会退让"，符合既有模式')


def test_parse_judge_response_rejects_out_of_range_score():
    with pytest.raises(ValueError, match='score 11 out of range'):
        _parse_judge_response('{"score": 11, "reasoning": "invalid"}')
