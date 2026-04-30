from rest_framework import serializers
from .models import Article, Fragment


class FragmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fragment
        fields = [
            'id', 'article', 'character', 'text', 'tags',
            'is_confirmed', 'order', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'article', 'character', 'is_confirmed',
            'order', 'created_at', 'updated_at',
        ]


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