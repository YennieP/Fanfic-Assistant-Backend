import json
import uuid
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

        # 提前生成 generation_id，不依赖数据库操作。
        # done 事件携带此 id 发给前端，前端用它触发评估请求。
        # sync=True 保证 LlmCallLog 在 yield done 之前已落库，前端拿到 id 时可立即查询。
        generation_id = uuid.uuid4()

        @log_llm_call(feature='character_generate', sync=True)
        def _get_stream(user=None, generation_id=None):
            return provider.stream(system_prompt, user_prompt)

        def event_stream():
            try:
                for chunk in _get_stream(user=request.user, generation_id=generation_id):
                    data = json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)
                    yield f'data: {data}\n\n'
                # _get_stream 迭代结束 = _write_sync 已执行 = LlmCallLog 已落库
                done_data = json.dumps({
                    'type': 'done',
                    'generationId': str(generation_id),
                })
                yield f'data: {done_data}\n\n'
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