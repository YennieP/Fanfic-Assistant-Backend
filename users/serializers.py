from rest_framework import serializers
from django.contrib.auth.models import User
from .models import UserLLMConfig, UserProviderKey, SUPPORTED_PROVIDERS


# Scaffold: 每个 provider 的静态能力声明
# 与 generation/providers/base.py 中的 supports_video / supports_embedding 保持一致
# 前端据此动态显示/隐藏视频提取、embedding 相关入口，不再 hardcode provider 名称判断
_PROVIDER_CAPABILITIES = {
    'anthropic': {'video': False, 'embedding': False},
    'gemini':    {'video': True,  'embedding': True},
    'groq':      {'video': False, 'embedding': False},
    'cerebras':  {'video': False, 'embedding': False},
    'openrouter':{'video': False, 'embedding': False},
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
    返回 activeProvider 和每个 provider 的 hasKey 状态 + capabilities。
    Key 明文永不出现在响应中。

    经 djangorestframework-camelcase 中间件转换后，前端收到：
    {
      activeProvider: 'gemini',
      providers: {
        anthropic: { hasKey: false, capabilities: { video: false, embedding: false } },
        gemini:    { hasKey: true,  capabilities: { video: true,  embedding: true  } },
        groq:      { hasKey: false, capabilities: { video: false, embedding: false } },
      }
    }
    """
    active_provider = serializers.CharField(source='provider', read_only=True)
    providers = serializers.SerializerMethodField()

    class Meta:
        model = UserLLMConfig
        fields = ['active_provider', 'providers']

    def get_providers(self, obj):
        existing = set(
            UserProviderKey.objects
            .filter(user=obj.user)
            .values_list('provider', flat=True)
        )
        return {
            p: {
                'has_key': p in existing,
                'capabilities': _PROVIDER_CAPABILITIES.get(
                    p, {'video': False, 'embedding': False}
                ),
            }
            for p in SUPPORTED_PROVIDERS
        }