import logging
from django.db import transaction
from django.db.models import Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions

from characters.models import BaseCard
from users.encryption import decrypt_key
from .models import Article, Fragment
from .serializers import (
    ArticleSerializer,
    ArticleListSerializer,
    ConfirmSelectedSerializer,
    FragmentSerializer,
    MergeFragmentsSerializer,
    ResolveConflictSerializer,
    ResolveConflictsSerializer,
)
from .embedding import get_embedding, tags_to_text
from .llm_pipeline import segment_article, infer_tags
from generation.providers.anthropic import AnthropicProvider
from generation.providers.gemini import GeminiProvider
from generation.providers.groq import GroqProvider
from generation.providers.cerebras import CerebrasProvider
from generation.providers.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)


def _extract_residuals(preserved: str, discarded: str) -> list[str]:
    """复刻前端既有语义：保留 discarded 中不与 preserved 重叠的连续行块。"""
    preserved_lines = {
        line.strip()
        for line in preserved.split('\n')
        if line.strip()
    }
    blocks: list[str] = []
    current: list[str] = []

    for raw_line in discarded.split('\n'):
        line = raw_line.strip()
        if line and line in preserved_lines:
            text = '\n'.join(item for item in current if item).strip()
            if text:
                blocks.append(text)
            current = []
        else:
            current.append(line)

    tail = '\n'.join(item for item in current if item).strip()
    if tail:
        blocks.append(tail)
    return blocks


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


def _validate_segment_results(
    segment_results: list[dict],
    lines: list[str],
    gap_start: int,
    gap_end: int,
) -> list[dict]:
    """验证非空原文行恰好覆盖一次；允许片段之间只遗漏空白行。"""
    if not segment_results:
        if any(lines[index].strip() for index in range(gap_start, gap_end + 1)):
            raise ValueError('empty segment result')
        return []

    try:
        ordered = sorted(segment_results, key=lambda segment: segment['start'])
    except (KeyError, TypeError):
        raise ValueError('segment result is missing a valid start line') from None

    previous_end = gap_start - 1
    covered_lines: set[int] = set()
    validated: list[dict] = []
    for segment in ordered:
        start = segment.get('start')
        end = segment.get('end')
        fragment_type = segment.get('type')
        if type(start) is not int or type(end) is not int:
            raise ValueError('segment range must use integer line numbers')
        if start < gap_start or end < start or end > gap_end:
            raise ValueError('segment range is outside the requested gap')
        if start <= previous_end:
            raise ValueError('segment ranges must not overlap')
        if fragment_type not in ('story', 'skip'):
            raise ValueError('segment type is invalid')

        expected_text = '\n'.join(lines[start:end + 1]).strip()
        segment_text = segment.get('text')
        if (
            not expected_text
            or not isinstance(segment_text, str)
            or segment_text.strip() != expected_text
        ):
            raise ValueError('segment text does not match its source range')

        validated.append({
            'text': expected_text,
            'type': fragment_type,
            'start': start,
            'end': end,
        })
        covered_lines.update(range(start, end + 1))
        previous_end = end

    missing_content_lines = [
        index
        for index in range(gap_start, gap_end + 1)
        if lines[index].strip() and index not in covered_lines
    ]
    if missing_content_lines:
        raise ValueError('segment ranges do not cover every non-empty source line')
    return validated


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
    3. 先在内存中验证所有缺口结果，任一失败都保留上一轮草稿
    4. 在短事务中删除旧的未确认片段并批量创建全部新片段

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

        pending_fragments: list[Fragment] = []

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
                    user=request.user,
                )
            except Exception as e:
                logger.exception('Segmentation failed for gap %d-%d', gap_start, gap_end)
                err_str = str(e)
                if '503' in err_str or 'UNAVAILABLE' in err_str:
                    return Response({'error': 'LLM 当前负载过高，请等待 1-2 分钟后重试'}, status=503)
                return Response({'error': f'切割失败：{err_str}'}, status=500)

            try:
                validated_results = _validate_segment_results(
                    segment_results, lines, gap_start, gap_end,
                )
            except ValueError as e:
                logger.warning(
                    'Invalid segment result for gap %d-%d: %s',
                    gap_start,
                    gap_end,
                    e,
                )
                return Response({
                    'error': 'LLM 返回了空或不完整的切割结果，请稍后重试',
                }, status=503)

            for seg in validated_results:
                pending_fragments.append(Fragment(
                    owner=request.user,
                    article=article,
                    character=article.character,
                    text=seg['text'],
                    fragment_type=seg.get('type', 'story'),
                    start_line=seg['start'],
                    end_line=seg['end'],
                    order=seg['start'],  # 用 start_line 作 order，保证全文阅读顺序
                ))

        if not pending_fragments:
            return Response({'error': 'LLM 返回了空结果，可能是负载过高，请稍后重试'}, status=503)

        replacement_range = Q(start_line__isnull=True) | Q(end_line__isnull=True)
        for gap_start, gap_end in gaps:
            replacement_range |= (
                Q(start_line__lte=gap_end, end_line__gte=gap_start)
            )

        try:
            with transaction.atomic():
                # 串行化同一文章的最终替换，但绝不在等待 LLM 时持有事务。
                locked_article = Article.objects.select_for_update().get(
                    id=article.id,
                    owner=request.user,
                )
                locked_article.fragments.filter(
                    is_confirmed=False,
                ).filter(replacement_range).delete()
                Fragment.objects.bulk_create(pending_fragments)
        except Exception:
            logger.exception('Failed to replace segmented drafts for article %s', article.id)
            return Response({'error': '保存切割结果失败，请重试'}, status=500)

        return Response({
            'count':     len(pending_fragments),
            'fragments': FragmentSerializer(pending_fragments, many=True).data,
        })


