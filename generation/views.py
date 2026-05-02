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


def _get_style_fragments(
    character: BaseCard,
    scene_input: dict,
    user,
    llm_config,
    limit: int = 5,
) -> list:
    """
    从 pgvector 检索与当前场景最相似的风格示例片段。

    limit 默认改为 5（原为 3），以支持候选反应面板展示。
    调用方决定注入数量（通常注入 top-1，其余作为候选展示）。

    MVP 约束：向量化使用 gemini-embedding-001，仅当用户配置 Gemini 时生效。
    降级策略：Anthropic/Groq 用户返回空列表，生成正常进行。
    """
    if llm_config.provider not in ('gemini', 'groq'):
        return []

    try:
        from examples.models import Fragment
        from examples.embedding import get_embedding, scene_to_text
        from pgvector.django import CosineDistance

        if not Fragment.objects.filter(
            owner=user, character=character, is_confirmed=True,
        ).exists():
            return []

        key_obj = UserProviderKey.objects.get(user=user, provider='gemini')
        api_key = decrypt_key(key_obj.api_key_encrypted)
        scene_text = scene_to_text(scene_input)
        if not scene_text.strip():
            return []

        query_embedding = get_embedding(scene_text, api_key)

        return list(
            Fragment.objects.filter(
                owner=user,
                character=character,
                is_confirmed=True,
                embedding__isnull=False,
            ).order_by(
                CosineDistance('embedding', query_embedding)
            )[:limit]
        )

    except Exception:
        logger.exception('Style fragment retrieval failed, proceeding without injection')
        return []


def _get_fragment_by_id(fragment_id: str, user) -> object | None:
    """
    候选反应面板：用户切换候选时，按 ID 直接获取指定片段，跳过相似度检索。
    """
    try:
        from examples.models import Fragment
        return Fragment.objects.get(id=fragment_id, owner=user, is_confirmed=True)
    except Exception:
        return None


def _get_active_rel_contexts(character: BaseCard, active_relationship_ids: list, user) -> list:
    """
    根据前端传入的关系 ID 列表，查询对应关系实体和当前角色的 membership。
    返回 [(Relationship, RelationshipMembership | None), ...] 列表。
    只返回属于当前用户的关系（防止越权）。
    """
    if not active_relationship_ids:
        return []

    try:
        rels = list(Relationship.objects.filter(
            id__in=active_relationship_ids, owner=user,
        ))
        membership_map = {
            m.relationship_id: m
            for m in RelationshipMembership.objects.filter(
                relationship__in=rels, character=character,
            )
        }
        return [(rel, membership_map.get(rel.id)) for rel in rels]
    except Exception:
        logger.exception('Failed to fetch relationship contexts')
        return []


def _fragment_preview(fragment, max_len: int = 120) -> str:
    """生成片段的文字预览，用于候选面板展示。"""
    text = fragment.text or ''
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + '…'


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
        active_relationship_ids = request.data.get('active_relationship_ids', [])
        # 候选反应面板：切换候选时前端传入指定片段 ID
        forced_fragment_id = request.data.get('forced_fragment_id')

        if not character_id:
            return StreamingHttpResponse(_error_stream('请选择角色'), content_type='text/event-stream')
        if not scene_input.get('location') or not scene_input.get('intent'):
            return StreamingHttpResponse(_error_stream('场景地点和写作意图为必填项'), content_type='text/event-stream')

        try:
            character = BaseCard.objects.get(id=character_id, owner=request.user)
        except BaseCard.DoesNotExist:
            return StreamingHttpResponse(_error_stream('角色不存在'), content_type='text/event-stream')

        au_mod = None
        if au_mod_id:
            try:
                au_mod = AUMod.objects.get(id=au_mod_id, character=character)
            except AUMod.DoesNotExist:
                pass

        try:
            key_obj = UserProviderKey.objects.get(user=request.user, provider=llm_config.provider)
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

        # ── 候选反应面板逻辑 ──────────────────────────────────────────────────
        # 获取 top-5 候选片段（相似度排序）
        all_candidates = _get_style_fragments(
            character, scene_input, request.user, llm_config, limit=5
        )

        if forced_fragment_id:
            # 切换候选：使用指定片段注入，跳过相似度检索
            forced = _get_fragment_by_id(forced_fragment_id, request.user)
            style_fragments = [forced] if forced else all_candidates[:1]
        else:
            # 正常生成：注入相似度最高的 1 条
            # 原设计（phase2.md §1）：「路径二加权采样，从候选集随机抽一条注入」
            # 这里直接取 top-1（最高相似度），候选面板展示其余候选
            style_fragments = all_candidates[:1]
        # ── 候选面板逻辑结束 ──────────────────────────────────────────────────

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
                    'activeRelationships': [str(rel.id) for rel, _ in active_rel_contexts],
                    # 候选反应面板：本次注入的片段 ID（面板标记「当前」用）
                    'currentFragmentId': str(style_fragments[0].id) if style_fragments else None,
                    # 候选反应面板：全部候选（最多 5 条），前端直接渲染
                    'candidates': [
                        {
                            'fragmentId': str(f.id),
                            'preview': _fragment_preview(f),
                        }
                        for f in all_candidates
                    ],
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