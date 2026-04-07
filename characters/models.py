from django.db import models
from django.contrib.auth.models import User
import uuid


class BaseCard(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='characters')

    # meta
    name = models.CharField(max_length=100)
    fandom = models.CharField(max_length=200, blank=True)

    # gender
    # 存储用户选择的代词选项：他 / 她 / 它 / 祂 / other
    # 选 other 时，gender_type 和 gender_pronoun 必填
    gender = models.CharField(max_length=10, blank=True)
    gender_type = models.CharField(
        max_length=50, blank=True,
        help_text='仅 gender=other 时填写，例：双性、无性别、流性别'
    )
    gender_pronoun = models.CharField(
        max_length=20, blank=True,
        help_text='仅 gender=other 时填写，例：他们、TA、祂们'
    )

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

    # 选择性继承
    # 结构: {"quick_labels": ["id1", "id2"], "forbidden_behaviors": ["id3"]}
    inherit_exclude = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.au_name} ({self.character.name})'


class Relationship(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='relationships',
        help_text='用于访问控制，不代表关系从属于该用户之外的某个角色'
    )
    overall_tone = models.TextField(blank=True, help_text='关系整体基调，客观描述，不预设视角')
    participants = models.ManyToManyField(
        BaseCard,
        through='RelationshipMembership',
        related_name='relationships'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'Relationship ({self.id})'


class RelationshipMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    relationship = models.ForeignKey(
        Relationship, on_delete=models.CASCADE, related_name='memberships'
    )
    character = models.ForeignKey(
        BaseCard, on_delete=models.CASCADE, related_name='memberships'
    )

    # 结构: [{"calls": "陈默", "as": ["老陈", "陈队"]}, ...]
    nicknames_for_others = models.JSONField(default=list, blank=True)

    quick_labels = models.JSONField(default=list, blank=True)
    forbidden_behaviors = models.JSONField(default=list, blank=True)
    inherit_exclude = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['relationship', 'character']]
        ordering = ['created_at']

    def __str__(self):
        return f'{self.character.name} in Relationship({self.relationship_id})'