import logging

from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import status
from django.contrib.auth.models import User
from django.utils import timezone

from generation.provider_verification import verify_provider_api_key
from generation.providers.base import CompleteResult, ProviderError
from generation.providers.catalog import get_provider_definition
from .models import UserLLMConfig, UserProviderKey, SUPPORTED_PROVIDERS
from .serializers import UserSerializer, RegisterSerializer, LLMConfigSerializer
from .encryption import decrypt_key, encrypt_key


logger = logging.getLogger(__name__)


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


def _get_saved_key(user, provider) -> UserProviderKey | None:
    return UserProviderKey.objects.filter(
        user=user,
        provider=provider,
    ).first()


def _require_enabled_provider(provider: str):
    definition = get_provider_definition(provider)
    if definition is None:
        raise ProviderError(
            '当前配置的 provider 不可用',
            code='provider_model_unavailable',
            http_status=503,
        )
    if not definition.enabled:
        raise ProviderError(
            '该服务商已暂时下线，请选择其他服务商',
            code=definition.disabled_reason or 'provider_disabled',
            http_status=503,
        )
    return definition


def _record_verification(
    key: UserProviderKey,
    provider: str,
    verification_status: str,
    result: CompleteResult | None = None,
) -> None:
    definition = get_provider_definition(provider)
    key.verification_status = verification_status
    key.verification_checked_at = timezone.now()
    key.verification_model = definition.model if definition else None
    key.verification_resolved_model = result.model if result else None
    key.save(update_fields=[
        'verification_status',
        'verification_checked_at',
        'verification_model',
        'verification_resolved_model',
    ])


def _verify_saved_key(key: UserProviderKey, provider: str) -> CompleteResult:
    try:
        api_key = decrypt_key(key.api_key_encrypted)
    except Exception as exc:
        logger.error(
            'Provider key decryption failed provider=%s type=%s',
            provider,
            type(exc).__name__,
        )
        error = ProviderError(
            'Provider 配置暂时不可用，请稍后重试',
            code='provider_temporarily_unavailable',
            http_status=503,
        )
        _record_verification(key, provider, error.code)
        raise error from None

    try:
        result = verify_provider_api_key(provider, api_key)
    except ProviderError as exc:
        _record_verification(key, provider, exc.code)
        raise

    _record_verification(key, provider, 'valid', result)
    return result


class LLMConfigView(APIView):
    """
    GET  /api/auth/llm-config/     — 返回当前激活 provider + 各 provider hasKey 状态
    POST /api/auth/llm-config/     — 验证后保存某 provider 的候选 Key
    PATCH /api/auth/llm-config/    — 验证已存 Key 后切换激活 provider
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
                {
                    'code': 'unsupported_provider',
                    'detail': f'不支持的 provider，可选：{SUPPORTED_PROVIDERS}',
                },
                status=400
            )
        if not api_key:
            return Response(
                {'code': 'no_api_key', 'detail': 'API Key 不能为空'},
                status=400,
            )

        definition = _require_enabled_provider(provider)

        # Provider network I/O must finish before the short database write.
        # A failed candidate never replaces the saved key or active provider.
        result = verify_provider_api_key(provider, api_key)
        encrypted = encrypt_key(api_key)
        with transaction.atomic():
            UserProviderKey.objects.update_or_create(
                user=request.user,
                provider=provider,
                defaults={
                    'api_key_encrypted': encrypted,
                    'verification_status': 'valid',
                    'verification_checked_at': timezone.now(),
                    'verification_model': definition.model,
                    'verification_resolved_model': result.model,
                },
            )

            # Preserve existing success behavior: a valid newly saved key
            # becomes active only after both verification and storage succeed.
            config = _get_or_create_config(request.user)
            config.provider = provider
            config.save(update_fields=['provider'])

        return Response(LLMConfigSerializer(config).data)

    def patch(self, request):
        """切换激活 provider（不改变任何 Key）。"""
        provider = request.data.get('provider', '').strip()

        if provider not in SUPPORTED_PROVIDERS:
            return Response(
                {
                    'code': 'unsupported_provider',
                    'detail': f'不支持的 provider，可选：{SUPPORTED_PROVIDERS}',
                },
                status=400
            )

        _require_enabled_provider(provider)

        key = _get_saved_key(request.user, provider)
        if key is None:
            return Response(
                {
                    'code': 'no_api_key',
                    'detail': f'{provider} 尚未配置 API Key，请先保存 Key',
                },
                status=400
            )

        # Verification is deliberately outside the database transaction.
        # On failure _verify_saved_key records only safe state and raises;
        # active provider remains unchanged.
        _verify_saved_key(key, provider)
        config = _get_or_create_config(request.user)
        config.provider = provider
        config.save(update_fields=['provider'])

        return Response(LLMConfigSerializer(config).data)


class LLMConfigVerifyView(APIView):
    """Explicitly verify one saved key without changing the active provider."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        provider = request.data.get('provider', '').strip()

        if provider not in SUPPORTED_PROVIDERS:
            return Response(
                {
                    'code': 'unsupported_provider',
                    'detail': f'不支持的 provider，可选：{SUPPORTED_PROVIDERS}',
                },
                status=400,
            )

        _require_enabled_provider(provider)

        key = _get_saved_key(request.user, provider)
        if key is None:
            return Response(
                {
                    'code': 'no_api_key',
                    'detail': f'{provider} 尚未配置 API Key，请先保存 Key',
                },
                status=400,
            )

        _verify_saved_key(key, provider)
        config = _get_or_create_config(request.user)
        return Response(LLMConfigSerializer(config).data)
