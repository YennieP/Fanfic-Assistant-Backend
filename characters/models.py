from django.db import models
from django.contrib.auth.models import User
import uuid


class BaseCard(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='characters')

    # meta
    name = models.CharField(max_length=100)
    fandom = models.CharField(max_length=200, blank=True)
    card_author = models.CharField(max_length=100, blank=True)
    version = models.CharField(max_length=20, default='v1.0')
    author_nicknames = models.JSONField(default=list, blank=True)

    # personality_core
    mbti = models.CharField(max_length=4, blank=True)
    mbti_notes = models.TextField(blank=True)
    core_values = models.JSONField(default=list, blank=True)
    core_fears = models.JSONField(default=list, blank=True)
    key_experiences = models.JSONField(default=list, blank=True)

    # behavior_tags
    quick_labels = models.JSONField(default=list, blank=True)
    behavioral_patterns = models.JSONField(default=list, blank=True)
    forbidden_behaviors = models.JSONField(default=list, blank=True)

    # emotional_patterns
    default_state = models.TextField(blank=True)
    emotional_triggers = models.JSONField(default=list, blank=True)
    emotion_expression_style = models.TextField(blank=True)
    recovery_pattern = models.TextField(blank=True)

    # physical
    conditions = models.JSONField(default=list, blank=True)
    physical_traits = models.JSONField(default=list, blank=True)

    # speech_style
    speech_style_custom_tags = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.name} ({self.fandom})'


class AUMod(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    character = models.ForeignKey(BaseCard, on_delete=models.CASCADE, related_name='au_mods')

    au_name = models.CharField(max_length=200)
    setting = models.TextField(blank=True)

    # role
    role_title = models.CharField(max_length=200, blank=True)
    role_age = models.CharField(max_length=50, blank=True)
    role_current_situation = models.TextField(blank=True)

    # 追加字段
    quick_labels = models.JSONField(default=list, blank=True)
    forbidden_behaviors = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.au_name} ({self.character.name})'