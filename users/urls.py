from django.urls import path
from .views import RegisterView, MeView, LLMConfigView, LLMConfigVerifyView

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('me/', MeView.as_view(), name='me'),
    path('llm-config/', LLMConfigView.as_view(), name='llm-config'),
    path(
        'llm-config/verify/',
        LLMConfigVerifyView.as_view(),
        name='llm-config-verify',
    ),
]
