from django.db import models
from django.contrib.auth.models import User
import uuid


class RestApiLog(models.Model):
    """记录自己的 Django REST API 被调用的情况"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(db_index=True)
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    method = models.CharField(max_length=8)
    path = models.CharField(max_length=256)
    status_code = models.IntegerField()
    latency_ms = models.IntegerField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['path', 'status_code']),
        ]

    def __str__(self):
        return f'{self.method} {self.path} {self.status_code}'


class LlmCallLog(models.Model):
    """记录每次对外部 LLM API 的调用"""

    class Status(models.TextChoices):
        SUCCESS = 'success'
        ERROR = 'error'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(null=True, db_index=True)
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    # 业务调用的唯一标识，用于关联评估记录。
    # 生成调用：前端从 done 事件拿到后传给评估接口
    # 评估调用（judge）：内部生成用于查询本条记录
    generation_id = models.UUIDField(null=True, blank=True, db_index=True)

    feature = models.CharField(max_length=64)
    model = models.CharField(max_length=64)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    latency_ms = models.IntegerField()

    status = models.CharField(max_length=16, choices=Status.choices)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['feature', 'status']),
            models.Index(fields=['generation_id']),
        ]

    def __str__(self):
        return f'{self.feature} {self.status} {self.latency_ms}ms'