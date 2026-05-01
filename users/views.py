from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import status
from django.contrib.auth.models import User

from .models import UserLLMConfig, UserProviderKey, SUPPORTED_PROVIDERS
from .serializers import UserSerializer, RegisterSerializer, LLMConfigSerializer
from .encryption import encrypt_key


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({'message': '注册成功'}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


def _get_or_create_config(user) -> UserLLMConfig:
    config, _ = UserLLMConfig.objects.get_or_create(
        user=user, defaults={'provider': 'gemini'}
    )
    return config


class LLMConfigView(APIView):
    """
    GET  /api/auth/llm-config/     — 返回当前激活 provider + 各 provider hasKey 状态
    POST /api/auth/llm-config/     — 保存或更新某 provider 的 Key（body: {provider, apiKey}）
    PATCH /api/auth/llm-config/    — 切换激活 provider（body: {provider}，必须已有 Key）
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        config = _get_or_create_config(request.user)
        return Response(LLMConfigSerializer(config).data)

    def post(self, request):
        """保存或更新某 provider 的 API Key。"""
        provider = request.data.get('provider', '').strip()
        api_key = request.data.get('api_key', '').strip()

        if provider not in SUPPORTED_PROVIDERS:
            return Response(
                {'error': f'不支持的 provider，可选：{SUPPORTED_PROVIDERS}'},
                status=400
            )
        if not api_key:
            return Response({'error': 'API Key 不能为空'}, status=400)

        encrypted = encrypt_key(api_key)
        UserProviderKey.objects.update_or_create(
            user=request.user,
            provider=provider,
            defaults={'api_key_encrypted': encrypted},
        )

        # 保存 Key 后自动切换到该 provider
        config = _get_or_create_config(request.user)
        config.provider = provider
        config.save()

        return Response(LLMConfigSerializer(config).data)

    def patch(self, request):
        """切换激活 provider（不改变任何 Key）。"""
        provider = request.data.get('provider', '').strip()

        if provider not in SUPPORTED_PROVIDERS:
            return Response(
                {'error': f'不支持的 provider，可选：{SUPPORTED_PROVIDERS}'},
                status=400
            )

        has_key = UserProviderKey.objects.filter(
            user=request.user, provider=provider
        ).exists()
        if not has_key:
            return Response(
                {'error': f'{provider} 尚未配置 API Key，请先保存 Key'},
                status=400
            )

        config = _get_or_create_config(request.user)
        config.provider = provider
        config.save()

        return Response(LLMConfigSerializer(config).data)