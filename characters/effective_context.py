"""BaseCard、AU Mod 与关系 Mod 的统一有效上下文。"""

from dataclasses import dataclass
from typing import Sequence
from uuid import UUID

from django.db.models import Count

from .models import AUMod, BaseCard, Relationship, RelationshipMembership


@dataclass(frozen=True)
class EffectiveListItem:
    id: str
    content: str


@dataclass(frozen=True)
class EffectiveRelationshipContext:
    id: str
    participant_count: int
    overall_tone: str
    quick_labels: tuple[EffectiveListItem, ...]
    forbidden_behaviors: tuple[EffectiveListItem, ...]
    nicknames_for_others: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class EffectiveCharacterContext:
    quick_labels: tuple[EffectiveListItem, ...]
    forbidden_behaviors: tuple[EffectiveListItem, ...]
    relationships: tuple[EffectiveRelationshipContext, ...]

    @property
    def active_relationship_ids(self) -> tuple[str, ...]:
        return tuple(relationship.id for relationship in self.relationships)


@dataclass(frozen=True)
class _RelationshipLayer:
    relationship: Relationship
    membership: RelationshipMembership
    participant_count: int


def _clean_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ''


def _normalize_items(value: object) -> list[EffectiveListItem]:
    if not isinstance(value, list):
        return []

    result = []
    for raw_item in value:
        if not isinstance(raw_item, dict):
            continue
        content = _clean_text(raw_item.get('content'))
        if not content:
            continue
        result.append(EffectiveListItem(
            id=_clean_text(raw_item.get('id')),
            content=content,
        ))
    return result


def _exclude_ids(value: object, field_name: str) -> set[str]:
    if not isinstance(value, dict):
        return set()
    raw_ids = value.get(field_name)
    if not isinstance(raw_ids, list):
        return set()
    return {cleaned for raw in raw_ids if (cleaned := _clean_text(raw))}


