from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from .models import UserLLMConfig
from .encryption import encrypt_key


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password', 'password_confirm']

    def validate(self, data):
        if data['password'] != data['password_confirm']:
            raise serializers.ValidationError({'password': '两次密码不一致'})
        return data

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        return User.objects.create_user(**validated_data)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']


class LLMConfigSerializer(serializers.ModelSerializer):
    # write_only：前端可以发来 api_key，但任何 response 都不返回它
    api_key = serializers.CharField(write_only=True, required=False)
    has_key = serializers.SerializerMethodField()

    class Meta:
        model = UserLLMConfig
        fields = ['provider', 'api_key', 'has_key', 'updated_at']
        read_only_fields = ['has_key', 'updated_at']

    def get_has_key(self, obj):
        return bool(obj.api_key_encrypted)

    def validate(self, data):
        api_key = data.get('api_key', '')
        provider = data.get('provider', getattr(self.instance, 'provider', ''))
        if api_key:
            if provider == 'anthropic' and not api_key.startswith('sk-ant-'):
                raise serializers.ValidationError({'api_key': 'Anthropic Key 应以 sk-ant- 开头'})
            if provider == 'gemini' and not api_key.startswith('AIza'):
                raise serializers.ValidationError({'api_key': 'Gemini Key 应以 AIza 开头'})
        return data

    def create(self, validated_data):
        api_key = validated_data.pop('api_key', '')
        validated_data['api_key_encrypted'] = encrypt_key(api_key)
        return UserLLMConfig.objects.create(user=self.context['request'].user, **validated_data)

    def update(self, instance, validated_data):
        if 'api_key' in validated_data:
            instance.api_key_encrypted = encrypt_key(validated_data.pop('api_key'))
        instance.provider = validated_data.get('provider', instance.provider)
        instance.save()
        return instance