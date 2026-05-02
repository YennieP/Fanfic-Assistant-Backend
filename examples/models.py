from django.db import models
from django.contrib.auth.models import User
from pgvector.django import VectorField
from characters.models import BaseCard
import uuid


class Article(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='articles')
    character = models.ForeignKey(BaseCard, on_delete=models.CASCADE, related_name='articles')
    title = models.CharField(max_length=200)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.title} ({self.character.name})'


class Fragment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fragments')
    article = models.ForeignKey(
        Article, on_delete=models.CASCADE, related_name='fragments',
        null=True, blank=True,
    )
    # null=True：多角色标注（B2方案）的父片段不绑定具体角色，客观存储场景信息
    # 现有数据不受影响（已有片段均有 character 值）
    character = models.ForeignKey(
        BaseCard, on_delete=models.CASCADE, related_name='fragments',
        null=True, blank=True,
    )
    text = models.TextField()
    tags = models.JSONField(default=dict, blank=True)

    # MVP A1 方案：存储标签组合文字的向量（768维，gemini-embedding-001）
    # Phase 3 升级接口：新增 content_embedding = VectorField(dimensions=768) 存内容向量
    # 检索层加权合并两个相似度分数，无需修改此字段
    embedding = VectorField(dimensions=768, null=True, blank=True)

    is_confirmed = models.BooleanField(default=False)
    order = models.IntegerField(default=0)

    # ── Scaffold: 连贯情绪弧（phase2.md §6）──────────────────────────────
    # 当前值均为 null，不影响现有逻辑
    # 同一情绪弧内的片段共享同一个 sequence_group 标识
    sequence_group = models.CharField(max_length=64, null=True, blank=True)
    # 弧内顺序，从 1 开始；非序列片段为 null
    sequence_order = models.PositiveIntegerField(null=True, blank=True)

    # ── Scaffold: 多角色片段标注（phase2.md §7，Phase 3 扩展）──────────
    # 父片段（character=null）存客观场景信息
    # 子片段（character=具体角色）存该角色的主观视角标签和权重
    parent_fragment = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='child_fragments',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'created_at']

    def __str__(self):
        char_name = self.character.name if self.character else '（父片段）'
        return f'Fragment({char_name}, confirmed={self.is_confirmed})'