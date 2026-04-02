from django.urls import path
from .views import GenerateStreamView

urlpatterns = [
    path('generate/stream/', GenerateStreamView.as_view(), name='generate-stream'),
]