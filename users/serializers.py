from rest_framework import serializers
from django.contrib.auth.models import User
from generation.providers.catalog import PROVIDER_CATALOG
from .models import UserLLMConfig, UserProviderKey, SUPPORTED_PROVIDERS


def _provider_capabilities(provider_name):
    definition = PROVIDER_CATALOG[provider_name]
    return {
        'video': definition.supports_video,
        'embedding': definition.supports_embedding,
    }


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['username', 'email', 'password']

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class LLMConfigSerializer(serializers.ModelSerializer):
    """
    返回 activeProvider，以及每个 provider 的 catalog 模型、hasKey、
    capabilities 和最近一次功能性验证状态。
    Key 明文永不出现在响应中。

    经 djangorestframework-camelcase 中间件转换后，前端收到：
    {
      activeProvider: 'gemini',
      providers: {
        anthropic: { hasKey: false, model: '...', verificationStatus: null, ... },
        gemini:    { hasKey: true,  model: '...', verificationStatus: 'valid', ... },
        groq:      { hasKey: false, model: '...', verificationStatus: null, ... },
      }
    }
    """
    active_provider = serializers.CharField(source='provider', read_only=True)
    providers = serializers.SerializerMethodField()

    class Meta:
        model = UserLLMConfig
        fields = ['active_provider', 'providers']

    def get_providers(self, obj):
        existing = {
            key.provider: key
            for key in UserProviderKey.objects.filter(user=obj.user)
        }
        return {
            p: self._provider_info(p, existing.get(p))
            for p in SUPPORTED_PROVIDERS
        }

    @staticmethod
    def _provider_info(provider_name, key):
        definition = PROVIDER_CATALOG[provider_name]
        return {
            'has_key': key is not None,
            'model': definition.model,
            'capabilities': _provider_capabilities(provider_name),
            'verification_status': (
                key.verification_status if key else None
            ),
            'verification_checked_at': (
                key.verification_checked_at if key else None
            ),
            'verification_model': (
                key.verification_model if key else None
            ),
            'verification_resolved_model': (
                key.verification_resolved_model if key else None
            ),
        }
