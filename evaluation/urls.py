from django.urls import path
from .views import EvaluateView, RateView

urlpatterns = [
    path('score/', EvaluateView.as_view()),
    path('score/<uuid:pk>/rate/', RateView.as_view()),
]