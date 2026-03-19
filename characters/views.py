from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import BaseCard, AUMod
from .serializers import BaseCardSerializer, BaseCardListSerializer, AUModSerializer


class BaseCardViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BaseCardSerializer

    def get_queryset(self):
        # 只返回当前用户的角色卡
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