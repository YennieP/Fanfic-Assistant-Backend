import logging
from django.db.models import Max, Q
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions

from characters.models import BaseCard
from users.encryption import decrypt_key
from .models import Article, Fragment
from .serializers import ArticleSerializer, ArticleListSerializer, FragmentSerializer
from .embedding import get_embedding, tags_to_text
from .llm_pipeline import segment_article, infer_tags
from generation.providers.anthropic import AnthropicProvider
from generation.providers.gemini import GeminiProvider
from generation.providers.groq import GroqProvider
from generation.providers.cerebras import CerebrasProvider
from generation.providers.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)


def _get_provider(llm_config):
    from users.models import UserProviderKey
    key_obj = UserProviderKey.objects.get(
        user=llm_config.user, provider=llm_config.provider
    )
    api_key = decrypt_key(key_obj.api_key_encrypted)
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


def _get_llm_config(user):
    try:
        return user.llm_config
    except Exception:
        raise ValueError('未配置 LLM provider，请先在设置页配置')


def _find_gaps(confirmed_fragments: list, total_lines: int) -> list[tuple[int, int]]:
    """
    在已确认片段的行号覆盖范围中找出所有「缺口」（未覆盖的行范围）。

    返回 list of (gap_start, gap_end)，三种情况均涵盖：
      - 缺口在正文开头  ：第一个已确认片段之前有未覆盖行
      - 缺口在正文中间  ：两个相邻已确认片段之间有未覆盖行
      - 缺口在正文末尾  ：最后一个已确认片段之后有未覆盖行
    """
    if not confirmed_fragments:
        return [(0, total_lines - 1)]

    gaps: list[tuple[int, int]] = []

    # 缺口在开头
    first_start = confirmed_fragments[0].start_line
    if first_start > 0:
        gaps.append((0, first_start - 1))

    # 缺口在中间
    for i in range(len(confirmed_fragments) - 1):
        end_curr  = confirmed_fragments[i].end_line
        start_next = confirmed_fragments[i + 1].start_line
        if start_next > end_curr + 1:
            gaps.append((end_curr + 1, start_next - 1))

    # 缺口在末尾
    last_end = confirmed_fragments[-1].end_line
    if last_end < total_lines - 1:
        gaps.append((last_end + 1, total_lines - 1))

    return gaps


# ── Article endpoints ─────────────────────────────────────────────────────────

class ArticleListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        character_id = request.query_params.get('character_id')
        qs = Article.objects.filter(owner=request.user).select_related('character')
        if character_id:
            qs = qs.filter(character_id=character_id)
        return Response(ArticleListSerializer(qs, many=True).data)

    def post(self, request):
        character_id = (
            request.data.get('character_id')
            or request.data.get('characterId')
        )
        title   = request.data.get('title', '').strip()
        content = request.data.get('content', '').strip()

        if not character_id:
            return Response({'error': '请选择关联角色'}, status=400)
        if not content:
            return Response({'error': '文章内容不能为空'}, status=400)

        character = get_object_or_404(BaseCard, id=character_id, owner=request.user)
        article = Article.objects.create(
            owner=request.user,
            character=character,
            title=title or '未命名文章',
            content=content,
        )
        return Response(ArticleSerializer(article).data, status=201)


class ArticleDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, article_id):
        article = get_object_or_404(Article, id=article_id, owner=request.user)
        return Response(ArticleSerializer(article).data)

    def patch(self, request, article_id):
        article = get_object_or_404(Article, id=article_id, owner=request.user)
        if 'title'   in request.data: article.title   = request.data['title']
        if 'content' in request.data: article.content = request.data['content']
        article.save()
        return Response(ArticleSerializer(article).data)

    def delete(self, request, article_id):
        article = get_object_or_404(Article, id=article_id, owner=request.user)
        article.delete()
        return Response(status=204)


