from rest_framework_nested import routers
from django.urls import path, include
from .views import BaseCardViewSet, AUModViewSet

router = routers.DefaultRouter()
router.register(r'characters', BaseCardViewSet, basename='character')

characters_router = routers.NestedDefaultRouter(router, r'characters', lookup='character')
characters_router.register(r'mods', AUModViewSet, basename='character-mods')

urlpatterns = [
    path('', include(router.urls)),
    path('', include(characters_router.urls)),
]