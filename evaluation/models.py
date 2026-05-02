from django.db import models
from django.contrib.auth.models import User
import uuid

from characters.models import BaseCard, AUMod
from logs.models import LlmCallLog


class ConsistencyScore(models.Model):
    """
    LLM-as-judge 一致性评估结果。

    关联关系：
      generation_log  → 被评估的那次生成调用的 LlmCallLog
      judge_call_log  → 本次评估调用的 LlmCallLog（judge 自身）
      两者都通过 generation_id 关联，便于 admin 追溯完整链路。

    评分权重（evaluation.md §4）：
      只有 LLM 分时直接使用 LLM 分；
      同时有用户评分时：final = user_rating × 0.7 + score × 0.3
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    character = models.ForeignKey(
        BaseCard, null=True, on_delete=models.SET_NULL, related_name='consistency_scores'
    )
    au_mod = models.ForeignKey(
        AUMod, null=True, blank=True, on_delete=models.SET_NULL
    )
    generation_log = models.ForeignKey(
        LlmCallLog, null=True, on_delete=models.SET_NULL,
        related_name='consistency_scores',
        help_text='被评估的生成调用记录'
    )
    judge_call_log = models.ForeignKey(
        LlmCallLog, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='judge_scores',
        help_text='本次 judge 调用记录'
    )

    generated_text = models.TextField(help_text='被评估的生成文本')
    score = models.IntegerField(help_text='LLM judge 评分，0-10 整数')
    judge_reasoning = models.TextField(help_text='judge 的评分理由')
    judge_model = models.CharField(max_length=64, help_text='执行评估的模型名')

    # ── Scaffold: 用户人工评分（evaluation.md §4，个性化校准前置）────────
    # null 表示用户尚未人工评分
    user_rating = models.IntegerField(
        null=True, blank=True,
        help_text='用户人工评分，0-10；null 表示未评分'
    )
    user_rated_at = models.DateTimeField(
        null=True, blank=True,
        help_text='用户提交人工评分的时间'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'character']),
            models.Index(fields=['created_at']),
            models.Index(fields=['score']),
        ]

    def __str__(self):
        char_name = self.character.name if self.character else '已删除角色'
        return f'{char_name} {self.score}/10 ({self.created_at.date()})'

    @property
    def final_score(self) -> float:
        """加权最终分数。有用户评分时优先参考用户判断。"""
        if self.user_rating is not None:
            return round(self.user_rating * 0.7 + self.score * 0.3, 1)
        return float(self.score)