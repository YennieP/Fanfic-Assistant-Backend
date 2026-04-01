from rest_framework import serializers
from .models import BaseCard, AUMod, Relationship, RelationshipMembership


class AUModSerializer(serializers.ModelSerializer):
    class Meta:
        model = AUMod
        fields = [
            'id', 'au_name', 'setting',
            'role_title', 'role_age', 'role_current_situation',
            'quick_labels', 'forbidden_behaviors',
            'inherit_exclude',  # Fix 2
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class RelationshipMembershipSerializer(serializers.ModelSerializer):
    character_id = serializers.UUIDField(source='character.id', read_only=True)
    character_name = serializers.CharField(source='character.name', read_only=True)

    class Meta:
        model = RelationshipMembership
        fields = [
            'id', 'character_id', 'character_name',
            'nicknames_for_others',
            'quick_labels', 'forbidden_behaviors', 'inherit_exclude',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'character_id', 'character_name', 'created_at', 'updated_at']


class RelationshipSerializer(serializers.ModelSerializer):
    memberships = RelationshipMembershipSerializer(many=True, read_only=True)
    participant_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        help_text='创建时传入参与角色的 UUID 列表（至少2个）'
    )

    class Meta:
        model = Relationship
        fields = [
            'id', 'overall_tone',
            'participant_ids',
            'memberships',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, data):
        # 创建时必须提供参与者
        if not self.instance and not data.get('participant_ids'):
            raise serializers.ValidationError(
                {'participant_ids': '创建关系实体时必须指定参与者'}
            )
        if data.get('participant_ids') and len(data['participant_ids']) < 2:
            raise serializers.ValidationError(
                {'participant_ids': '关系实体至少需要2个参与者'}
            )
        return data

    def create(self, validated_data):
        participant_ids = validated_data.pop('participant_ids')
        relationship = Relationship.objects.create(**validated_data)
        # 只为属于该用户的角色创建 membership（安全过滤）
        characters = BaseCard.objects.filter(
            id__in=participant_ids,
            owner=validated_data['owner']
        )
        for character in characters:
            RelationshipMembership.objects.create(
                relationship=relationship,
                character=character
            )
        return relationship

    def update(self, instance, validated_data):
        # participant_ids 在更新时忽略（参与者管理待关系实体 UI 实现后处理）
        validated_data.pop('participant_ids', None)
        return super().update(instance, validated_data)


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