def _normalize_nicknames(
    value: object,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(value, list):
        return ()

    result = []
    seen_calls = set()
    for raw_item in value:
        if not isinstance(raw_item, dict):
            continue
        calls = _clean_text(raw_item.get('calls'))
        raw_names = raw_item.get('as')
        if not calls or not isinstance(raw_names, list) or calls.casefold() in seen_calls:
            continue
        names = []
        seen_names = set()
        for raw_name in raw_names:
            name = _clean_text(raw_name)
            key = name.casefold()
            if name and key not in seen_names:
                names.append(name)
                seen_names.add(key)
        if names:
            result.append((calls, tuple(names)))
            seen_calls.add(calls.casefold())
    return tuple(result)


def _select_priority_groups(
    groups: Sequence[Sequence[EffectiveListItem]],
) -> list[list[EffectiveListItem]]:
    """按高到低优先级去重；同组保持原顺序。"""
    seen_ids: set[str] = set()
    seen_contents: set[str] = set()
    selected_groups = []

    for group in groups:
        selected = []
        for item in group:
            content_key = item.content.casefold()
            if (
                (item.id and item.id in seen_ids)
                or content_key in seen_contents
            ):
                continue
            selected.append(item)
            if item.id:
                seen_ids.add(item.id)
            seen_contents.add(content_key)
        selected_groups.append(selected)

    return selected_groups


def _base_au_items(
    base_value: object,
    au_value: object,
    au_excludes: object,
    field_name: str,
) -> list[EffectiveListItem]:
    excluded_ids = _exclude_ids(au_excludes, field_name)
    base_items = [
        item for item in _normalize_items(base_value)
        if not item.id or item.id not in excluded_ids
    ]
    au_items = _normalize_items(au_value)

    # AU 比 BaseCard 特异，因此先选 AU，再过滤 BaseCard；最终展示仍按 Base → AU。
    selected_au, selected_base = _select_priority_groups([au_items, base_items])
    return [*selected_base, *selected_au]


def _compose_field(
    inherited_items: list[EffectiveListItem],
    relationship_layers: Sequence[_RelationshipLayer],
    field_name: str,
) -> tuple[list[EffectiveListItem], list[list[EffectiveListItem]]]:
    relationship_excludes = set().union(*(
        _exclude_ids(layer.membership.inherit_exclude, field_name)
        for layer in relationship_layers
    )) if relationship_layers else set()
    inherited_items = [
        item for item in inherited_items
        if not item.id or item.id not in relationship_excludes
    ]
    relationship_items = [
        _normalize_items(getattr(layer.membership, field_name))
        for layer in relationship_layers
    ]

    # relationship_layers 已按高特异性在前；所有 relationship 都高于 AU/Base。
    selected = _select_priority_groups([*relationship_items, inherited_items])
    return selected[-1], selected[:-1]


def compose_effective_character_context(
    character: BaseCard,
    au_mod: AUMod | None,
    relationship_layers: Sequence[_RelationshipLayer] = (),
) -> EffectiveCharacterContext:
    quick_labels = _base_au_items(
        character.quick_labels,
        au_mod.quick_labels if au_mod else [],
        au_mod.inherit_exclude if au_mod else {},
        'quick_labels',
    )
    forbidden_behaviors = _base_au_items(
        character.forbidden_behaviors,
        au_mod.forbidden_behaviors if au_mod else [],
        au_mod.inherit_exclude if au_mod else {},
        'forbidden_behaviors',
    )

    quick_labels, relationship_labels = _compose_field(
        quick_labels, relationship_layers, 'quick_labels',
    )
    forbidden_behaviors, relationship_forbidden = _compose_field(
        forbidden_behaviors, relationship_layers, 'forbidden_behaviors',
    )

    relationships = tuple(
        EffectiveRelationshipContext(
            id=str(layer.relationship.id),
            participant_count=layer.participant_count,
            overall_tone=_clean_text(layer.relationship.overall_tone),
            quick_labels=tuple(relationship_labels[index]),
            forbidden_behaviors=tuple(relationship_forbidden[index]),
            nicknames_for_others=_normalize_nicknames(
                layer.membership.nicknames_for_others,
            ),
        )
        for index, layer in enumerate(relationship_layers)
    )

    return EffectiveCharacterContext(
        quick_labels=tuple(quick_labels),
        forbidden_behaviors=tuple(forbidden_behaviors),
        relationships=relationships,
    )


def _valid_relationship_ids(values: object) -> list[UUID]:
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    seen = set()
    for value in values:
        try:
            relationship_id = UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            continue
        if relationship_id not in seen:
            result.append(relationship_id)
            seen.add(relationship_id)
    return result


def _relationship_layers(
    character: BaseCard,
    active_relationship_ids: object,
) -> list[_RelationshipLayer]:
    relationship_ids = _valid_relationship_ids(active_relationship_ids)
    if not relationship_ids:
        return []

    relationships = list(
        Relationship.objects.filter(
            id__in=relationship_ids,
            owner=character.owner,
        ).annotate(_participant_count=Count('memberships', distinct=True))
    )
    memberships = {
        membership.relationship_id: membership
        for membership in RelationshipMembership.objects.filter(
            relationship__in=relationships,
            character=character,
        )
    }

    layers = [
        _RelationshipLayer(
            relationship=relationship,
            membership=memberships[relationship.id],
            participant_count=relationship._participant_count,
        )
        for relationship in relationships
        if relationship.id in memberships
    ]
    # 参与人数越少，激活条件越窄、特异性越高；同特异性不依赖 updated_at。
    return sorted(
        layers,
        key=lambda layer: (
            layer.participant_count,
            layer.relationship.created_at,
            str(layer.relationship.id),
        ),
    )


def build_effective_character_context(
    character: BaseCard,
    au_mod: AUMod | None,
    active_relationship_ids: object,
) -> EffectiveCharacterContext:
    return compose_effective_character_context(
        character,
        au_mod,
        _relationship_layers(character, active_relationship_ids),
    )
