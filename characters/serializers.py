from rest_framework import serializers
from .models import BaseCard, AUMod


class AUModSerializer(serializers.ModelSerializer):
    class Meta:
        model = AUMod
        fields = [
            'id', 'au_name', 'setting',
            'role_title', 'role_age', 'role_current_situation',
            'quick_labels', 'forbidden_behaviors',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class BaseCardSerializer(serializers.ModelSerializer):
    au_mods = AUModSerializer(many=True, read_only=True)

    class Meta:
        model = BaseCard
        fields = [
            'id', 'name', 'fandom', 'card_author', 'version',
            'author_nicknames',
            'mbti', 'mbti_notes', 'core_values', 'core_fears', 'key_experiences',
            'quick_labels', 'behavioral_patterns', 'forbidden_behaviors',
            'default_state', 'emotional_triggers', 'emotion_expression_style', 'recovery_pattern',
            'conditions', 'physical_traits',
            'speech_style_custom_tags',
            'au_mods',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class BaseCardListSerializer(serializers.ModelSerializer):
    au_mods = AUModSerializer(many=True, read_only=True)

    class Meta:
        model = BaseCard
        fields = [
            'id', 'name', 'fandom', 'mbti',
            'quick_labels', 'au_mods',
            'created_at', 'updated_at',
        ]