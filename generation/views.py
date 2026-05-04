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
from .providers.cerebras import CerebrasProvider
from .providers.openrouter import OpenRouterProvider
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
    limit 默认 5（支持候选面板）；注入时取 top-1，其余作为候选展示。
    """
    if llm_config.provider not in ('gemini', 'groq', 'cerebras', 'openrouter'):
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
    """候选面板切换：按 ID 直接获取指定片段，跳过相似度检索。"""
    try:
        from examples.models import Fragment
        return Fragment.objects.get(id=fragment_id, owner=user, is_confirmed=True)
    except Exception:
        return None


def _get_active_rel_contexts(character: BaseCard, active_relationship_ids: list, user) -> list:
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
    text = fragment.text or ''
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + '…'


def _fragment_to_candidate(fragment) -> dict:
    """
    将 Fragment 对象转为候选面板所需的数据结构。
    tags 以原始 snake_case 格式输出（SSE 不经过 camelCase 中间件）。
    前端从中提取 scene_type / emotion / target_type / speech_intent 等维度展示。
    """
    return {
        'fragmentId': str(fragment.id),
        'preview': _fragment_preview(fragment),
        'tags': fragment.tags or {},
    }


class GenerateStreamView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if _is_rate_limited(request.user.id):
            return StreamingHttpResponse(
                _error_stream('rate_limited'),
                content_type='text/event-stream',
            )

        try:
            llm_config = request.user.llm_config
        except Exception:
            return StreamingHttpResponse(
                _error_stream('no_api_key'),
                content_type='text/event-stream',
            )

        character_id = request.data.get('character_id')
        au_mod_id = request.data.get('au_mod_id')
        scene_input = request.data.get('scene_input', {})
        active_relationship_ids = request.data.get('active_relationship_ids', [])
        forced_fragment_id = request.data.get('forced_fragment_id')

        if not character_id:
            return StreamingHttpResponse(_error_stream('validation_error'), content_type='text/event-stream')
        if not scene_input.get('location') or not scene_input.get('intent'):
            return StreamingHttpResponse(_error_stream('validation_error'), content_type='text/event-stream')

        try:
            character = BaseCard.objects.get(id=character_id, owner=request.user)
        except BaseCard.DoesNotExist:
            return StreamingHttpResponse(_error_stream('character_not_found'), content_type='text/event-stream')

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
                _error_stream('no_api_key'),
                content_type='text/event-stream',
            )

        if llm_config.provider == 'anthropic':
            provider = AnthropicProvider(api_key)
        elif llm_config.provider == 'groq':
            provider = GroqProvider(api_key)
        elif llm_config.provider == 'cerebras':
            provider = CerebrasProvider(api_key)
        elif llm_config.provider == 'openrouter':
            provider = OpenRouterProvider(api_key)
        else:
            provider = GeminiProvider(api_key)

        # ── 候选面板逻辑 ──────────────────────────────────────────────────────
        all_candidates = _get_style_fragments(
            character, scene_input, request.user, llm_config, limit=5
        )

        if forced_fragment_id:
            forced = _get_fragment_by_id(forced_fragment_id, request.user)
            style_fragments = [forced] if forced else all_candidates[:1]
        else:
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
                    'currentFragmentId': str(style_fragments[0].id) if style_fragments else None,
                    # 包含 tags 供前端提取 scene_type / emotion 等维度展示
                    'candidates': [_fragment_to_candidate(f) for f in all_candidates],
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


def _error_stream(code: str):
    """Yield a single SSE error event with a machine-readable code.
    Frontend maps code → i18n display text via t.writing.generationErrors[code].
    """
    data = json.dumps({'type': 'error', 'code': code}, ensure_ascii=False)
    yield f'data: {data}\n\n'