from django.contrib import admin
from django.db.models import Avg, Count, Sum, Q
from django.utils import timezone
from .models import RestApiLog, LlmCallLog, VectorSearchLog


@admin.register(RestApiLog)
class RestApiLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'method', 'path', 'status_code', 'latency_ms', 'user']
    list_filter = ['method', 'status_code']
    date_hierarchy = 'created_at'
    readonly_fields = [f.name for f in RestApiLog._meta.fields]

    def changelist_view(self, request, extra_context=None):
        today = timezone.now().date()
        qs = RestApiLog.objects.filter(created_at__date=today)
        total = qs.count()
        extra_context = extra_context or {}
        extra_context['summary'] = {
            '今日请求总数': total,
            '平均响应时间': f"{qs.aggregate(v=Avg('latency_ms'))['v'] or 0:.0f} ms",
            '4xx/5xx 错误数': qs.filter(status_code__gte=400).count(),
        }
        return super().changelist_view(request, extra_context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LlmCallLog)
class LlmCallLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'feature', 'model', 'latency_ms',
                    'prompt_tokens', 'completion_tokens', 'status']
    list_filter = ['status', 'feature', 'model']
    date_hierarchy = 'created_at'
    readonly_fields = [f.name for f in LlmCallLog._meta.fields]

    def changelist_view(self, request, extra_context=None):
        today = timezone.now().date()
        qs = LlmCallLog.objects.filter(created_at__date=today)
        agg = qs.aggregate(
            avg_latency=Avg('latency_ms'),
            total_prompt=Sum('prompt_tokens'),
            total_completion=Sum('completion_tokens'),
        )
        total = qs.count()
        error_count = qs.filter(status='error').count()
        extra_context = extra_context or {}
        extra_context['summary'] = {
            '今日调用总数': total,
            '平均 latency': f"{agg['avg_latency'] or 0:.0f} ms",
            '错误率': f"{error_count / max(total, 1) * 100:.1f}%",
            '今日 prompt tokens': agg['total_prompt'] or 0,
            '今日 completion tokens': agg['total_completion'] or 0,
        }
        return super().changelist_view(request, extra_context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(VectorSearchLog)
class VectorSearchLogAdmin(admin.ModelAdmin):
    list_display = [
        'created_at', 'feature', 'character_id',
        'top_k', 'result_count', 'top_similarity', 'latency_ms', 'style_injected',
    ]
    list_filter = ['feature', 'style_injected']
    date_hierarchy = 'created_at'
    readonly_fields = [f.name for f in VectorSearchLog._meta.fields]

    def changelist_view(self, request, extra_context=None):
        today = timezone.now().date()
        qs = VectorSearchLog.objects.filter(created_at__date=today)
        agg = qs.aggregate(
            avg_latency=Avg('latency_ms'),
            avg_similarity=Avg('top_similarity'),
        )
        total = qs.count()
        injected = qs.filter(style_injected=True).count()
        extra_context = extra_context or {}
        extra_context['summary'] = {
            '今日检索总数': total,
            '平均 latency': f"{agg['avg_latency'] or 0:.0f} ms",
            '平均最高相似度': f"{agg['avg_similarity'] or 0:.3f}",
            '风格注入率': f"{injected / max(total, 1) * 100:.1f}%",
        }
        return super().changelist_view(request, extra_context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False