class ArticleSegmentView(APIView):
    """
    POST /api/examples/articles/:id/segment/ — LLM 情节切割

    智能缺口检测逻辑（双向上下文，三种缺口位置均支持）：

    1. 读取所有「已确认且有行号」的片段，排序后找出未覆盖行范围（缺口）
    2. 对每个缺口：
       - 找前方最近的已确认片段 → prev_context（末尾若干行）
       - 找后方最近的已确认片段 → next_context（开头若干行）
       - 调用 LLM 仅切割该缺口范围内的内容
    3. 将所有新片段合并，order 按 start_line 排序保证正确顺序
    4. 在每个缺口范围内清理旧的未确认片段（避免重复）

    三种缺口位置：
      - 缺口在正文开头：只有 next_context（无前置）
      - 缺口在正文中间：prev_context + next_context
      - 缺口在正文末尾：只有 prev_context（无后置）
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, article_id):
        article = get_object_or_404(Article, id=article_id, owner=request.user)

        try:
            llm_config = _get_llm_config(request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        lines       = article.content.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return Response({'error': '文章内容为空'}, status=400)

        # ── 获取所有已确认且有行号的片段（用于 gap 检测和上下文提取）────────
        confirmed = list(
            article.fragments
            .filter(is_confirmed=True, start_line__isnull=False)
            .order_by('start_line')
        )

        # ── 找出所有缺口 ──────────────────────────────────────────────────────
        gaps = _find_gaps(confirmed, total_lines)

        if not gaps:
            return Response({
                'count':   0,
                'fragments': [],
                'message': '所有内容已分割完毕，无需重新切割',
            })

        # ── 清理各缺口内的旧未确认片段 ────────────────────────────────────────
        for gap_start, gap_end in gaps:
            article.fragments.filter(is_confirmed=False).filter(
                Q(start_line__gte=gap_start, start_line__lte=gap_end) |
                Q(end_line__gte=gap_start,   end_line__lte=gap_end)
            ).delete()
        # 清理无行号的旧格式未确认片段
        article.fragments.filter(is_confirmed=False, start_line__isnull=True).delete()

        # ── 为每个缺口找前后上下文 ────────────────────────────────────────────
        # 构建 {end_line: fragment} 和 {start_line: fragment} 两个查找表
        by_end   = {f.end_line:   f for f in confirmed}
        by_start = {f.start_line: f for f in confirmed}

        def _prev_fragment(gap_start: int):
            """找缺口前方最近的已确认片段（end_line < gap_start）。"""
            candidates = [f for f in confirmed if f.end_line < gap_start]
            return max(candidates, key=lambda f: f.end_line) if candidates else None

        def _next_fragment(gap_end: int):
            """找缺口后方最近的已确认片段（start_line > gap_end）。"""
            candidates = [f for f in confirmed if f.start_line > gap_end]
            return min(candidates, key=lambda f: f.start_line) if candidates else None

        # ── 逐缺口调用 LLM ──────────────────────────────────────────────────
        try:
            provider = _get_provider(llm_config)
        except Exception as e:
            return Response({'error': f'获取 LLM provider 失败：{str(e)}'}, status=400)

        all_new_fragments: list[Fragment] = []
        # order 用 start_line 保证全局读取顺序
        next_order_base = (
            article.fragments.aggregate(max_order=Max('order'))['max_order'] or -1
        ) + 1

        for gap_idx, (gap_start, gap_end) in enumerate(gaps):
            gap_content = '\n'.join(lines[gap_start:gap_end + 1])
            if not gap_content.strip():
                continue

            prev_frag = _prev_fragment(gap_start)
            next_frag = _next_fragment(gap_end)

            try:
                segment_results = segment_article(
                    gap_content,
                    provider,
                    global_start=gap_start,
                    prev_context=prev_frag.text if prev_frag else None,
                    next_context=next_frag.text if next_frag else None,
                )
            except Exception as e:
                logger.exception('Segmentation failed for gap %d-%d', gap_start, gap_end)
                err_str = str(e)
                if '503' in err_str or 'UNAVAILABLE' in err_str:
                    return Response({'error': 'Gemini 当前负载过高，请等待 1-2 分钟后重试'}, status=503)
                return Response({'error': f'切割失败：{err_str}'}, status=500)

            if not segment_results:
                logger.warning('Empty segment result for gap %d-%d', gap_start, gap_end)
                continue

            for seg in segment_results:
                f = Fragment.objects.create(
                    owner=request.user,
                    article=article,
                    character=article.character,
                    text=seg['text'],
                    fragment_type=seg.get('type', 'story'),
                    start_line=seg['start'],
                    end_line=seg['end'],
                    order=seg['start'],  # 用 start_line 作 order，保证全文阅读顺序
                )
                all_new_fragments.append(f)

        if not all_new_fragments:
            return Response({'error': 'Gemini 返回了空结果，可能是负载过高，请稍后重试'}, status=503)

        return Response({
            'count':     len(all_new_fragments),
            'fragments': FragmentSerializer(all_new_fragments, many=True).data,
        })


class ArticleBatchConfirmView(APIView):
    """POST /api/examples/articles/:id/confirm-all/"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, article_id):
        article = get_object_or_404(Article, id=article_id, owner=request.user)

        from users.models import UserProviderKey
        try:
            gemini_key_obj = UserProviderKey.objects.get(user=request.user, provider='gemini')
            api_key = decrypt_key(gemini_key_obj.api_key_encrypted)
        except UserProviderKey.DoesNotExist:
            return Response({
                'error': '向量化需要 Gemini API Key。请在设置页配置 Gemini Key 后重试。'
            }, status=400)

        to_confirm = article.fragments.filter(
            is_confirmed=False, fragment_type='story',
        ).exclude(tags={})

        confirmed_ids, error_ids = [], []
        for fragment in to_confirm:
            try:
                tag_text = tags_to_text(fragment.tags)
                if not tag_text:
                    continue
                fragment.embedding    = get_embedding(tag_text, api_key)
                fragment.is_confirmed = True
                fragment.save()
                confirmed_ids.append(str(fragment.id))
            except Exception:
                logger.exception('Vectorization failed for fragment %s', fragment.id)
                error_ids.append(str(fragment.id))

        return Response({
            'confirmed': len(confirmed_ids),
            'errors':    len(error_ids),
            'error_ids': error_ids,
        })


