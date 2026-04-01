from django.contrib import admin
from .models import BaseCard, AUMod, Relationship, RelationshipMembership

@admin.register(BaseCard)
class BaseCardAdmin(admin.ModelAdmin):
    list_display = ['name', 'fandom', 'mbti', 'owner', 'updated_at']
    search_fields = ['name', 'fandom']
    list_filter = ['mbti']

@admin.register(AUMod)
class AUModAdmin(admin.ModelAdmin):
    list_display = ['au_name', 'character', 'updated_at']
    search_fields = ['au_name']

@admin.register(Relationship)
class RelationshipAdmin(admin.ModelAdmin):
    list_display = ['id', 'owner', 'overall_tone', 'created_at']

@admin.register(RelationshipMembership)
class RelationshipMembershipAdmin(admin.ModelAdmin):
    list_display = ['relationship', 'character', 'created_at']