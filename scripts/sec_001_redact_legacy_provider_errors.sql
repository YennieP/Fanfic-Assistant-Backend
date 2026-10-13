-- SEC-001 one-time production data repair. REVIEW ONLY until separately authorized.
--
-- Safety boundary:
--   * This file never selects or returns an original error_message.
--   * The DO block is the only data-changing statement. PostgreSQL executes it
--     atomically even when the database console uses autocommit.
--   * The cutoff is PR #26's merge time (2026-10-08 14:29:16 PDT), after the
--     newest candidate observed by the read-only audit and before safe writes
--     became the default-branch contract.
--   * All counts and feature/model characteristics must still match the
--     read-only 2026-10-13 audit. Any mismatch raises an exception and rolls
--     back the entire DO statement.
--   * Execute only after this exact revision is merged and the user separately
--     authorizes the production write.

DO $sec_001$
DECLARE
    v_cutoff CONSTANT timestamptz := TIMESTAMPTZ '2026-10-08 21:29:16+00';
    v_redacted CONSTANT text := 'historical_provider_error_redacted';
    v_safe_values CONSTANT text[] := ARRAY[
        -- Current and historical persistence-safe codes.
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
        v_redacted,

        -- Fixed, user-safe messages persisted before error-code storage.
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
        'OpenRouter 响应超时，请稍后重试'
    ];
    v_candidate_ids uuid[];
    v_candidate_count integer;
    v_distinct_text_count integer;
    v_character_generate_count integer;
    v_consistency_check_count integer;
    v_segment_article_count integer;
    v_nonempty_model_count integer;
    v_existing_redacted_count integer;
    v_updated_count integer;
    v_remaining_count integer;
BEGIN
    SELECT
        COALESCE(array_agg(id ORDER BY id), ARRAY[]::uuid[]),
        COUNT(*)::integer,
        COUNT(DISTINCT error_message)::integer,
        COUNT(*) FILTER (WHERE feature = 'character_generate')::integer,
        COUNT(*) FILTER (WHERE feature = 'consistency_check')::integer,
        COUNT(*) FILTER (WHERE feature = 'segment_article')::integer,
        COUNT(*) FILTER (WHERE COALESCE(model, '') <> '')::integer
    INTO
        v_candidate_ids,
        v_candidate_count,
        v_distinct_text_count,
        v_character_generate_count,
        v_consistency_check_count,
        v_segment_article_count,
        v_nonempty_model_count
    FROM logs_llmcalllog
    WHERE status = 'error'
      AND created_at < v_cutoff
      AND NULLIF(BTRIM(error_message), '') IS NOT NULL
      AND NOT (error_message = ANY (v_safe_values));

    SELECT COUNT(*)::integer
      INTO v_existing_redacted_count
      FROM logs_llmcalllog
     WHERE error_message = v_redacted;

    IF v_candidate_count <> 22
       OR v_distinct_text_count <> 10
       OR v_character_generate_count <> 15
       OR v_consistency_check_count <> 4
       OR v_segment_article_count <> 3
       OR v_nonempty_model_count <> 0
       OR v_existing_redacted_count <> 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'SEC-001 aborted: candidates=%s distinct=%s features=%s/%s/%s nonempty_model=%s existing_redacted=%s',
                v_candidate_count,
                v_distinct_text_count,
                v_character_generate_count,
                v_consistency_check_count,
                v_segment_article_count,
                v_nonempty_model_count,
                v_existing_redacted_count
            );
    END IF;

    UPDATE logs_llmcalllog
       SET error_message = v_redacted
     WHERE id = ANY (v_candidate_ids)
       AND status = 'error'
       AND created_at < v_cutoff
       AND NULLIF(BTRIM(error_message), '') IS NOT NULL
       AND NOT (error_message = ANY (v_safe_values));

    GET DIAGNOSTICS v_updated_count = ROW_COUNT;

    IF v_updated_count <> 22 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'SEC-001 aborted: expected 22 updates, got %s',
                v_updated_count
            );
    END IF;

    SELECT COUNT(*)::integer
      INTO v_remaining_count
      FROM logs_llmcalllog
     WHERE status = 'error'
       AND created_at < v_cutoff
       AND NULLIF(BTRIM(error_message), '') IS NOT NULL
       AND NOT (error_message = ANY (v_safe_values));

    IF v_remaining_count <> 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'SEC-001 aborted: %s historical candidates remain',
                v_remaining_count
            );
    END IF;

    RAISE NOTICE 'SEC-001 redacted % legacy provider errors', v_updated_count;
END
$sec_001$;

-- Read-only post-write verification. It returns counts only, never message text.
SELECT
    COUNT(*) FILTER (
        WHERE error_message = 'historical_provider_error_redacted'
    ) AS redacted_count,
    COUNT(*) FILTER (
        WHERE status = 'error'
          AND created_at < TIMESTAMPTZ '2026-10-08 21:29:16+00'
          AND NULLIF(BTRIM(error_message), '') IS NOT NULL
          AND NOT (
              error_message = ANY (ARRAY[
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
                  'OpenRouter 响应超时，请稍后重试'
              ]::text[])
          )
    ) AS remaining_candidate_count
FROM logs_llmcalllog;
