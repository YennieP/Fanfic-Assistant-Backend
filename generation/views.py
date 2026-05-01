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
from .providers.groq import GroqProvider
from users.models import UserProviderKey



logger = logging.getLogger(__name__)


def _is_rate_limited(user_id: int, rate: int = 10, period: int = 60) -> bool:
    key = f'ratelimit:generate:{user_id}'
    count = cache.get(key, 0)
    if count >= rate:
        return True
    cache.set(key, count + 1, timeout=period)
    return False


def _get_style_fragments(character: BaseCard, scene_input: dict, user, llm_config, limit: int = 3) -> list:
    """
    从 pgvector 检索与当前场景最相似的风格示例片段。

    MVP 约束：向量化使用 Gemini text-embedding-004，仅当用户配置 Gemini 时生效。
    降级策略：Anthropic 用户跳过注入，生成正常进行（不报错）。
    Phase 3 升级路径：在此函数中新增 content_embedding 的加权合并逻辑即可。
    """
    # 仅在 Gemini 配置下支持向量检索
    if llm_config.provider not in ('gemini', 'groq'):
        return []

    try:
        from examples.models import Fragment
        from examples.embedding import get_embedding, scene_to_text
        from pgvector.django import CosineDistance

        # 快速检查是否有已入库的片段，避免无效的 embedding API 调用
        if not Fragment.objects.filter(
            owner=user,
            character=character,
            is_confirmed=True,
        ).exists():
            return []

        api_key = decrypt_key(llm_config.api_key_encrypted)
        scene_text = scene_to_text(scene_input)
        if not scene_text.strip():
            return []

        query_embedding = get_embedding(scene_text, api_key)

        fragments = list(
            Fragment.objects.filter(
                owner=user,
                character=character,
                is_confirmed=True,
                embedding__isnull=False,
            ).order_by(
                CosineDistance('embedding', query_embedding)
            )[:limit]
        )
        return fragments

    except Exception:
        # 降级：任何检索错误不影响生成
        logger.exception('Style fragment retrieval failed, proceeding without injection')
        return []


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
            key_obj = UserProviderKey.objects.get(
                user=request.user, provider=llm_config.provider
            )
            api_key = decrypt_key(key_obj.api_key_encrypted)
        except Exception:
            return StreamingHttpResponse(
                _error_stream(f'未找到 {llm_config.provider} 的 API Key，请在设置页保存'),
                content_type='text/event-stream',
            )

        if llm_config.provider == 'anthropic':
            provider = AnthropicProvider(api_key)
        elif llm_config.provider == 'groq':
            provider = GroqProvider(api_key)
        else:
            provider = GeminiProvider(api_key)

        # Phase 2：检索风格示例片段（Gemini 用户生效，Anthropic 用户返回空列表）
        style_fragments = _get_style_fragments(
            character, scene_input, request.user, llm_config
        )

        system_prompt, user_prompt = build_prompt(
            character, au_mod, scene_input, style_fragments
        )

        generation_id = uuid.uuid4()

        @log_llm_call(feature='character_generate', sync=True)
        def _get_stream(user=None, generation_id=None):
            return provider.stream(system_prompt, user_prompt)

        def event_stream():
            try:
                for chunk in _get_stream(user=request.user, generation_id=generation_id):
                    data = json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)
                    yield f'data: {data}\n\n'
                done_data = json.dumps({
                    'type': 'done',
                    'generationId': str(generation_id),
                    # Phase 2：告知前端本次生成是否注入了风格示例
                    'styleInjected': len(style_fragments) > 0,
                    'styleFragmentCount': len(style_fragments),
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