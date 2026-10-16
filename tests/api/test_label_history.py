from datetime import timedelta

import pytest
from django.utils import timezone

from characters.models import LabelHistory


LABEL_HISTORY_URL = '/api/label-history/'


def _authenticate(api_client, user):
    api_client.force_authenticate(user=user)


@pytest.mark.django_db
@pytest.mark.parametrize(
    'payload',
    [
        {'label': '克制'},
        {'fieldType': 'relationship-quickLabels', 'label': '   '},
    ],
)
def test_label_history_missing_fields_use_standard_error_contract(
    api_client, user_factory, payload,
):
    user = user_factory()
    _authenticate(api_client, user)

    response = api_client.post(LABEL_HISTORY_URL, payload, format='json')

    assert response.status_code == 400
    assert response.json() == {
        'code': 'missing_required_fields',
        'detail': 'fieldType and label are required',
    }
    assert not LabelHistory.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_label_history_requires_authentication_without_writing(api_client):
    response = api_client.post(
        LABEL_HISTORY_URL,
        {'fieldType': 'relationship-quickLabels', 'label': '克制'},
        format='json',
    )

    assert response.status_code == 401
    assert response.json()['code'] == 'unauthorized'
    assert not LabelHistory.objects.exists()


@pytest.mark.django_db
def test_label_history_post_creates_then_refreshes_the_existing_row(
    api_client, user_factory,
):
    user = user_factory()
    _authenticate(api_client, user)
    payload = {
        'fieldType': 'relationship-quickLabels',
        'label': '  克制  ',
    }

    created_response = api_client.post(
        LABEL_HISTORY_URL,
        payload,
        format='json',
    )

    assert created_response.status_code == 200
    created = created_response.json()
    assert created['fieldType'] == 'relationship-quickLabels'
    assert created['label'] == '克制'
    obj = LabelHistory.objects.get(user=user)
    assert str(obj.id) == str(created['id'])

    stale_time = timezone.now() - timedelta(days=1)
    LabelHistory.objects.filter(pk=obj.pk).update(used_at=stale_time)

    refreshed_response = api_client.post(
        LABEL_HISTORY_URL,
        payload,
        format='json',
    )

    assert refreshed_response.status_code == 200
    assert str(refreshed_response.json()['id']) == str(obj.id)
    assert LabelHistory.objects.filter(user=user).count() == 1
    obj.refresh_from_db()
    assert obj.used_at > stale_time


@pytest.mark.django_db
def test_label_history_get_is_filtered_by_user_and_field_type(
    api_client, user_factory,
):
    first_user = user_factory()
    second_user = user_factory()
    first_row = LabelHistory.objects.create(
        user=first_user,
        field_type='relationship-quickLabels',
        label='同名标签',
    )
    second_row = LabelHistory.objects.create(
        user=second_user,
        field_type='relationship-quickLabels',
        label='同名标签',
    )
    LabelHistory.objects.create(
        user=first_user,
        field_type='baseCard-quickLabels',
        label='其他字段',
    )

    _authenticate(api_client, first_user)
    response = api_client.get(
        LABEL_HISTORY_URL,
        {'field_type': 'relationship-quickLabels'},
    )

    assert response.status_code == 200
    assert [str(item['id']) for item in response.json()] == [str(first_row.id)]
    assert second_row.id != first_row.id


@pytest.mark.django_db
def test_label_history_get_returns_latest_one_hundred(api_client, user_factory):
    user = user_factory()
    rows = LabelHistory.objects.bulk_create([
        LabelHistory(
            user=user,
            field_type='relationship-quickLabels',
            label=f'label-{index:03d}',
        )
        for index in range(101)
    ])
    base_time = timezone.now() - timedelta(days=1)
    for index, row in enumerate(rows):
        row.used_at = base_time + timedelta(seconds=index)
    LabelHistory.objects.bulk_update(rows, ['used_at'])
    _authenticate(api_client, user)

    response = api_client.get(
        LABEL_HISTORY_URL,
        {'field_type': 'relationship-quickLabels'},
    )

    assert response.status_code == 200
    labels = [item['label'] for item in response.json()]
    assert len(labels) == 100
    assert labels[:3] == ['label-100', 'label-099', 'label-098']
    assert 'label-000' not in labels
