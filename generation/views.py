import json
import uuid
import logging
from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.core.cache import cache

from characters.models import BaseCard, AUMod, Relationship, RelationshipMembership
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

    MVP 约束：向量化使用 gemini-embedding-001，仅当用户配置 Gemini 时生效。
    降级策略：Anthropic/Groq 用户跳过注入，生成正常进行（不报错）。
    Phase 3 升级路径：在此函数中新增 content_embedding 的加权合并逻辑即可。
    """
    if llm_config.provider not in ('gemini', 'groq'):
        return []

    try:
        from examples.models import Fragment
        from examples.embedding import get_embedding, scene_to_text
        from pgvector.django import CosineDistance

        if not Fragment.objects.filter(
            owner=user,
            character=character,
            is_confirmed=True,
        ).exists():
            return []

        key_obj = UserProviderKey.objects.get(user=user, provider='gemini')
        api_key = decrypt_key(key_obj.api_key_encrypted)
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
        logger.exception('Style fragment retrieval failed, proceeding without injection')
        return []


def _get_active_rel_contexts(
    character: BaseCard,
    active_relationship_ids: list,
    user,
) -> list:
    """
    根据前端传入的关系 ID 列表，查询对应关系实体和当前角色的 membership。
    返回 [(Relationship, RelationshipMembership | None), ...] 列表。
    只返回属于当前用户的关系（防止越权）。
    """
    if not active_relationship_ids:
        return []

    try:
        rels = list(Relationship.objects.filter(
            id__in=active_relationship_ids,
            owner=user,
        ))
        membership_map = {
            m.relationship_id: m
            for m in RelationshipMembership.objects.filter(
                relationship__in=rels,
                character=character,
            )
        }
        return [(rel, membership_map.get(rel.id)) for rel in rels]
    except Exception:
        logger.exception('Failed to fetch relationship contexts')
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
        # Scaffold: 前端传入当前激活的关系实体 ID 列表
        # 写作页侧边栏实现前此列表为空，生成行为与之前完全一致
        active_relationship_ids = request.data.get('active_relationship_ids', [])

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

        style_fragments = _get_style_fragments(
            character, scene_input, request.user, llm_config
        )

        # Scaffold: 查询激活关系的上下文，空列表时对生成无影响
        active_rel_contexts = _get_active_rel_contexts(
            character, active_relationship_ids, request.user
        )

        system_prompt, user_prompt = build_prompt(
            character, au_mod, scene_input, style_fragments, active_rel_contexts
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
                    'styleInjected': len(style_fragments) > 0,
                    'styleFragmentCount': len(style_fragments),
                    # Scaffold: 本次生成实际使用了哪些关系实体
                    # 前端侧边栏实现后用于确认激活状态
                    'activeRelationships': [str(rel.id) for rel, _ in active_rel_contexts],
                    # Scaffold: 候选反应面板占位，待 phase1.md §7 实现时填充
                    'candidates': [],
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