class ArticleConfirmSelectedView(APIView):
    """POST /api/examples/articles/:id/confirm-selected/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, article_id):
        article = get_object_or_404(Article, id=article_id, owner=request.user)
        serializer = ConfirmSelectedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fragment_ids = serializer.validated_data['fragment_ids']

        selected = list(
            Fragment.objects.filter(
                id__in=fragment_ids,
                owner=request.user,
                article=article,
            )
        )
        if len(selected) != len(fragment_ids):
            return Response({'error': '所选片段必须全部属于当前文章'}, status=400)
        if any(
            fragment.is_confirmed or fragment.fragment_type != 'story'
            for fragment in selected
        ):
            return Response({'error': '所选片段状态已变化，请刷新后重试'}, status=409)

        tag_text_by_id = {
            fragment.id: tags_to_text(fragment.tags)
            for fragment in selected
        }
        if any(not tag_text for tag_text in tag_text_by_id.values()):
            return Response({'error': '所选片段必须全部包含有效标签'}, status=400)

        from users.models import UserProviderKey
        try:
            gemini_key_obj = UserProviderKey.objects.get(
                user=request.user,
                provider='gemini',
            )
            api_key = decrypt_key(gemini_key_obj.api_key_encrypted)
        except UserProviderKey.DoesNotExist:
            return Response({
                'error': '向量化需要 Gemini API Key。请在设置页配置 Gemini Key 后重试。'
            }, status=400)

        selected_by_id = {fragment.id: fragment for fragment in selected}
        confirmed_ids, error_ids = [], []
        for fragment_id in fragment_ids:
            fragment = selected_by_id[fragment_id]
            try:
                tag_text = tag_text_by_id[fragment.id]
                fragment.embedding = get_embedding(tag_text, api_key)
                fragment.is_confirmed = True
                fragment.save()
                confirmed_ids.append(str(fragment.id))
            except Exception:
                logger.exception('Vectorization failed for fragment %s', fragment.id)
                error_ids.append(str(fragment.id))

        return Response({
            'confirmed': len(confirmed_ids),
            'errors': len(error_ids),
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


class FragmentResolveConflictView(APIView):
    """一次事务完成冲突片段替换、残余片段创建和舍弃片段删除。"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ResolveConflictSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        old_id = data['old_fragment_id']
        new_id = data['new_fragment_id']
        action = data['action']
        edited_new_text = data['edited_new_text']

        with transaction.atomic():
            fragments = list(
                Fragment.objects.select_for_update()
                .select_related('article')
                .filter(
                    owner=request.user,
                    article__owner=request.user,
                    id__in=[old_id, new_id],
                )
                .order_by('id')
            )
            if len(fragments) != 2:
                # 对不存在、已被处理或属于其他用户的片段统一返回 404，
                # 同时保证重复请求不会再次创建 residual。
                raise Http404

            fragments_by_id = {fragment.id: fragment for fragment in fragments}
            old_fragment = fragments_by_id[old_id]
            new_fragment = fragments_by_id[new_id]

            if not old_fragment.article_id or old_fragment.article_id != new_fragment.article_id:
                return Response({'error': '新旧片段必须属于同一篇文章'}, status=400)
            if not old_fragment.is_confirmed or new_fragment.is_confirmed:
                return Response({'error': '冲突状态已变化，请刷新后重试'}, status=409)
            if (
                old_fragment.updated_at != data['old_updated_at']
                or new_fragment.updated_at != data['new_updated_at']
            ):
                return Response({'error': '冲突状态已变化，请刷新后重试'}, status=409)

            if action == 'keepOld':
                preserved_fragment = old_fragment
                discarded_fragment = new_fragment
                preserved_text = old_fragment.text
                discarded_text = edited_new_text
            else:
                preserved_fragment = new_fragment
                discarded_fragment = old_fragment
                preserved_text = edited_new_text
                discarded_text = old_fragment.text

                if edited_new_text != new_fragment.text:
                    new_fragment.text = edited_new_text
                    new_fragment.is_confirmed = False
                    new_fragment.embedding = None
                    new_fragment.save(update_fields=[
                        'text', 'is_confirmed', 'embedding', 'updated_at',
                    ])

            residual_texts = _extract_residuals(preserved_text, discarded_text)
            max_order = (
                Fragment.objects.filter(article_id=old_fragment.article_id)
                .aggregate(max_order=Max('order'))['max_order']
            )
            next_order = max(max_order if max_order is not None else 0, 0) + 1
            residual_fragments = [
                Fragment(
                    owner=request.user,
                    article_id=old_fragment.article_id,
                    character_id=old_fragment.article.character_id,
                    text=text,
                    order=next_order + index,
                    fragment_type='story',
                )
                for index, text in enumerate(residual_texts)
            ]
            Fragment.objects.bulk_create(residual_fragments)

            deleted_fragment_id = discarded_fragment.id
            discarded_fragment.delete()

        return Response({
            'preserved_fragment': FragmentSerializer(preserved_fragment).data,
            'residual_fragments': FragmentSerializer(residual_fragments, many=True).data,
            'deleted_fragment_id': str(deleted_fragment_id),
            'residual_count': len(residual_fragments),
        })


