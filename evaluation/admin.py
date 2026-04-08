from django.contrib import admin
from django.db.models import Avg
from django.utils.timezone import now
from .models import ConsistencyScore


@admin.register(ConsistencyScore)
class ConsistencyScoreAdmin(admin.ModelAdmin):
    list_display = ['character', 'score', 'judge_model', 'user', 'created_at']
    list_filter = ['score', 'judge_model']
    readonly_fields = [
        'id', 'user', 'character', 'au_mod',
        'generation_log', 'judge_call_log',
        'score', 'judge_reasoning', 'judge_model', 'created_at',
    ]
    search_fields = ['character__name', 'judge_reasoning']

    def changelist_view(self, request, extra_context=None):
        qs = self.model.objects.filter(created_at__date=now().date())
        count = qs.count()
        avg = qs.aggregate(avg=Avg('score'))['avg']
        extra_context = extra_context or {}
        extra_context['summary'] = {
            'today_count': count,
            'today_avg_score': round(avg, 1) if avg is not None else '—',
        }
        return super().changelist_view(request, extra_context)