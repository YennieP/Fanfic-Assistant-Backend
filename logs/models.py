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


class VectorSearchLog(models.Model):
    """记录每次 pgvector 向量检索调用，用于 Phase 2 ablation study 调试"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(null=True, db_index=True)
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    # 关联触发此次检索的生成调用（与 LlmCallLog.generation_id 对齐）
    generation_id = models.UUIDField(null=True, blank=True, db_index=True)

    # 功能模块：style_retrieval（生成时风格检索）/ candidate_panel（候选面板，当前两者同一次调用）
    feature = models.CharField(max_length=64)

    # 检索时的角色过滤条件，便于 ablation 按角色分析
    # 存 UUID 而非 FK，角色删除后历史记录仍完整保留
    character_id = models.UUIDField(null=True, blank=True)

    # 向量化前的查询文字（场景标签拼成的描述，如「争执发生，愤怒·高」）
    # 不含用户创作内容，PII 风险极低，存下来对 ablation 调试有价值
    query_text = models.TextField()

    # 检索参数与结果
    top_k = models.IntegerField()                              # 请求的候选数量（当前固定 5）
    result_count = models.IntegerField()                       # 实际返回结果数（可能 < top_k）
    top_similarity = models.FloatField(null=True, blank=True)  # 最高相似度，无结果时为 null
    latency_ms = models.IntegerField()

    # 是否有结果被注入 prompt（result_count > 0）
    style_injected = models.BooleanField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['generation_id']),
            models.Index(fields=['feature', 'character_id']),
        ]

    def __str__(self):
        return f'{self.feature} top_k={self.top_k} results={self.result_count} {self.latency_ms}ms'