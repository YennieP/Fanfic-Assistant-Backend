from rest_framework_nested import routers
from django.urls import path, include
from .views import (
    BaseCardViewSet, AUModViewSet,
    RelationshipViewSet, RelationshipMembershipViewSet,
)

router = routers.DefaultRouter()
router.register(r'characters', BaseCardViewSet, basename='character')
router.register(r'relationships', RelationshipViewSet, basename='relationship')  # Fix 1

characters_router = routers.NestedDefaultRouter(router, r'characters', lookup='character')
characters_router.register(r'mods', AUModViewSet, basename='character-mods')

# Fix 1: memberships 嵌套在 relationships 下
relationships_router = routers.NestedDefaultRouter(router, r'relationships', lookup='relationship')
relationships_router.register(r'memberships', RelationshipMembershipViewSet, basename='relationship-memberships')

urlpatterns = [
    path('', include(router.urls)),
    path('', include(characters_router.urls)),
    path('', include(relationships_router.urls)),
]