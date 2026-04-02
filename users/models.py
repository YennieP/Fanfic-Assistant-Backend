from django.db import models
from django.contrib.auth.models import User


class UserLLMConfig(models.Model):
    class Provider(models.TextChoices):
        ANTHROPIC = 'anthropic', 'Anthropic (Claude)'
        GEMINI = 'gemini', 'Google (Gemini)'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='llm_config')
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.ANTHROPIC)
    api_key_encrypted = models.TextField()  # 永远不在 API response 里返回明文
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.username} - {self.provider}'