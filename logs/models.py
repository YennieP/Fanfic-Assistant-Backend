from django.db import models
from django.contrib.auth.models import User
import uuid


class RestApiLog(models.Model):
    """记录自己的 Django REST API 被调用的情况"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(db_index=True)
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    method = models.CharField(max_length=8)       # GET / POST / PATCH / DELETE
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
    request_id = models.UUIDField(null=True, db_index=True)  # nullable：非 HTTP 触发的调用
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    feature = models.CharField(max_length=64)     # 功能模块，e.g. "character_generate"
    model = models.CharField(max_length=64)        # e.g. "claude-sonnet-4-20250514"
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
        ]

    def __str__(self):
        return f'{self.feature} {self.status} {self.latency_ms}ms'