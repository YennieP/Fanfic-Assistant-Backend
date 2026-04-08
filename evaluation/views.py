import json
import re
import uuid
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from characters.models import BaseCard, AUMod
from logs.models import LlmCallLog
from logs.decorators import log_llm_call
from users.encryption import decrypt_key
from generation.providers.anthropic import AnthropicProvider
from generation.providers.gemini import GeminiProvider
from .models import ConsistencyScore
from .prompt import build_judge_prompt

logger = logging.getLogger(__name__)


class EvaluateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        generation_id = request.data.get('generation_id')
        generated_text = (request.data.get('generated_text') or '').strip()
        character_id = request.data.get('character_id')
        au_mod_id = request.data.get('au_mod_id')

        if not all([generation_id, generated_text, character_id]):
            return Response({'error': '缺少必填参数'}, status=400)

        # 查找被评估的生成记录
        try:
            generation_log = LlmCallLog.objects.get(
                generation_id=generation_id, user=request.user
            )
        except LlmCallLog.DoesNotExist:
            return Response({'error': '生成记录不存在'}, status=404)

        # 查找角色
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

        # 获取用户 LLM 配置
        try:
            llm_config = request.user.llm_config
            api_key = decrypt_key(llm_config.api_key_encrypted)
        except Exception:
            return Response({'error': '未配置 API Key，请先在设置页配置'}, status=400)

        provider = (
            AnthropicProvider(api_key)
            if llm_config.provider == 'anthropic'
            else GeminiProvider(api_key)
        )

        system_prompt, user_prompt = build_judge_prompt(character, au_mod, generated_text)

        # 为 judge 调用分配唯一 id，sync=True 保证返回时已落库，之后可按 id 查到
        judge_id = uuid.uuid4()

        @log_llm_call(feature='consistency_check', sync=True)
        def _call_judge(user=None, generation_id=None):
            return provider.complete(system_prompt, user_prompt)

        try:
            result_text = _call_judge(user=request.user, generation_id=judge_id)
        except Exception as e:
            logger.exception('Judge LLM call failed')
            return Response({'error': f'评估调用失败：{str(e)}'}, status=500)

        # 解析 JSON，兼容模型误加 markdown 代码块的情况
        try:
            clean = re.sub(r'```(?:json)?\s*|\s*```', '', result_text).strip()
            result = json.loads(clean)
            score = int(result['score'])
            reasoning = str(result['reasoning'])
            if not 0 <= score <= 10:
                raise ValueError(f'score {score} out of range')
        except Exception:
            logger.error('Judge returned invalid response: %s', result_text)
            return Response({'error': '评估结果格式异常，请重试'}, status=500)

        # 查找 judge 的调用记录（sync=True 保证已落库）
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