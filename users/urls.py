from django.urls import path
from .views import RegisterView, MeView, LLMConfigView

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('me/', MeView.as_view(), name='me'),
    path('llm-config/', LLMConfigView.as_view(), name='llm-config'),
]