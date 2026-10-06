from rest_framework import serializers
from .models import Article, Fragment


class FragmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fragment
        fields = [
            'id', 'article', 'character', 'text', 'tags',
            'is_confirmed', 'order',
            # Scaffold: 连贯情绪弧 + 多角色标注预留字段
            'sequence_group', 'sequence_order', 'parent_fragment',
            'created_at', 'updated_at',
            'fragment_type', 'start_line', 'end_line',
        ]
        read_only_fields = [
            'id', 'article', 'is_confirmed', 'order',
            'created_at', 'updated_at',
        ]
        # character 和 parent_fragment 不再是 read_only，
        # 允许创建父片段（character=null）时由前端控制


class ResolveConflictSerializer(serializers.Serializer):
    old_fragment_id = serializers.UUIDField()
    new_fragment_id = serializers.UUIDField()
    action = serializers.ChoiceField(choices=['keepOld', 'keepNew'])
    edited_new_text = serializers.CharField(allow_blank=True, trim_whitespace=False)

    def validate(self, attrs):
        if attrs['old_fragment_id'] == attrs['new_fragment_id']:
            raise serializers.ValidationError('新旧片段不能是同一个片段')
        return attrs


class MergeFragmentsSerializer(serializers.Serializer):
    keep_fragment_id = serializers.UUIDField()
    delete_fragment_id = serializers.UUIDField()
    keep_updated_at = serializers.DateTimeField()
    delete_updated_at = serializers.DateTimeField()
    merged_text = serializers.CharField(allow_blank=False, trim_whitespace=False)

    def validate(self, attrs):
        if attrs['keep_fragment_id'] == attrs['delete_fragment_id']:
            raise serializers.ValidationError('待合并片段不能是同一个片段')
        if not attrs['merged_text'].strip():
            raise serializers.ValidationError({'merged_text': '合并后的内容不能为空'})
        return attrs


class ArticleListSerializer(serializers.ModelSerializer):
    character_name = serializers.CharField(source='character.name', read_only=True)
    fragment_count = serializers.SerializerMethodField()
    confirmed_count = serializers.SerializerMethodField()

    class Meta:
        model = Article
        fields = [
            'id', 'character', 'character_name', 'title',
            'fragment_count', 'confirmed_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_fragment_count(self, obj):
        return obj.fragments.count()

    def get_confirmed_count(self, obj):
        return obj.fragments.filter(is_confirmed=True).count()


class ArticleSerializer(serializers.ModelSerializer):
    character_name = serializers.CharField(source='character.name', read_only=True)
    fragments = FragmentSerializer(many=True, read_only=True)
    fragment_count = serializers.SerializerMethodField()
    confirmed_count = serializers.SerializerMethodField()

    class Meta:
        model = Article
        fields = [
            'id', 'character', 'character_name', 'title', 'content',
            'fragment_count', 'confirmed_count', 'fragments',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_fragment_count(self, obj):
        return obj.fragments.count()

    def get_confirmed_count(self, obj):
        return obj.fragments.filter(is_confirmed=True).count()
