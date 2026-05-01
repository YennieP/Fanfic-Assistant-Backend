from django.db import models
from django.contrib.auth.models import User


SUPPORTED_PROVIDERS = ['anthropic', 'gemini', 'groq']


class UserProviderKey(models.Model):
    """
    每个 provider 独立存一行，Fernet 加密存储。
    Key 永不在任何 API response 中返回明文。
    """
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='provider_keys'
    )
    provider = models.CharField(max_length=20)
    api_key_encrypted = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['user', 'provider']]

    def __str__(self):
        return f'{self.user.username} / {self.provider}'


class UserLLMConfig(models.Model):
    """
    只存当前激活的 provider。
    Key 存在 UserProviderKey 里。
    """
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='llm_config'
    )
    provider = models.CharField(max_length=20, default='gemini')

    def __str__(self):
        return f'{self.user.username} → {self.provider}'