class FragmentResolveConflictsView(APIView):
    """在一个事务中采用所有指定冲突对的新版。"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ResolveConflictsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conflicts = serializer.validated_data['conflicts']
        fragment_ids = {
            fragment_id
            for pair in conflicts
            for fragment_id in (pair['old_fragment_id'], pair['new_fragment_id'])
        }

        with transaction.atomic():
            fragments = list(
                Fragment.objects.select_for_update()
                .select_related('article')
                .filter(
                    owner=request.user,
                    article__owner=request.user,
                    id__in=fragment_ids,
                )
                .order_by('id')
            )
            if len(fragments) != len(fragment_ids):
                raise Http404

            fragments_by_id = {fragment.id: fragment for fragment in fragments}
            article_ids = {fragment.article_id for fragment in fragments}
            if None in article_ids or len(article_ids) != 1:
                return Response({'error': '所有冲突片段必须属于同一篇文章'}, status=400)

            for pair in conflicts:
                old_fragment = fragments_by_id[pair['old_fragment_id']]
                new_fragment = fragments_by_id[pair['new_fragment_id']]
                if old_fragment.article_id != new_fragment.article_id:
                    return Response({'error': '新旧片段必须属于同一篇文章'}, status=400)
                if not old_fragment.is_confirmed or new_fragment.is_confirmed:
                    return Response({'error': '冲突状态已变化，请刷新后重试'}, status=409)
                if (
                    old_fragment.updated_at != pair['old_updated_at']
                    or new_fragment.updated_at != pair['new_updated_at']
                ):
                    return Response({'error': '冲突状态已变化，请刷新后重试'}, status=409)

            article_id = next(iter(article_ids))
            max_order = (
                Fragment.objects.filter(article_id=article_id)
                .aggregate(max_order=Max('order'))['max_order']
            )
            next_order = max(max_order if max_order is not None else 0, 0) + 1
            residual_fragments: list[Fragment] = []
            old_ids = []

            for pair in conflicts:
                old_fragment = fragments_by_id[pair['old_fragment_id']]
                new_fragment = fragments_by_id[pair['new_fragment_id']]
                old_ids.append(old_fragment.id)
                for text in _extract_residuals(new_fragment.text, old_fragment.text):
                    residual_fragments.append(Fragment(
                        owner=request.user,
                        article_id=article_id,
                        character_id=old_fragment.article.character_id,
                        text=text,
                        order=next_order + len(residual_fragments),
                        fragment_type='story',
                    ))

            Fragment.objects.bulk_create(residual_fragments)
            Fragment.objects.filter(id__in=old_ids).delete()

        return Response({
            'resolved_count': len(conflicts),
            'residual_count': len(residual_fragments),
            'residual_fragments': FragmentSerializer(residual_fragments, many=True).data,
        })


class FragmentMergeView(APIView):
    """一次事务完成目标片段更新与另一个片段删除。"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = MergeFragmentsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        keep_id = data['keep_fragment_id']
        delete_id = data['delete_fragment_id']

        with transaction.atomic():
            fragments = list(
                Fragment.objects.select_for_update()
                .filter(
                    owner=request.user,
                    article__owner=request.user,
                    id__in=[keep_id, delete_id],
                )
                .order_by('id')
            )
            if len(fragments) != 2:
                # 不存在、越权或重复请求均不暴露片段信息，也不会产生第二次写入。
                raise Http404

            fragments_by_id = {fragment.id: fragment for fragment in fragments}
            keep_fragment = fragments_by_id[keep_id]
            delete_fragment = fragments_by_id[delete_id]

            if (
                not keep_fragment.article_id
                or keep_fragment.article_id != delete_fragment.article_id
            ):
                return Response({'error': '待合并片段必须属于同一篇文章'}, status=400)

            if (
                keep_fragment.updated_at != data['keep_updated_at']
                or delete_fragment.updated_at != data['delete_updated_at']
            ):
                return Response({'error': '片段状态已变化，请刷新后重试'}, status=409)

            keep_fragment.text = data['merged_text']
            keep_fragment.is_confirmed = False
            keep_fragment.embedding = None
            keep_fragment.save(update_fields=[
                'text', 'is_confirmed', 'embedding', 'updated_at',
            ])

            deleted_fragment_id = delete_fragment.id
            delete_fragment.delete()

        return Response({
            'merged_fragment': FragmentSerializer(keep_fragment).data,
            'deleted_fragment_id': str(deleted_fragment_id),
        })


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
            tags = infer_tags(fragment.text, provider, language=language, user=request.user)
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
