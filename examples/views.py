import logging
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
    from users.encryption import decrypt_key
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
        title = request.data.get('title', '').strip()
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
        if 'title' in request.data:
            article.title = request.data['title']
        if 'content' in request.data:
            article.content = request.data['content']
        article.save()
        return Response(ArticleSerializer(article).data)

    def delete(self, request, article_id):
        article = get_object_or_404(Article, id=article_id, owner=request.user)
        article.delete()
        return Response(status=204)


class ArticleSegmentView(APIView):
    """POST /api/examples/articles/:id/segment/ — LLM 情节切割"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, article_id):
        article = get_object_or_404(Article, id=article_id, owner=request.user)

        try:
            llm_config = _get_llm_config(request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        # 删除该文章未确认的旧片段，保留已确认的
        article.fragments.filter(is_confirmed=False).delete()

        try:
            provider = _get_provider(llm_config)
            segments = segment_article(article.content, provider)
        except Exception as e:
            logger.exception('Article segmentation failed')
            err_str = str(e)
            if '503' in err_str or 'UNAVAILABLE' in err_str:
                return Response({
                    'error': 'Gemini 当前负载过高，请等待 1-2 分钟后重试'
                }, status=503)
            return Response({'error': f'切割失败：{err_str}'}, status=500)

        if not segments:
            return Response({
                'error': 'Gemini 返回了空结果，可能是负载过高，请稍后重试'
            }, status=503)

        fragments = [
            Fragment.objects.create(
                owner=request.user,
                article=article,
                character=article.character,
                text=text,
                order=i,
            )
            for i, text in enumerate(segments)
        ]

        return Response({
            'count': len(fragments),
            'fragments': FragmentSerializer(fragments, many=True).data,
        })


class ArticleBatchConfirmView(APIView):
    """POST /api/examples/articles/:id/confirm-all/ — 批量向量化入库"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, article_id):
        article = get_object_or_404(Article, id=article_id, owner=request.user)

        try:
            llm_config = _get_llm_config(request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        if llm_config.provider != 'gemini':
            return Response({
                'error': (
                    '向量化需要 Gemini API Key。'
                    '请在设置页将 provider 切换为 Gemini，完成入库后可切换回 Anthropic。'
                    '已入库的向量不受影响。'
                )
            }, status=400)

        api_key = decrypt_key(llm_config.api_key_encrypted)
        to_confirm = article.fragments.filter(is_confirmed=False).exclude(tags={})

        confirmed_ids = []
        error_ids = []

        for fragment in to_confirm:
            try:
                tag_text = tags_to_text(fragment.tags)
                if not tag_text:
                    continue
                fragment.embedding = get_embedding(tag_text, api_key)
                fragment.is_confirmed = True
                fragment.save()
                confirmed_ids.append(str(fragment.id))
            except Exception:
                logger.exception(f'Vectorization failed for fragment {fragment.id}')
                error_ids.append(str(fragment.id))

        return Response({
            'confirmed': len(confirmed_ids),
            'errors': len(error_ids),
            'error_ids': error_ids,
        })


# ── Fragment endpoints ────────────────────────────────────────────────────────

class FragmentListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        character_id = request.query_params.get('character_id')
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
            fragment.embedding = None
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

        try:
            provider = _get_provider(llm_config)
            tags = infer_tags(fragment.text, provider)
        except Exception as e:
            logger.exception('Tag inference failed')
            return Response({'error': f'标签推断失败：{str(e)}'}, status=500)

        fragment.tags = tags
        fragment.is_confirmed = False
        fragment.embedding = None
        fragment.save()
        return Response(FragmentSerializer(fragment).data)


class FragmentConfirmView(APIView):
    """POST /api/examples/fragments/:id/confirm/ — 单片段向量化入库"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, fragment_id):
        fragment = get_object_or_404(Fragment, id=fragment_id, owner=request.user)

        if not fragment.tags:
            return Response({'error': '请先为片段打标签再确认入库'}, status=400)

        try:
            llm_config = _get_llm_config(request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=400)

        if llm_config.provider != 'gemini':
            return Response({
                'error': (
                    '向量化需要 Gemini API Key。'
                    '请在设置页切换为 Gemini 后重试。'
                )
            }, status=400)

        try:
            api_key = decrypt_key(llm_config.api_key_encrypted)
            tag_text = tags_to_text(fragment.tags)
            if not tag_text:
                return Response({'error': '标签内容为空，无法向量化'}, status=400)
            fragment.embedding = get_embedding(tag_text, api_key)
            fragment.is_confirmed = True
            fragment.save()
        except Exception as e:
            logger.exception('Fragment vectorization failed')
            return Response({'error': f'向量化失败：{str(e)}'}, status=500)

        return Response(FragmentSerializer(fragment).data)