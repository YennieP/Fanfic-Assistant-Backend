from rest_framework_nested import routers
from django.urls import path, include
from .views import (
    BaseCardViewSet, AUModViewSet,
    RelationshipViewSet, RelationshipMembershipViewSet,
    taxonomy_view,
    LabelHistoryView,
)
from django.urls import path
from . import suggest, translate

router = routers.DefaultRouter()
router.register(r'characters', BaseCardViewSet, basename='character')
router.register(r'relationships', RelationshipViewSet, basename='relationship')

characters_router = routers.NestedDefaultRouter(router, r'characters', lookup='character')
characters_router.register(r'mods', AUModViewSet, basename='character-mods')

relationships_router = routers.NestedDefaultRouter(router, r'relationships', lookup='relationship')
relationships_router.register(r'memberships', RelationshipMembershipViewSet, basename='relationship-memberships')

urlpatterns = [
    # 具体路径必须在 router include 之前，防止被 {id}/ 模式拦截
    path('characters/suggest-completions/', suggest.SuggestCompletionsView.as_view(), name='character-suggest'),
    path('characters/<uuid:canonical_id>/translate/', translate.TranslateView.as_view(), name='character-translate'),
    path('characters/<uuid:canonical_id>/versions/', translate.VersionsView.as_view(), name='character-versions'),
    path('', include(router.urls)),
    path('', include(characters_router.urls)),
    path('', include(relationships_router.urls)),
    path('taxonomy/', taxonomy_view, name='taxonomy'),
    path('label-history/', LabelHistoryView.as_view(), name='label-history'),
]