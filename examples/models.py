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
    FRAGMENT_TYPE_CHOICES = [
        ('story', 'Story'),   # 有情节价值，可入库
        ('skip',  'Skip'),    # 章节标题、作者注等，不入库但保证全文覆盖
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fragments')
    article = models.ForeignKey(
        Article, on_delete=models.CASCADE, related_name='fragments',
        null=True, blank=True,
    )
    character = models.ForeignKey(
        BaseCard, on_delete=models.CASCADE, related_name='fragments',
        null=True, blank=True,
    )
    text = models.TextField()
    tags = models.JSONField(default=dict, blank=True)

    embedding = VectorField(dimensions=768, null=True, blank=True)

    is_confirmed = models.BooleanField(default=False)
    order = models.IntegerField(default=0)

    # 片段类型：story = 可入库；skip = 不入库（但纳入展示，保证全文覆盖不变量）
    # 默认 story 兼容存量数据，存量片段行为不变
    fragment_type = models.CharField(
        max_length=10,
        choices=FRAGMENT_TYPE_CHOICES,
        default='story',
    )

    # 在原文中的绝对行号（用于智能重切割：跳过已覆盖行，只切新增内容）
    # null 表示旧数据，不参与行号覆盖计算
    start_line = models.IntegerField(null=True, blank=True)
    end_line   = models.IntegerField(null=True, blank=True)

    # ── Scaffold: 连贯情绪弧（phase2.md §6）──────────────────────────────
    sequence_group = models.CharField(max_length=64, null=True, blank=True)
    sequence_order = models.PositiveIntegerField(null=True, blank=True)

    # ── Scaffold: 多角色片段标注（phase2.md §7，Phase 3 扩展）──────────
    parent_fragment = models.ForeignKey(
        'self',
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='child_fragments',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'created_at']

    def __str__(self):
        char_name = self.character.name if self.character else '（父片段）'
        return f'Fragment({char_name}, type={self.fragment_type}, confirmed={self.is_confirmed})'