from django.urls import path
from .views import (
    ArticleListView, ArticleDetailView, ArticleSegmentView, ArticleBatchConfirmView,
    FragmentListView, FragmentDetailView, FragmentInferTagsView, FragmentConfirmView,
)

urlpatterns = [
    path('articles/', ArticleListView.as_view(), name='article-list'),
    path('articles/<uuid:article_id>/', ArticleDetailView.as_view(), name='article-detail'),
    path('articles/<uuid:article_id>/segment/', ArticleSegmentView.as_view(), name='article-segment'),
    path('articles/<uuid:article_id>/confirm-all/', ArticleBatchConfirmView.as_view(), name='article-confirm-all'),
    path('fragments/', FragmentListView.as_view(), name='fragment-list'),
    path('fragments/<uuid:fragment_id>/', FragmentDetailView.as_view(), name='fragment-detail'),
    path('fragments/<uuid:fragment_id>/infer-tags/', FragmentInferTagsView.as_view(), name='fragment-infer-tags'),
    path('fragments/<uuid:fragment_id>/confirm/', FragmentConfirmView.as_view(), name='fragment-confirm'),
]