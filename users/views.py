from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth.models import User
from .models import UserLLMConfig
from .serializers import RegisterSerializer, UserSerializer, LLMConfigSerializer


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class LLMConfigView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            config = request.user.llm_config
            return Response(LLMConfigSerializer(config).data)
        except UserLLMConfig.DoesNotExist:
            return Response({'hasKey': False, 'provider': None})

    def post(self, request):
        try:
            config = request.user.llm_config
            serializer = LLMConfigSerializer(config, data=request.data, context={'request': request})
        except UserLLMConfig.DoesNotExist:
            serializer = LLMConfigSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'hasKey': True, 'provider': request.data.get('provider')})