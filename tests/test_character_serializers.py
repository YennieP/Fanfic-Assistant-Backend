"""角色标签与红线写入格式的 serializer 契约。"""

import pytest

from characters.serializers import (
    AUModSerializer,
    BaseCardSerializer,
    RelationshipMembershipSerializer,
)


SERIALIZER_CLASSES = (
    BaseCardSerializer,
    AUModSerializer,
    RelationshipMembershipSerializer,
)
LIST_ITEM_FIELDS = ('quick_labels', 'forbidden_behaviors')


@pytest.mark.parametrize('serializer_class', SERIALIZER_CLASSES)
@pytest.mark.parametrize('field_name', LIST_ITEM_FIELDS)
@pytest.mark.parametrize(
    'invalid_value',
    (
        'legacy string',
        {'id': 'not-a-list', 'content': 'broken'},
        ['legacy string'],
        [{'id': 'valid', 'content': 'kept'}, 42],
    ),
)
def test_context_list_fields_reject_non_object_items(
    serializer_class,
    field_name,
    invalid_value,
):
    serializer = serializer_class(
        data={field_name: invalid_value},
        partial=True,
    )

    assert not serializer.is_valid()
    assert field_name in serializer.errors


@pytest.mark.parametrize('serializer_class', SERIALIZER_CLASSES)
@pytest.mark.parametrize('field_name', LIST_ITEM_FIELDS)
@pytest.mark.parametrize(
    'valid_value',
    (
        [],
        [{'id': 'item-1', 'content': '克制'}],
    ),
)
def test_context_list_fields_accept_object_items(
    serializer_class,
    field_name,
    valid_value,
):
    serializer = serializer_class(
        data={field_name: valid_value},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data[field_name] == valid_value
