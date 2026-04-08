from django.urls import path
from .views import EvaluateView

urlpatterns = [
    path('score/', EvaluateView.as_view()),
]