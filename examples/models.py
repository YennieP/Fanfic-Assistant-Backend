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
    character = models.ForeignKey(BaseCard, on_delete=models.CASCADE, related_name='fragments')
    text = models.TextField()
    tags = models.JSONField(default=dict, blank=True)

    # MVP A1 方案：存储标签组合文字的向量（768维，Gemini text-embedding-004）
    # Phase 3 升级接口：新增 content_embedding = VectorField(dimensions=768) 存内容向量
    # 检索层加权合并两个相似度分数，无需修改此字段
    embedding = VectorField(dimensions=768, null=True, blank=True)

    is_confirmed = models.BooleanField(default=False)
    order = models.IntegerField(default=0)  # 在文章中的显示顺序

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'created_at']

    def __str__(self):
        return f'Fragment({self.character.name}, confirmed={self.is_confirmed})'