from rest_framework import viewsets, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .models import BaseCard, AUMod, Relationship, RelationshipMembership
from .serializers import (
    BaseCardSerializer, BaseCardListSerializer,
    AUModSerializer,
    RelationshipSerializer, RelationshipMembershipSerializer,
)
from core.taxonomy import TAXONOMY
from .models import BaseCard, AUMod, Relationship, RelationshipMembership, LabelHistory
from .serializers import (
    BaseCardSerializer, BaseCardListSerializer,
    AUModSerializer,
    RelationshipSerializer, RelationshipMembershipSerializer,
    LabelHistorySerializer,
)
from rest_framework.views import APIView


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
    关系实体独立 CRUD。
    GET    /api/relationships/       — 列出当前用户的所有关系实体
    POST   /api/relationships/       — 创建，body: {overall_tone, participant_ids: [uuid, uuid]}
    GET    /api/relationships/{id}/  — 详情（含 memberships）
    PATCH  /api/relationships/{id}/  — 更新 overall_tone
    DELETE /api/relationships/{id}/  — 删除
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

    GET   /api/relationships/{id}/memberships/        — 列出所有参与者 mod
    GET   /api/relationships/{id}/memberships/{id}/   — 单个参与者 mod 详情
    PATCH /api/relationships/{id}/memberships/{id}/   — 更新参与者 mod
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RelationshipMembershipSerializer
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        return RelationshipMembership.objects.filter(
            relationship__owner=self.request.user,
            relationship_id=self.kwargs['relationship_pk']
        ).select_related('character')


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def taxonomy_view(request):
    """
    GET /api/taxonomy/
    返回系统全局标签表。用于前端渲染预设标签选项。
    Phase 2 Example Library A 片段标注使用相同 schema。
    camel-case middleware 自动转换 key：scene_type → sceneType 等。
    """
    return Response(TAXONOMY)

class LabelHistoryView(APIView):
    """
    GET  /api/label-history/?field_type=relationship-quickLabels
         返回当前用户该字段的历史标签，按最近使用时间倒序，最多 100 条

    POST /api/label-history/
         body: { fieldType, label }
         新增或更新（upsert）一条历史标签，used_at 自动刷新
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        field_type = request.query_params.get('field_type', '')
        qs = LabelHistory.objects.filter(
            user=request.user,
            field_type=field_type,
        )[:100]
        return Response(LabelHistorySerializer(qs, many=True).data)

    def post(self, request):
        field_type = request.data.get('fieldType', '').strip()
        label = request.data.get('label', '').strip()
        if not field_type or not label:
            return Response(
                {'error': 'fieldType and label are required'},
                status=400
            )
        obj, _ = LabelHistory.objects.update_or_create(
            user=request.user,
            field_type=field_type,
            label=label,
            defaults={},   # used_at 由 auto_now 自动刷新
        )
        # auto_now 在 update_or_create 里不会自动触发，需要显式 save
        obj.save()
        return Response(LabelHistorySerializer(obj).data)