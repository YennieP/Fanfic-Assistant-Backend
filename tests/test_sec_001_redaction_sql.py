import re
from pathlib import Path

import sqlparse


ROOT = Path(__file__).resolve().parent.parent
SQL_PATH = ROOT / 'scripts' / 'sec_001_redact_legacy_provider_errors.sql'

EXPECTED_SAFE_VALUES = {
    'generation_failed',
    'no_api_key',
    'provider_connection_failed',
    'provider_key_invalid',
    'provider_model_unavailable',
    'provider_payment_required',
    'provider_quota_daily',
    'provider_quota_monthly',
    'provider_rate_limit',
    'provider_temporarily_unavailable',
    'provider_timeout',
    'stream interrupted before completion',
    'historical_provider_error_redacted',
    'Anthropic API Key 无效',
    'Anthropic API Key 无效，请在设置页重新配置',
    'Anthropic 请求频率超限',
    'Anthropic 请求频率超限，请稍后再试',
    'Cerebras API Key 无效',
    'Cerebras API Key 无效，请在设置页重新配置',
    'Cerebras 响应超时，请稍后重试',
    'Cerebras 本月免费额度（~10 亿 tokens）已用完',
    'Cerebras 本月免费额度（~10 亿 tokens）已用完，请下月再试',
    'Gemini API Key 无效',
    'Gemini 免费额度暂时耗尽，请稍后再试',
    'Gemini 服务暂时不可用，请稍后再试',
    'Groq API Key 无效',
    'Groq API Key 无效，请在设置页重新配置',
    'Groq 今日免费额度（100K tokens）已用完',
    'Groq 今日免费额度（100K tokens）已用完，请明天再试',
    'OpenRouter API Key 无效',
    'OpenRouter API Key 无效，请在设置页重新配置',
    'OpenRouter 今日免费请求次数（200 次）已用完',
    'OpenRouter 今日免费请求次数（200 次）已用完，请明天再试',
    'OpenRouter 响应超时，请稍后重试',
}


def _sql() -> str:
    return SQL_PATH.read_text(encoding='utf-8')


def _array_values(sql: str, marker: str) -> set[str]:
    start = sql.index(marker) + len(marker)
    array_start = sql.index('ARRAY[', start) + len('ARRAY[')
    array_end = sql.index(']', array_start)
    array_body = sql[array_start:array_end]
    values = set(re.findall(r"'((?:[^']|'')*)'", array_body))
    if 'v_redacted' in array_body:
        values.add('historical_provider_error_redacted')
    return values


def test_sec_001_sql_has_one_atomic_write_statement_and_read_only_check():
    statements = sqlparse.split(_sql())

    assert len(statements) == 2
    assert statements[0].lstrip().startswith('-- SEC-001')
    assert 'DO $sec_001$' in statements[0]
    assert statements[1].lstrip().startswith('-- Read-only post-write verification')
    assert re.search(r'\bSELECT\b', statements[1], re.IGNORECASE)


def test_sec_001_write_is_narrow_and_fails_closed():
    write_statement = sqlparse.split(_sql())[0]
    executable = sqlparse.format(write_statement, strip_comments=True)

    assert executable.upper().count('UPDATE LOGS_LLMCALLLOG') == 1
    assert re.search(
        r'UPDATE\s+logs_llmcalllog\s+'
        r'SET\s+error_message\s*=\s*v_redacted\s+'
        r'WHERE\s+id\s*=\s*ANY\s*\(v_candidate_ids\)',
        executable,
        re.IGNORECASE,
    )
    assert 'RETURNING' not in executable.upper()
    assert not re.search(
        r'\b(DELETE|INSERT|TRUNCATE|ALTER|DROP|CREATE)\b',
        executable,
        re.IGNORECASE,
    )
    assert "v_cutoff CONSTANT timestamptz := TIMESTAMPTZ '2026-10-08 21:29:16+00'" in executable
    assert 'v_candidate_count <> 22' in executable
    assert 'v_distinct_text_count <> 10' in executable
    assert 'v_character_generate_count <> 15' in executable
    assert 'v_consistency_check_count <> 4' in executable
    assert 'v_segment_article_count <> 3' in executable
    assert 'v_nonempty_model_count <> 0' in executable
    assert 'v_existing_redacted_count <> 0' in executable
    assert 'v_updated_count <> 22' in executable
    assert 'v_remaining_count <> 0' in executable
    assert executable.upper().count('RAISE EXCEPTION') == 3
    assert 'GET DIAGNOSTICS v_updated_count = ROW_COUNT' in executable


def test_sec_001_safe_value_sets_are_complete_and_identical():
    sql = _sql()
    write_values = _array_values(sql, 'v_safe_values CONSTANT text[] := ')
    verification_values = _array_values(
        sql,
        '-- Read-only post-write verification.',
    )

    assert write_values == EXPECTED_SAFE_VALUES
    assert verification_values == EXPECTED_SAFE_VALUES


def test_sec_001_never_selects_original_error_text():
    sql = sqlparse.format(_sql(), strip_comments=True)

    assert not re.search(r'\bSELECT\s+error_message\b', sql, re.IGNORECASE)
    assert not re.search(r'\bRETURNING\b', sql, re.IGNORECASE)
