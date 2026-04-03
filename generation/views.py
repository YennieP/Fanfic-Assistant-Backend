import json
import logging
from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.core.cache import cache

from characters.models import BaseCard, AUMod
from users.encryption import decrypt_key
from .prompt import build_prompt
from .providers.anthropic import AnthropicProvider
from .providers.gemini import GeminiProvider
from logs.decorators import log_llm_call

logger = logging.getLogger(__name__)


def _is_rate_limited(user_id: int, rate: int = 10, period: int = 60) -> bool:
    key = f'ratelimit:generate:{user_id}'
    count = cache.get(key, 0)
    if count >= rate:
        return True
    cache.set(key, count + 1, timeout=period)
    return False


class GenerateStreamView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if _is_rate_limited(request.user.id):
            return StreamingHttpResponse(
                _error_stream('请求过于频繁，请稍后再试'),
                content_type='text/event-stream',
            )

        try:
            llm_config = request.user.llm_config
        except Exception:
            return StreamingHttpResponse(
                _error_stream('未配置 API Key，请先在设置页配置'),
                content_type='text/event-stream',
            )

        character_id = request.data.get('character_id')
        au_mod_id = request.data.get('au_mod_id')
        scene_input = request.data.get('scene_input', {})

        if not character_id:
            return StreamingHttpResponse(
                _error_stream('请选择角色'),
                content_type='text/event-stream',
            )
        if not scene_input.get('location') or not scene_input.get('intent'):
            return StreamingHttpResponse(
                _error_stream('场景地点和写作意图为必填项'),
                content_type='text/event-stream',
            )

        try:
            character = BaseCard.objects.get(id=character_id, owner=request.user)
        except BaseCard.DoesNotExist:
            return StreamingHttpResponse(
                _error_stream('角色不存在'),
                content_type='text/event-stream',
            )

        au_mod = None
        if au_mod_id:
            try:
                au_mod = AUMod.objects.get(id=au_mod_id, character=character)
            except AUMod.DoesNotExist:
                pass

        try:
            api_key = decrypt_key(llm_config.api_key_encrypted)
        except Exception:
            return StreamingHttpResponse(
                _error_stream('API Key 解密失败，请重新在设置页保存'),
                content_type='text/event-stream',
            )

        provider = (
            AnthropicProvider(api_key)
            if llm_config.provider == 'anthropic'
            else GeminiProvider(api_key)
        )

        system_prompt, user_prompt = build_prompt(character, au_mod, scene_input)

        # 用局部闭包包裹 provider.stream，让 decorator 能提取 user 并注入 log
        @log_llm_call(feature='character_generate')
        def _get_stream(user=None):
            return provider.stream(system_prompt, user_prompt)

        def event_stream():
            try:
                # _get_stream 返回的 generator 已由 decorator 包装：
                # - str chunk 正常透传
                # - UsageInfo sentinel 被过滤，迭代结束时自动写 LlmCallLog
                for chunk in _get_stream(user=request.user):
                    data = json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)
                    yield f'data: {data}\n\n'
                yield f'data: {json.dumps({"type": "done"})}\n\n'
            except Exception as e:
                logger.exception('LLM streaming error')
                data = json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)
                yield f'data: {data}\n\n'

        response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response


def _error_stream(message: str):
    data = json.dumps({'type': 'error', 'message': message}, ensure_ascii=False)
    yield f'data: {data}\n\n'