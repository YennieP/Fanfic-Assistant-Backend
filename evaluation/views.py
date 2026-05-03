import json
import re
import uuid
import logging
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from characters.models import BaseCard, AUMod
from logs.models import LlmCallLog
from logs.decorators import log_llm_call
from users.encryption import decrypt_key
from users.models import UserProviderKey
from generation.providers.anthropic import AnthropicProvider
from generation.providers.gemini import GeminiProvider
from generation.providers.groq import GroqProvider
from generation.providers.cerebras import CerebrasProvider
from generation.providers.openrouter import OpenRouterProvider
from .models import ConsistencyScore
from .prompt import build_judge_prompt

logger = logging.getLogger(__name__)


def _parse_judge_response(result_text: str) -> tuple[int, str]:
    """
    解析 judge 返回的 JSON，兼容以下情况：
    1. 正常 JSON
    2. JSON 外层包了 markdown 代码块（```json ... ```）
    3. reasoning 字段包含字面换行符（违反 JSON 规范）
    """
    clean = re.sub(r'```(?:json)?\s*|\s*```', '', result_text).strip()

    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        clean_escaped = re.sub(r'(?<!\\)\n', r'\\n', clean)
        result = json.loads(clean_escaped)

    score = int(result['score'])
    reasoning = str(result['reasoning']).replace('\\n', '\n').strip()

    if not 0 <= score <= 10:
        raise ValueError(f'score {score} out of range')

    return score, reasoning


def _get_provider(llm_config, request_user):
    """
    修复：统一使用 UserProviderKey 获取 API Key，
    与 generation/views.py 保持一致。
    原实现错误地访问已不存在的 llm_config.api_key_encrypted。
    同时支持 Groq（原实现遗漏）。
    """
    try:
        key_obj = UserProviderKey.objects.get(
            user=request_user, provider=llm_config.provider
        )
        api_key = decrypt_key(key_obj.api_key_encrypted)
    except UserProviderKey.DoesNotExist:
        raise ValueError(f'未找到 {llm_config.provider} 的 API Key，请在设置页保存')

    if llm_config.provider == 'anthropic':
        return AnthropicProvider(api_key)
    elif llm_config.provider == 'groq':
        return GroqProvider(api_key)
    elif llm_config.provider == 'cerebras':
        return CerebrasProvider(api_key)
    elif llm_config.provider == 'openrouter':
        return OpenRouterProvider(api_key)
    else:
        return GeminiProvider(api_key)


class EvaluateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        generation_id = request.data.get('generation_id')
        generated_text = (request.data.get('generated_text') or '').strip()
        character_id = request.data.get('character_id')
        au_mod_id = request.data.get('au_mod_id')

        if not all([generation_id, generated_text, character_id]):
            return Response({'error': '缺少必填参数'}, status=400)

        try:
            generation_log = LlmCallLog.objects.get(
                generation_id=generation_id, user=request.user
            )
        except LlmCallLog.DoesNotExist:
            return Response({'error': '生成记录不存在'}, status=404)

        try:
            character = BaseCard.objects.get(id=character_id, owner=request.user)
        except BaseCard.DoesNotExist:
            return Response({'error': '角色不存在'}, status=404)

        au_mod = None
        if au_mod_id:
            try:
                au_mod = AUMod.objects.get(id=au_mod_id, character=character)
            except AUMod.DoesNotExist:
                pass

        try:
            llm_config = request.user.llm_config
            provider = _get_provider(llm_config, request.user)
        except (Exception,) as e:
            return Response({'error': str(e)}, status=400)

        system_prompt, user_prompt = build_judge_prompt(character, au_mod, generated_text)

        judge_id = uuid.uuid4()

        @log_llm_call(feature='consistency_check', sync=True)
        def _call_judge(user=None, generation_id=None):
            return provider.complete(system_prompt, user_prompt)

        try:
            result_text = _call_judge(user=request.user, generation_id=judge_id)
        except Exception as e:
            logger.exception('Judge LLM call failed')
            return Response({'error': f'评估调用失败：{str(e)}'}, status=500)

        try:
            score, reasoning = _parse_judge_response(result_text)
        except Exception:
            logger.error('Judge returned invalid response: %s', result_text)
            return Response({'error': '评估结果格式异常，请重试'}, status=500)

        judge_log = None
        try:
            judge_log = LlmCallLog.objects.get(generation_id=judge_id)
        except LlmCallLog.DoesNotExist:
            logger.warning('judge_call_log not found for judge_id=%s', judge_id)

        score_obj = ConsistencyScore.objects.create(
            user=request.user,
            character=character,
            au_mod=au_mod,
            generation_log=generation_log,
            judge_call_log=judge_log,
            generated_text=generated_text,
            score=score,
            judge_reasoning=reasoning,
            judge_model=provider.MODEL,
        )

        return Response({
            'score': score,
            'reasoning': reasoning,
            'evaluation_id': str(score_obj.id),
        })


class RateView(APIView):
    """
    PATCH /api/evaluation/score/<uuid:pk>/rate/

    用户提交人工评分。有用户评分时，最终分数 = 用户分 × 0.7 + LLM 分 × 0.3。
    这是个性化校准机制（evaluation.md §6）的数据入口，评分历史后续用于
    校准 judge 的评分标准。
    """
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        raw = request.data.get('user_rating')
        if raw is None:
            return Response({'error': '缺少 user_rating 参数'}, status=400)

        try:
            user_rating = int(raw)
            if not 0 <= user_rating <= 10:
                raise ValueError
        except (ValueError, TypeError):
            return Response({'error': 'user_rating 必须为 0-10 整数'}, status=400)

        try:
            score_obj = ConsistencyScore.objects.get(id=pk, user=request.user)
        except ConsistencyScore.DoesNotExist:
            return Response({'error': '评估记录不存在'}, status=404)

        score_obj.user_rating = user_rating
        score_obj.user_rated_at = timezone.now()
        score_obj.save(update_fields=['user_rating', 'user_rated_at'])

        return Response({
            'evaluation_id': str(score_obj.id),
            'user_rating': score_obj.user_rating,
            'llm_score': score_obj.score,
            'final_score': score_obj.final_score,
        })