import logging
from django.db.models import Max
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
    else:
        return GeminiProvider(api_key)


def _get_llm_config(user):
    try:
        return user.llm_config
    except Exception:
        raise ValueError('未配置 LLM provider，请先在设置页配置')


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

    智能重切割逻辑：
    1. 检查文章是否有已确认且带行号的片段
    2. 有 → 只切最后已确认行之后的新内容；将最后已确认片段作为上下文注入 LLM
    3. 无 → 全文切割（初次切割或所有片段均未确认）
    
    切割结果包含 story 和 skip 两种类型，保证所有行均有归属（全文覆盖不变量）。
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

        # ── 智能重切割：寻找已确认片段中最后一个有行号的 ────────────────────
        last_confirmed = (
            article.fragments
            .filter(is_confirmed=True, end_line__isnull=False)
            .order_by('end_line')
            .last()
        )

        if last_confirmed:
            last_end = last_confirmed.end_line

            if last_end >= total_lines - 1:
                return Response({
                    'count':   0,
                    'fragments': [],
                    'message': '所有内容已分割完毕，无需重新切割',
                })

            global_start    = last_end + 1
            overlap_context = last_confirmed.text

            # 清理新区间内的未确认片段（含无行号的旧格式片段）
            article.fragments.filter(is_confirmed=False, start_line__gte=global_start).delete()
            article.fragments.filter(is_confirmed=False, start_line__isnull=True).delete()
        else:
            # 初次切割或全部未确认：清除所有未确认，全文重切
            article.fragments.filter(is_confirmed=False).delete()
            global_start    = 0
            overlap_context = None
        # ── 智能重切割结束 ────────────────────────────────────────────────────

        new_content = '\n'.join(lines[global_start:])
        if not new_content.strip():
            return Response({'count': 0, 'fragments': [], 'message': '没有新内容需要分割'})

        try:
            provider = _get_provider(llm_config)
            segment_results = segment_article(
                new_content,
                provider,
                global_start=global_start,
                overlap_context=overlap_context,
            )
        except Exception as e:
            logger.exception('Article segmentation failed')
            err_str = str(e)
            if '503' in err_str or 'UNAVAILABLE' in err_str:
                return Response({'error': 'Gemini 当前负载过高，请等待 1-2 分钟后重试'}, status=503)
            return Response({'error': f'切割失败：{err_str}'}, status=500)

        if not segment_results:
            return Response({'error': 'Gemini 返回了空结果，可能是负载过高，请稍后重试'}, status=503)

        # order 接续已有片段的最大值
        max_order = article.fragments.aggregate(max_order=Max('order'))['max_order']
        next_order = (max_order + 1) if max_order is not None else 0

        fragments = []
        for i, seg in enumerate(segment_results):
            f = Fragment.objects.create(
                owner=request.user,
                article=article,
                character=article.character,
                text=seg['text'],
                fragment_type=seg.get('type', 'story'),
                start_line=seg['start'],
                end_line=seg['end'],
                order=next_order + i,
            )
            fragments.append(f)

        return Response({
            'count':     len(fragments),
            'fragments': FragmentSerializer(fragments, many=True).data,
        })


class ArticleBatchConfirmView(APIView):
    """POST /api/examples/articles/:id/confirm-all/ — 批量向量化入库"""
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

        # 只处理 story 类型、未确认、有标签的片段（skip 片段不入库）
        to_confirm = article.fragments.filter(
            is_confirmed=False,
            fragment_type='story',
        ).exclude(tags={})

        confirmed_ids = []
        error_ids     = []

        for fragment in to_confirm:
            try:
                tag_text = tags_to_text(fragment.tags)
                if not tag_text:
                    continue
                fragment.embedding   = get_embedding(tag_text, api_key)
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

    def get(self, request):
        character_id   = request.query_params.get('character_id')
        confirmed_only = request.query_params.get('confirmed') == 'true'
        qs = Fragment.objects.filter(owner=request.user).select_related('article', 'character')
        if character_id:
            qs = qs.filter(character_id=character_id)
        if confirmed_only:
            qs = qs.filter(is_confirmed=True)
        return Response(FragmentSerializer(qs, many=True).data)


class FragmentDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, fragment_id):
        fragment = get_object_or_404(Fragment, id=fragment_id, owner=request.user)
        return Response(FragmentSerializer(fragment).data)

    def patch(self, request, fragment_id):
        """更新片段文本或标签。修改后重置确认状态。"""
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
    """POST /api/examples/fragments/:id/infer-tags/ — LLM 标签推断"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, fragment_id):
        fragment = get_object_or_404(Fragment, id=fragment_id, owner=request.user)

        try:
            llm_config = _get_llm_config(request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        # 前端传入当前界面语言（'zh' 或 'en'），默认 'zh' 兼容旧请求
        language = request.data.get('lang', 'zh')
        if language not in ('zh', 'en'):
            language = 'zh'

        try:
            provider = _get_provider(llm_config)
            tags = infer_tags(fragment.text, provider, language=language)
        except Exception as e:
            logger.exception('Tag inference failed')
            return Response({'error': f'标签推断失败：{str(e)}'}, status=500)

        fragment.tags         = tags
        fragment.is_confirmed = False
        fragment.embedding    = None
        fragment.save()
        return Response(FragmentSerializer(fragment).data)


class FragmentConfirmView(APIView):
    """POST /api/examples/fragments/:id/confirm/ — 单片段向量化入库"""
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