# ── Fragment endpoints ────────────────────────────────────────────────────────

class FragmentListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    # views.py — FragmentListView.get() 修改

    def get(self, request):
        character_id   = request.query_params.get('character') or request.query_params.get('character_id')
        confirmed_only = (
            request.query_params.get('is_confirmed') == 'true'
            or request.query_params.get('confirmed') == 'true'
        )
        qs = Fragment.objects.filter(owner=request.user).select_related('article', 'character')
        if character_id:
            qs = qs.filter(character_id=character_id)
        if confirmed_only:
            qs = qs.filter(is_confirmed=True)
        return Response(FragmentSerializer(qs, many=True).data)

    def post(self, request):
        """
        创建单个草稿片段，供前端在冲突解决后创建残余片段使用。
        不触发向量化，fragment_type 固定为 'story'，is_confirmed=False。
        """
        article_id = (
            request.data.get('article_id')
            or request.data.get('articleId')
        )
        text  = request.data.get('text', '').strip()
        order = int(request.data.get('order', 0))

        if not article_id:
            return Response({'error': '请提供 article_id'}, status=400)
        if not text:
            return Response({'error': '片段内容不能为空'}, status=400)

        article = get_object_or_404(Article, id=article_id, owner=request.user)

        fragment = Fragment.objects.create(
            owner=request.user,
            article=article,
            character=article.character,
            text=text,
            order=order,
            fragment_type='story',
        )
        return Response(FragmentSerializer(fragment).data, status=201)


class FragmentDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, fragment_id):
        fragment = get_object_or_404(Fragment, id=fragment_id, owner=request.user)
        return Response(FragmentSerializer(fragment).data)

    def patch(self, request, fragment_id):
        fragment = get_object_or_404(Fragment, id=fragment_id, owner=request.user)
        changed = False
        if 'text' in request.data:
            fragment.text = request.data['text']
            changed = True
        if 'tags' in request.data:
            fragment.tags = request.data['tags']
            changed = True
        if changed:
            fragment.is_confirmed = False
            fragment.embedding    = None
        fragment.save()
        return Response(FragmentSerializer(fragment).data)

    def delete(self, request, fragment_id):
        fragment = get_object_or_404(Fragment, id=fragment_id, owner=request.user)
        fragment.delete()
        return Response(status=204)


class FragmentInferTagsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, fragment_id):
        fragment = get_object_or_404(Fragment, id=fragment_id, owner=request.user)

        try:
            llm_config = _get_llm_config(request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        language = request.data.get('lang', 'zh')
        if language not in ('zh', 'en'):
            language = 'zh'

        try:
            provider = _get_provider(llm_config)
            tags = infer_tags(fragment.text, provider, language=language)
        except Exception as e:
            logger.exception('Tag inference failed')
            err_str = str(e)
            if '429' in err_str or 'rate_limit_exceeded' in err_str or 'Rate limit' in err_str:
                return Response({
                    'error': f'{llm_config.provider.capitalize()} 每日 token 配额已用完，请明天再试或在设置页切换其他 provider'
                }, status=429)
            if '503' in err_str or 'UNAVAILABLE' in err_str:
                return Response({'error': 'LLM 服务暂时不可用，请稍后重试'}, status=503)
            return Response({'error': f'标签推断失败：{err_str}'}, status=500)

        fragment.tags         = tags
        fragment.is_confirmed = False
        fragment.embedding    = None
        fragment.save()
        return Response(FragmentSerializer(fragment).data)


class FragmentConfirmView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, fragment_id):
        fragment = get_object_or_404(Fragment, id=fragment_id, owner=request.user)

        if fragment.fragment_type == 'skip':
            return Response({'error': 'skip 类型片段不需要入库'}, status=400)
        if not fragment.tags:
            return Response({'error': '请先为片段打标签再确认入库'}, status=400)

        from users.models import UserProviderKey
        try:
            gemini_key_obj = UserProviderKey.objects.get(user=request.user, provider='gemini')
            api_key = decrypt_key(gemini_key_obj.api_key_encrypted)
        except UserProviderKey.DoesNotExist:
            return Response({
                'error': '向量化需要 Gemini API Key。请在设置页配置 Gemini Key 后重试。'
            }, status=400)

        try:
            tag_text = tags_to_text(fragment.tags)
            if not tag_text:
                return Response({'error': '标签内容为空，无法向量化'}, status=400)
            fragment.embedding    = get_embedding(tag_text, api_key)
            fragment.is_confirmed = True
            fragment.save()
        except Exception as e:
            logger.exception('Fragment vectorization failed')
            return Response({'error': f'向量化失败：{str(e)}'}, status=500)

        return Response(FragmentSerializer(fragment).data)