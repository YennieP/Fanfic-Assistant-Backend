from django.contrib import admin
from .models import BaseCard, AUMod

@admin.register(BaseCard)
class BaseCardAdmin(admin.ModelAdmin):
    list_display = ['name', 'fandom', 'mbti', 'owner', 'updated_at']
    search_fields = ['name', 'fandom']
    list_filter = ['mbti']

@admin.register(AUMod)
class AUModAdmin(admin.ModelAdmin):
    list_display = ['au_name', 'character', 'updated_at']
    search_fields = ['au_name']