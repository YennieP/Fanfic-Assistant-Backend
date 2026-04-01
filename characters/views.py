from rest_framework import viewsets, permissions
from .models import BaseCard, AUMod, Relationship, RelationshipMembership
from .serializers import (
    BaseCardSerializer, BaseCardListSerializer,
    AUModSerializer,
    RelationshipSerializer, RelationshipMembershipSerializer,
)


class BaseCardViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BaseCardSerializer

    def get_queryset(self):
        return BaseCard.objects.filter(owner=self.request.user)

    def get_serializer_class(self):
        if self.action == 'list':
            return BaseCardListSerializer
        return BaseCardSerializer

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class AUModViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AUModSerializer

    def get_queryset(self):
        return AUMod.objects.filter(
            character__owner=self.request.user,
            character_id=self.kwargs['character_pk']
        )

    def perform_create(self, serializer):
        character = BaseCard.objects.get(
            id=self.kwargs['character_pk'],
            owner=self.request.user
        )
        serializer.save(character=character)


class RelationshipViewSet(viewsets.ModelViewSet):
    """
    Fix 1: 关系实体独立 CRUD。
    GET  /api/relationships/          — 列出当前用户的所有关系实体
    POST /api/relationships/          — 创建，body: {overall_tone, participant_ids: [uuid, uuid]}
    GET  /api/relationships/{id}/     — 详情（含 memberships）
    PATCH /api/relationships/{id}/    — 更新 overall_tone
    DELETE /api/relationships/{id}/   — 删除
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RelationshipSerializer

    def get_queryset(self):
        return Relationship.objects.filter(
            owner=self.request.user
        ).prefetch_related('memberships__character')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class RelationshipMembershipViewSet(viewsets.ModelViewSet):
    """
    每个参与者的 member mod 编辑。
    只允许 GET 和 PATCH——membership 随关系实体创建/删除，不单独新建或删除。

    GET   /api/relationships/{id}/memberships/          — 列出所有参与者 mod
    GET   /api/relationships/{id}/memberships/{id}/     — 单个参与者 mod 详情
    PATCH /api/relationships/{id}/memberships/{id}/     — 更新参与者 mod
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RelationshipMembershipSerializer
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        return RelationshipMembership.objects.filter(
            relationship__owner=self.request.user,
            relationship_id=self.kwargs['relationship_pk']
        ).select_related('character')